"""
history blueprint — /history 목록·상세·삭제 + /history/all (manager 이상)
+ CSV 내보내기 (§3-9).

라우트:
- GET  /history
- GET  /history/export             내 이력 CSV
- GET  /history/all                manager 이상
- GET  /history/all/export         전체 이력 CSV (manager 이상)
- GET  /history/<id>
- POST /history/<id>/delete
- POST /history/<id>/review
"""

import csv
import io
from datetime import datetime, timedelta
from math import ceil

from flask import (
    Blueprint, Response, abort, flash, redirect, render_template,
    request, stream_with_context, url_for,
)

from ..auth_utils import login_required, role_required
from ..logging_config import get_logger
from ..services import auth as auth_service
from ..services import inspection_store


log = get_logger(__name__)


bp = Blueprint("history", __name__)


DEFAULT_PAGE_SIZE = 20
ALLOWED_PAGE_SIZES = (20, 50, 100)   # §3-9 화이트리스트
VALID_STATUS = ("all", "normal", "alarm", "reviewed")


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def _parse_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_per_page(raw):
    """§3-9: 화이트리스트 외 값은 기본값. 사용자 입력을 그대로 쓰지 않음."""
    try:
        candidate = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    if candidate in ALLOWED_PAGE_SIZES:
        return candidate
    return DEFAULT_PAGE_SIZE


def _read_common_filters():
    date_from_raw = request.args.get("date_from", "").strip()
    date_to_raw = request.args.get("date_to", "").strip()
    status_raw = request.args.get("status", "all").strip()
    page = max(1, _parse_int(request.args.get("page", "1").strip(), 1))
    per_page = _parse_per_page(request.args.get("per_page", "").strip())

    date_from = _parse_date(date_from_raw)
    date_to = _parse_date(date_to_raw)
    date_to_exclusive = None
    if date_to is not None:
        date_to_exclusive = date_to + timedelta(days=1)

    status_filter = status_raw if status_raw in VALID_STATUS else "all"

    return {
        "date_from_raw": date_from_raw,
        "date_to_raw": date_to_raw,
        "status": status_filter,
        "page": page,
        "per_page": per_page,
        "date_from": date_from,
        "date_to_exclusive": date_to_exclusive,
    }


@bp.route("/history")
@login_required
def history_list():
    current_user = auth_service.get_current_user()
    filters = _read_common_filters()
    offset = (filters["page"] - 1) * filters["per_page"]

    try:
        rows, total_count = inspection_store.get_user_inspections(
            user_id=current_user["id"],
            limit=filters["per_page"],
            offset=offset,
            date_from=filters["date_from"],
            date_to=filters["date_to_exclusive"],
            status_filter=filters["status"],
        )
        error_message = None
    except Exception as exc:
        rows, total_count = [], 0
        error_message = "이력 조회 중 오류: {}: {}".format(type(exc).__name__, exc)

    total_pages = max(1, ceil(total_count / filters["per_page"])) if total_count else 1

    return render_template(
        "history/list.html",
        rows=rows,
        total_count=total_count,
        page=filters["page"],
        total_pages=total_pages,
        page_size=filters["per_page"],
        allowed_page_sizes=ALLOWED_PAGE_SIZES,
        show_user_column=False,
        list_scope="mine",
        filters={
            "date_from": filters["date_from_raw"],
            "date_to": filters["date_to_raw"],
            "status": filters["status"],
            "username_filter": "",
        },
        valid_status=VALID_STATUS,
        error_message=error_message,
    )


