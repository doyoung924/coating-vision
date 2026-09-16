"""
검사 결과 저장/조회/삭제 서비스 (§3-3, §3-4 확장).

§3-4 변경사항:
- IS_COLD_START 컬럼 저장. 통계 집계 시 콜드 스타트 행은 제외.
- HEATMAP_PATH · ANNOTATED_PATH · SEG_PATH 저장 (basename 만, uploads/ 아래).
- IMAGE_PATH 도 basename 통일.
- DETECTIONS.ROUNDNESS 를 shape stage 결과에서 매핑.
- 저장 성공 후 SPC 두 metric (a3, seg_crack) 에 add_spc_point 호출.
  알람이 하나라도 발생하면 INSPECTIONS.STATUS='alarm' 로 갱신.
- SPC 처리는 별도 트랜잭션 (실패해도 검사 저장은 유지, §3-3 원칙).

원칙:
- 하나의 트랜잭션. 실패 시 전체 롤백.
- DB 실패는 예외로 전파해 라우트에서 flash 처리 (계산 결과 렌더링은 유지).
- CONTENT (Findings) 4000자 초과는 잘라내고 print 로 알림.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func

from .. import db as app_db
from ..logging_config import get_logger
from ..models import Detection, Finding, Inspection, User
from . import cleanup as cleanup_service
from . import spc as spc_service


log = get_logger(__name__)


DEFAULT_LIMIT = 20
FINDING_CONTENT_MAX = 4000


def save_inspection(result, user_id, file_name, source, is_cold_start=False):
    """result: pipeline.run_inspection 반환 dict. INSPECTIONS·DETECTIONS·FINDINGS
    를 하나의 트랜잭션으로 저장. 반환값은 새 inspection_id (int).

    이후 SPC (별도 트랜잭션) 도 처리. SPC 실패는 stdout 로그만.
    실패 시 예외 전파 (라우트에서 잡아 flash)."""
    anomaly = result.get("anomaly") or {}
    detect = result.get("detect") or {}
    seg = result.get("seg") or {}
    shape = result.get("shape") or {}
    advisor = result.get("advisor") or {}
    file_names = result.get("file_names") or {}

    ms_a3 = float(anomaly.get("elapsed_ms_per_patch") or 0.0)
    ms_yolo = float(detect.get("total_ms") or 0.0)
    ms_seg = float(seg.get("elapsed_ms") or 0.0) if seg.get("available") else 0.0
    ms_shape = float(shape.get("elapsed_ms") or 0.0)
    ms_total = ms_a3 + ms_yolo + ms_seg + ms_shape

    roundness_by_index = _roundness_map(shape.get("per_detection") or [])

    with app_db.get_session() as sqlalchemy_session:
        inspection = Inspection(
            user_id=user_id,
            file_name=file_name,
            image_path=file_names.get("orig"),
            heatmap_path=file_names.get("heatmap"),
            annotated_path=file_names.get("annotated"),
            seg_path=file_names.get("seg"),
            source=source,
            a3_ratio=_round_decimal(anomaly.get("ratio"), 6),
            a3_cells=_int_or_none(anomaly.get("anomalous_cells")),
            seg_crack=_round_decimal(seg.get("crack_ratio"), 6) if seg.get("available") else None,
            seg_delam=_round_decimal(seg.get("delam_ratio"), 6) if seg.get("available") else None,
            pinhole_count=_int_or_none(detect.get("n_detections")),
            ms_a3=_round_decimal(ms_a3, 3),
            ms_yolo=_round_decimal(ms_yolo, 3),
            ms_seg=_round_decimal(ms_seg, 3),
            ms_total=_round_decimal(ms_total, 3),
            is_cold_start=1 if is_cold_start else 0,
            status="normal",
        )
        sqlalchemy_session.add(inspection)
        sqlalchemy_session.flush()

        for detection_row in detect.get("detections") or []:
            index = detection_row.get("index")
            sqlalchemy_session.add(Detection(
                inspection_id=inspection.id,
                x1=_round_decimal(detection_row.get("x1"), 2),
                y1=_round_decimal(detection_row.get("y1"), 2),
                x2=_round_decimal(detection_row.get("x2"), 2),
                y2=_round_decimal(detection_row.get("y2"), 2),
                conf=_round_decimal(detection_row.get("conf"), 4),
                aspect=_round_decimal(detection_row.get("aspect"), 3),
                roundness=_round_decimal(roundness_by_index.get(index), 4),
            ))

        for finding_row in _flatten_advisor(advisor):
            sqlalchemy_session.add(Finding(
                inspection_id=inspection.id,
                layer=finding_row["layer"],
                content=_truncate_content(finding_row["content"], inspection.id, finding_row["layer"]),
                confidence=finding_row.get("confidence"),
                source=finding_row.get("source"),
            ))

        new_id = int(inspection.id)

    # 별도 트랜잭션에서 SPC. 실패해도 검사 저장은 유지 (§3-3 원칙).
    _process_spc_and_status(new_id, anomaly, seg)

    return new_id


def _process_spc_and_status(inspection_id, anomaly, seg):
    """두 metric 에 add_spc_point 호출 후 알람 발생 시 STATUS='alarm' 로 갱신."""
    alarm_occurred = False

    a3_value = anomaly.get("ratio")
    if a3_value is not None:
        try:
            report = spc_service.add_spc_point(inspection_id, "a3", float(a3_value))
            if report.get("is_alarm"):
                alarm_occurred = True
        except Exception as exc:
            log.error("SPC add(a3) 실패 inspection_id=%d: %s: %s",
                      inspection_id, type(exc).__name__, exc)

    if seg.get("available"):
        crack_value = seg.get("crack_ratio")
        if crack_value is not None:
            try:
                report = spc_service.add_spc_point(inspection_id, "seg_crack", float(crack_value))
                if report.get("is_alarm"):
                    alarm_occurred = True
            except Exception as exc:
                log.error("SPC add(seg_crack) 실패 inspection_id=%d: %s: %s",
                          inspection_id, type(exc).__name__, exc)

    if alarm_occurred:
        try:
            with app_db.get_session() as sqlalchemy_session:
                sqlalchemy_session.query(Inspection).filter(
                    Inspection.id == inspection_id,
                ).update({"status": "alarm"})
        except Exception as exc:
            log.error("STATUS 갱신 실패 inspection_id=%d: %s: %s",
                      inspection_id, type(exc).__name__, exc)


def _roundness_map(per_detection_list):
    """shape.per_detection 을 index → roundness 매핑."""
    result = {}
    for entry in per_detection_list:
        idx = entry.get("index")
        roundness = entry.get("roundness")
        if idx is not None:
            result[idx] = roundness
    return result


def get_user_inspections(user_id, limit=DEFAULT_LIMIT, offset=0,
                        date_from=None, date_to=None, status_filter=None):
    """user_id 소유 검사 목록. 최신 CREATED_AT 순."""
    with app_db.get_session() as sqlalchemy_session:
        query = sqlalchemy_session.query(Inspection).filter(Inspection.user_id == user_id)
        query = _apply_common_filters(query, date_from, date_to, status_filter)
        query = query.order_by(Inspection.created_at.desc(), Inspection.id.desc())
        rows = query.offset(offset).limit(limit).all()

        total_query = sqlalchemy_session.query(func.count(Inspection.id)).filter(Inspection.user_id == user_id)
        total_query = _apply_common_filters(total_query, date_from, date_to, status_filter)
        total_count = int(total_query.scalar() or 0)

        result = []
        for row in rows:
            result.append(_inspection_summary_dict(row))
    return result, total_count


def get_all_inspections(limit=DEFAULT_LIMIT, offset=0,
                       date_from=None, date_to=None, status_filter=None, user_id_filter=None):
    """§3-4 manager 이상 전용. 전체 사용자의 검사 이력.
    username 포함해서 반환."""
    with app_db.get_session() as sqlalchemy_session:
        query = (
            sqlalchemy_session.query(Inspection, User)
            .outerjoin(User, Inspection.user_id == User.id)
        )
        query = _apply_common_filters(query, date_from, date_to, status_filter)
        if user_id_filter is not None:
            query = query.filter(Inspection.user_id == user_id_filter)
        query = query.order_by(Inspection.created_at.desc(), Inspection.id.desc())
        rows = query.offset(offset).limit(limit).all()

        total_query = sqlalchemy_session.query(func.count(Inspection.id))
        total_query = _apply_common_filters(total_query, date_from, date_to, status_filter)
        if user_id_filter is not None:
            total_query = total_query.filter(Inspection.user_id == user_id_filter)
        total_count = int(total_query.scalar() or 0)

        result = []
        for inspection_row, user_row in rows:
            entry = _inspection_summary_dict(inspection_row)
            entry["username"] = user_row.username if user_row is not None else None
            result.append(entry)
    return result, total_count


def get_inspection_detail(inspection_id, viewer_user_id, viewer_role):
    """상세. 소유자 또는 manager 이상만 볼 수 있다."""
    with app_db.get_session() as sqlalchemy_session:
        inspection = sqlalchemy_session.get(Inspection, inspection_id)
        if inspection is None:
            return None
        if not _can_view(inspection, viewer_user_id, viewer_role):
            return None

        owner = sqlalchemy_session.get(User, int(inspection.user_id)) if inspection.user_id else None
        owner_username = owner.username if owner is not None else None

        detections = sqlalchemy_session.query(Detection).filter(
            Detection.inspection_id == inspection.id,
        ).order_by(Detection.id.asc()).all()

        findings = sqlalchemy_session.query(Finding).filter(
            Finding.inspection_id == inspection.id,
        ).order_by(Finding.id.asc()).all()

        reviewer_username = None
        if inspection.reviewed_by is not None:
            reviewer = sqlalchemy_session.get(User, int(inspection.reviewed_by))
            reviewer_username = reviewer.username if reviewer is not None else None

        detail = _inspection_summary_dict(inspection)
        detail["owner_username"] = owner_username
        detail["reviewer_username"] = reviewer_username
        detail["detections"] = [_detection_dict(det) for det in detections]
        detail["findings_by_layer"] = _group_findings(findings)
    return detail


def delete_inspection(inspection_id, viewer_user_id, viewer_role):
    """소유자 또는 admin 만 삭제. 성공 시 True.
    §3-8: DB 삭제와 파일 삭제 (uploads/*.png 4장) 를 함께 수행.
    - 파일이 없어도 에러 없이 진행.
    - DB / 파일 중 하나가 실패해도 나머지는 수행. 실패는 로그.
    """
    # 파일 삭제 대상 basename 캡처. DB 삭제 트랜잭션 안에서 캡처하지 않으면 detached.
    file_names = None
    with app_db.get_session() as sqlalchemy_session:
        inspection = sqlalchemy_session.get(Inspection, inspection_id)
        if inspection is None:
            return False
        is_owner = int(inspection.user_id) == int(viewer_user_id) if inspection.user_id else False
        is_admin = viewer_role == "admin"
        if not (is_owner or is_admin):
            return False

        file_names = {
            "image_path": inspection.image_path,
            "heatmap_path": inspection.heatmap_path,
            "annotated_path": inspection.annotated_path,
            "seg_path": inspection.seg_path,
        }

        try:
            sqlalchemy_session.delete(inspection)
        except Exception as exc:
            log.error("DB delete failed inspection_id=%d: %s", inspection_id, exc)
            raise

    # DB 삭제 성공 후 파일 삭제 시도. 실패해도 예외 전파 안 함 (호출자에 True 반환).
    if file_names is not None:
        try:
            result = cleanup_service.delete_inspection_files(**file_names)
            log.info(
                "delete_inspection id=%d files_removed=%d bytes=%d",
                inspection_id, result["removed"], result["bytes"],
            )
        except Exception as exc:
            log.error("File delete failed for inspection_id=%d: %s", inspection_id, exc)
    return True


def mark_reviewed(inspection_id, viewer_user_id, viewer_role):
    """§3-5: STATUS='alarm' 검사를 'reviewed' 로 전이.
    - 권한: manager 이상
    - 조건: 해당 검사에 연결된 report 카테고리 글이 최소 1건 있어야 함
    반환: (ok, reason). ok=False 이면 reason 은 사유 문자열.
    """
    if viewer_role not in ("manager", "admin"):
        return False, "매니저 이상만 조치 완료 처리를 할 수 있습니다."

    # report 확인은 별도 트랜잭션에서 수행 (services.board 를 부르면 순환)
    from . import board as board_service
    if not board_service.has_report_for_inspection(inspection_id):
        return False, ("이 검사에 연결된 리포트 (category=report) 가 없습니다. "
                       "먼저 리포트를 작성한 뒤 다시 시도하세요.")

    with app_db.get_session() as sqlalchemy_session:
        inspection = sqlalchemy_session.get(Inspection, inspection_id)
        if inspection is None:
            return False, "검사가 존재하지 않습니다."
        if inspection.status != "alarm":
            return False, "STATUS='alarm' 인 검사만 조치 완료 처리할 수 있습니다. (현재: {})".format(inspection.status)

        inspection.status = "reviewed"
        inspection.reviewed_by = int(viewer_user_id)
        inspection.reviewed_at = datetime.utcnow()
        sqlalchemy_session.flush()
    return True, None


EXPORT_MAX_ROWS = 10000
EXPORT_COLUMNS = [
    "inspection_id", "created_at", "username", "file_name", "status",
    "a3_ratio", "a3_cells", "seg_crack", "seg_delam", "pinhole_count",
    "is_cold_start", "ms_a3", "ms_yolo", "ms_seg", "ms_total",
    "reviewer_username", "reviewed_at",
]


def iter_inspections_for_export(
    user_id=None, all_users=False,
    date_from=None, date_to=None, status_filter=None, user_id_filter=None,
    hard_limit=EXPORT_MAX_ROWS,
):
    """CSV export 용 generator. 검사 행 하나씩 dict 로 yield.
    - all_users=False: user_id 소유만
    - all_users=True: 전체 (검사자 username 조인)
    - hard_limit 초과 시 truncated=True 를 마지막 yield 튜플로 반환할 수 없어,
      호출자가 별도 카운터로 판정 (여기서는 그냥 hard_limit 까지만 yield).

    반환 형태:
        yield {"inspection_id": int, "created_at": datetime|None, "username": str|None,
               "file_name": str|None, "status": str, ...}
    """
    from ..models import Inspection, User

    with app_db.get_session() as sqlalchemy_session:
        query = (
            sqlalchemy_session.query(Inspection, User)
            .outerjoin(User, Inspection.user_id == User.id)
        )
        if not all_users:
            query = query.filter(Inspection.user_id == user_id)
        query = _apply_common_filters(query, date_from, date_to, status_filter)
        if user_id_filter is not None:
            query = query.filter(Inspection.user_id == user_id_filter)
        query = query.order_by(Inspection.created_at.desc(), Inspection.id.desc())

        emitted = 0
        # yield_per: SQLAlchemy 가 서버 쿼리를 청크 단위로 fetch. 전체 메모리 로드 방지.
        for inspection, user in query.yield_per(200):
            if emitted >= hard_limit:
                break
            reviewer_name = None
            if inspection.reviewed_by is not None:
                reviewer = sqlalchemy_session.get(User, int(inspection.reviewed_by))
                reviewer_name = reviewer.username if reviewer is not None else None
            yield {
                "inspection_id": int(inspection.id),
                "created_at": inspection.created_at,
                "username": user.username if user is not None else None,
                "file_name": inspection.file_name,
                "status": inspection.status,
                "a3_ratio": _to_float(inspection.a3_ratio),
                "a3_cells": _to_int(inspection.a3_cells),
                "seg_crack": _to_float(inspection.seg_crack),
                "seg_delam": _to_float(inspection.seg_delam),
                "pinhole_count": _to_int(inspection.pinhole_count),
                "is_cold_start": _to_int(inspection.is_cold_start),
                "ms_a3": _to_float(inspection.ms_a3),
                "ms_yolo": _to_float(inspection.ms_yolo),
                "ms_seg": _to_float(inspection.ms_seg),
                "ms_total": _to_float(inspection.ms_total),
                "reviewer_username": reviewer_name,
                "reviewed_at": inspection.reviewed_at,
            }
            emitted += 1


def count_export_rows(user_id=None, all_users=False,
                     date_from=None, date_to=None, status_filter=None, user_id_filter=None):
    """CSV export 전 대상 행 수 파악 (truncation 판정용)."""
    from ..models import Inspection

    with app_db.get_session() as sqlalchemy_session:
        query = sqlalchemy_session.query(func.count(Inspection.id))
        if not all_users:
            query = query.filter(Inspection.user_id == user_id)
        query = _apply_common_filters(query, date_from, date_to, status_filter)
        if user_id_filter is not None:
            query = query.filter(Inspection.user_id == user_id_filter)
        return int(query.scalar() or 0)


def get_dashboard_summary(user_id):
    """대시보드용 요약. §3-4: IS_COLD_START=1 행을 통계에서 제외."""
    now = datetime.utcnow()
    seven_days_ago = now - timedelta(days=7)

    with app_db.get_session() as sqlalchemy_session:
        base_query = sqlalchemy_session.query(Inspection).filter(Inspection.user_id == user_id)
        stat_query = base_query.filter(Inspection.is_cold_start == 0)

        total_count = int(base_query.with_entities(func.count(Inspection.id)).scalar() or 0)

        recent_count = int(
            base_query.with_entities(func.count(Inspection.id))
            .filter(Inspection.created_at >= seven_days_ago)
            .scalar() or 0
        )

        # 평균 crack 은 콜드 스타트 제외한 warm 요청만.
        avg_crack_raw = stat_query.with_entities(func.avg(Inspection.seg_crack)).scalar()
        avg_crack = float(avg_crack_raw) if avg_crack_raw is not None else None

        # 최근 5건은 콜드 여부 상관없이 (사용자에게 최신 이력 보여주기 목적)
        recent_rows = (
            base_query.order_by(Inspection.created_at.desc(), Inspection.id.desc())
            .limit(5).all()
        )
        recent_list = [_inspection_summary_dict(row) for row in recent_rows]

    return {
        "total_count": total_count,
        "recent_count": recent_count,
        "avg_crack": avg_crack,
        "avg_crack_note": "IS_COLD_START=0 만 집계",
        "recent_list": recent_list,
    }


# ============================================================
# 내부 헬퍼
# ============================================================

def _apply_common_filters(query, date_from, date_to, status_filter):
    if date_from is not None:
        query = query.filter(Inspection.created_at >= date_from)
    if date_to is not None:
        query = query.filter(Inspection.created_at < date_to)
    if status_filter and status_filter != "all":
        query = query.filter(Inspection.status == status_filter)
    return query


def _can_view(inspection, viewer_user_id, viewer_role):
    if viewer_role in ("manager", "admin"):
        return True
    if inspection.user_id is None:
        return False
    return int(inspection.user_id) == int(viewer_user_id)


def _inspection_summary_dict(row):
    return {
        "id": int(row.id),
        "user_id": int(row.user_id) if row.user_id is not None else None,
        "file_name": row.file_name,
        "image_path": row.image_path,
        "heatmap_path": row.heatmap_path,
        "annotated_path": row.annotated_path,
        "seg_path": row.seg_path,
        "source": row.source,
        "created_at": row.created_at,
        "a3_ratio": _to_float(row.a3_ratio),
        "a3_cells": _to_int(row.a3_cells),
        "seg_crack": _to_float(row.seg_crack),
        "seg_delam": _to_float(row.seg_delam),
        "pinhole_count": _to_int(row.pinhole_count),
        "ms_a3": _to_float(row.ms_a3),
        "ms_yolo": _to_float(row.ms_yolo),
        "ms_seg": _to_float(row.ms_seg),
        "ms_total": _to_float(row.ms_total),
        "is_cold_start": _to_int(row.is_cold_start),
        "status": row.status,
        "reviewed_by": _to_int(row.reviewed_by),
        "reviewed_at": row.reviewed_at,
    }


def _detection_dict(row):
    return {
        "id": int(row.id),
        "x1": _to_float(row.x1),
        "y1": _to_float(row.y1),
        "x2": _to_float(row.x2),
        "y2": _to_float(row.y2),
        "conf": _to_float(row.conf),
        "aspect": _to_float(row.aspect),
        "roundness": _to_float(row.roundness),
    }


def _group_findings(findings):
    result = {"observation": [], "interpretation": [], "reference": []}
    for row in findings:
        entry = {
            "id": int(row.id),
            "content": row.content,
            "confidence": row.confidence,
            "source": row.source,
        }
        layer = row.layer
        if layer in result:
            result[layer].append(entry)
    return result


def _flatten_advisor(advisor):
    output = []
    observation = advisor.get("observation") or {}
    for key, value in observation.items():
        output.append({
            "layer": "observation",
            "content": "{}: {}".format(key, value),
            "confidence": None,
            "source": None,
        })
    for item in advisor.get("interpretations") or []:
        output.append({
            "layer": "interpretation",
            "content": str(item.get("message", "")),
            "confidence": item.get("confidence"),
            "source": None,
        })
    for ref in advisor.get("references") or []:
        topic = ref.get("topic") or ""
        lines = ref.get("lines") or []
        output.append({
            "layer": "reference",
            "content": "\n".join(str(line) for line in lines),
            "confidence": None,
            "source": topic[:100],
        })
    return output


def _truncate_content(content, inspection_id, layer):
    if content is None:
        return None
    if len(content) <= FINDING_CONTENT_MAX:
        return content
    log.warning(
        "FINDINGS.CONTENT truncated inspection_id=%s layer=%s original_len=%d",
        inspection_id, layer, len(content),
    )
    return content[:FINDING_CONTENT_MAX]


def _round_decimal(value, places):
    if value is None:
        return None
    try:
        return Decimal(str(round(float(value), places)))
    except (TypeError, ValueError):
        return None


def _int_or_none(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value):
    if value is None:
        return None
    return float(value)


def _to_int(value):
    if value is None:
        return None
    return int(value)