@bp.route("/history/all")
@role_required("manager", "admin")
def history_list_all():
    filters = _read_common_filters()
    username_filter_raw = request.args.get("username", "").strip()
    offset = (filters["page"] - 1) * filters["per_page"]

    user_id_filter = _resolve_username_to_id(username_filter_raw)

    try:
        rows, total_count = inspection_store.get_all_inspections(
            limit=filters["per_page"],
            offset=offset,
            date_from=filters["date_from"],
            date_to=filters["date_to_exclusive"],
            status_filter=filters["status"],
            user_id_filter=user_id_filter,
        )
        error_message = None
    except Exception as exc:
        rows, total_count = [], 0
        error_message = "이력 조회 중 오류: {}: {}".format(type(exc).__name__, exc)

    total_pages = max(1, ceil(total_count / filters["per_page"])) if total_count else 1

    return render_template(
        "history/list.html",
        rows=rows,
        total_count=total_count,
        page=filters["page"],
        total_pages=total_pages,
        page_size=filters["per_page"],
        allowed_page_sizes=ALLOWED_PAGE_SIZES,
        show_user_column=True,
        list_scope="all",
        filters={
            "date_from": filters["date_from_raw"],
            "date_to": filters["date_to_raw"],
            "status": filters["status"],
            "username_filter": username_filter_raw,
        },
        valid_status=VALID_STATUS,
        error_message=error_message,
    )


def _resolve_username_to_id(username):
    """검사자 username → user_id. 미존재면 -1 (검색 결과 0 유도)."""
    if not username:
        return None
    from ..models import User
    from .. import db as app_db
    try:
        with app_db.get_session() as s:
            target = s.query(User).filter(User.username == username).one_or_none()
            return int(target.id) if target is not None else -1
    except Exception:
        return -1


# ============================================================
# CSV 내보내기 (§3-9)
# ============================================================

CSV_HEADER = [
    "검사ID", "일시", "검사자", "파일명", "상태",
    "A3비율", "A3이상셀", "세그크랙", "세그박리", "핀홀수",
    "콜드스타트",
    "처리시간_A3", "처리시간_YOLO", "처리시간_세그", "처리시간_합계",
    "조치자", "조치일시",
]


def _row_to_csv_values(row):
    def fmt_dt(value):
        if value is None:
            return ""
        return value.strftime("%Y-%m-%d %H:%M:%S")

    def fmt_num(value, places=None):
        if value is None:
            return ""
        if places is not None:
            return "{:.{}f}".format(value, places)
        return str(value)

    return [
        str(row["inspection_id"]),
        fmt_dt(row["created_at"]),
        row["username"] or "",
        row["file_name"] or "",
        row["status"] or "",
        fmt_num(row["a3_ratio"], 6),
        fmt_num(row["a3_cells"]),
        fmt_num(row["seg_crack"], 6),
        fmt_num(row["seg_delam"], 6),
        fmt_num(row["pinhole_count"]),
        fmt_num(row["is_cold_start"]),
        fmt_num(row["ms_a3"], 3),
        fmt_num(row["ms_yolo"], 3),
        fmt_num(row["ms_seg"], 3),
        fmt_num(row["ms_total"], 3),
        row["reviewer_username"] or "",
        fmt_dt(row["reviewed_at"]),
    ]


def _stream_csv(row_iter):
    """generator: UTF-8 BOM + header + 각 행 CSV line."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    # UTF-8 BOM (Excel 한글 호환)
    yield "﻿"

    writer.writerow(CSV_HEADER)
    yield buffer.getvalue()
    buffer.seek(0); buffer.truncate(0)

    for row in row_iter:
        writer.writerow(_row_to_csv_values(row))
        yield buffer.getvalue()
        buffer.seek(0); buffer.truncate(0)


def _make_export_response(row_iter, total_count, truncated, filename):
    """스트리밍 Response + 헤더."""
    headers = {
        "Content-Disposition": 'attachment; filename="{}"'.format(filename),
        "X-Row-Count-Total": str(total_count),
        "X-Row-Limit": str(inspection_store.EXPORT_MAX_ROWS),
        "X-Row-Truncated": "true" if truncated else "false",
    }
    return Response(
        stream_with_context(_stream_csv(row_iter)),
        mimetype="text/csv; charset=utf-8",
        headers=headers,
    )


def _timestamp_filename():
    return "inspections_{}.csv".format(datetime.utcnow().strftime("%Y%m%d_%H%M%S"))


@bp.route("/history/export")
@login_required
def history_export():
    current_user = auth_service.get_current_user()
    filters = _read_common_filters()

    total = inspection_store.count_export_rows(
        user_id=current_user["id"], all_users=False,
        date_from=filters["date_from"],
        date_to=filters["date_to_exclusive"],
        status_filter=filters["status"],
    )
    truncated = total > inspection_store.EXPORT_MAX_ROWS

    row_iter = inspection_store.iter_inspections_for_export(
        user_id=current_user["id"], all_users=False,
        date_from=filters["date_from"],
        date_to=filters["date_to_exclusive"],
        status_filter=filters["status"],
    )

    log.info(
        "history_export user_id=%s total=%d truncated=%s",
        current_user["id"], total, truncated,
    )
    return _make_export_response(row_iter, total, truncated, _timestamp_filename())


@bp.route("/history/all/export")
@role_required("manager", "admin")
def history_export_all():
    current_user = auth_service.get_current_user()
    filters = _read_common_filters()
    username_filter_raw = request.args.get("username", "").strip()
    user_id_filter = _resolve_username_to_id(username_filter_raw)

    total = inspection_store.count_export_rows(
        all_users=True,
        date_from=filters["date_from"],
        date_to=filters["date_to_exclusive"],
        status_filter=filters["status"],
        user_id_filter=user_id_filter,
    )
    truncated = total > inspection_store.EXPORT_MAX_ROWS

    row_iter = inspection_store.iter_inspections_for_export(
        all_users=True,
        date_from=filters["date_from"],
        date_to=filters["date_to_exclusive"],
        status_filter=filters["status"],
        user_id_filter=user_id_filter,
    )

    log.info(
        "history_export_all admin=%s total=%d truncated=%s username_filter=%s",
        current_user["id"], total, truncated, username_filter_raw or "",
    )
    return _make_export_response(row_iter, total, truncated, _timestamp_filename())


# ============================================================
# 상세 / 삭제 / 조치 완료
# ============================================================

@bp.route("/history/<int:inspection_id>")
@login_required
def history_detail(inspection_id):
    current_user = auth_service.get_current_user()
    try:
        detail = inspection_store.get_inspection_detail(
            inspection_id=inspection_id,
            viewer_user_id=current_user["id"],
            viewer_role=current_user["role"],
        )
    except Exception as exc:
        flash("상세 조회 중 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return redirect(url_for("history.history_list"))

    if detail is None:
        abort(403)

    return render_template(
        "history/detail.html",
        detail=detail,
        current_user=current_user,
    )


@bp.route("/history/<int:inspection_id>/review", methods=["POST"])
@login_required
def history_review(inspection_id):
    current_user = auth_service.get_current_user()
    try:
        ok, reason = inspection_store.mark_reviewed(
            inspection_id=inspection_id,
            viewer_user_id=current_user["id"],
            viewer_role=current_user["role"],
        )
    except Exception as exc:
        flash("조치 처리 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return redirect(url_for("history.history_detail", inspection_id=inspection_id))

    if not ok:
        flash(reason or "조치 처리 실패", "error")
    else:
        flash("검사 #{} 를 'reviewed' 상태로 전이했습니다.".format(inspection_id), "success")
    return redirect(url_for("history.history_detail", inspection_id=inspection_id))


@bp.route("/history/<int:inspection_id>/delete", methods=["POST"])
@login_required
def history_delete(inspection_id):
    current_user = auth_service.get_current_user()
    try:
        ok = inspection_store.delete_inspection(
            inspection_id=inspection_id,
            viewer_user_id=current_user["id"],
            viewer_role=current_user["role"],
        )
    except Exception as exc:
        flash("삭제 중 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return redirect(url_for("history.history_detail", inspection_id=inspection_id))

    if not ok:
        abort(403)
    flash("검사 #{} 삭제 완료.".format(inspection_id), "success")
    return redirect(url_for("history.history_list"))
