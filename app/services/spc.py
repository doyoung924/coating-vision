"""
SPC 누적 관리도 (§3단계 3-4).

정책:
- 전체 공통. 사용자별 분리하지 않음.
- 저장될 때마다 metric 별로 SPC_POINTS 에 점 하나 추가.
- baseline: 최초 BASELINE_SIZE (30) 개는 수집만. CENTER/UCL/EWMA 는 NULL.
  IS_ALARM=0.
- baseline 이후: baseline 30 점으로 관리한계 산출. metric 종류에 따라 분기.
    · 연속형 (continuous): UCL = CENTER + SIGMA_LIMIT (3.5) * sigma.
      EWMA 재귀 (초기값 CENTER). value 또는 EWMA 가 UCL 초과 시 IS_ALARM=1.
    · 계수형 (count): UCL = CENTER + 3·√CENTER (Poisson c 관리도, FR-57).
      EWMA 미적용 (설계 §1 (다)). value > UCL 만으로 IS_ALARM=1.

계산식 근거:
- compute_control_limits (연속형): 09_spc_monitor.py:204-223 그대로.
- 계수형 산출식: c̄ + 3√c̄ (Shewhart c 관리도. Poisson Var = mean 특성).
- EWMA 재귀: 09_spc_monitor.py:240-258 (lambda * value + (1-lambda) * prev).
- 계수 (3.5σ, λ 연속형만, baseline 30) 는 사용자 지시 (§10_spc_tuning 권장값).
  09 원본 계수 (3.0σ, λ=0.2) 는 사용하지 않는다.

import 방향: services/spc → models → db → config.
"""

import math

import numpy as np
from sqlalchemy import func

from .. import db as app_db
from ..models import Inspection, SpcPoint, User


BASELINE_SIZE = 30
# SIGMA_LIMIT: 3.5. 원 채택 근거 (§9-3 lift 16.51) 는 §15-5·§22-3-1 에서
# 폐기됨. §23-3 재해석 (프레임 대칭 c_frame_max L × λ 격자, N=367) 에서
# 세그 관점 최적 L=3.5 확인 · A3 관점 평탄 구간 내 (§22-4 재해석 (§23-2)).
# 사후 정량 근거 유효. 현행 유지.
SIGMA_LIMIT = 3.5

# ============================================================
# metric 정의: (kind, lambda) 매핑.
# ============================================================
# 새 metric 추가 시 정보가 누락될 경로가 차단되도록, metric 이름 · 종류 · λ 를
# 이 한 곳에서만 정의. VALID_METRICS 는 keys 에서 파생. FR-31 (§23-5), FR-57.
#
# kind:
#   "continuous" — a3 · seg_crack. σ 기반 UCL, EWMA 적용
#   "count"      — pinhole_count 등 포아송 정수형. √c̄ 기반 UCL, EWMA 미적용
#
# lambda:
#   연속형 EWMA 계수 (FR-31 §23-5). 계수형은 None (§보완4 설계 §1 (다),
#   Poisson EWMA 산출식이 저장소 밖 지식이라 인용 부재).
#
# 재검증 조건: 다른 데이터셋 (배터리 라인 · 다른 조성) 에서 세그 λ 최적이
# 0.2 밖으로 이동 시 재판단 (§23-5). 계수형 EWMA 는 실배포 후 알람 이력
# 확보 시 재검토 (§보완4 설계 §1-3).
#
# ┌── 새 SPC metric 추가 시 반드시 두 곳을 모두 수정 ──────────────────────┐
# │  1. 아래 METRICS 에 (metric → {kind, lambda}) 등록                     │
# │  2. sql/schema.sql SPC_POINTS 의 CHK_SPC_METRIC CHECK 목록에 새 값 추가│
# │     + 기존 DB 는 sql/migrate_3_X.py 로 CHECK 재정의 (DROP/ADD)         │
# │  한쪽만 고치면 앱 검증은 통과하고 DB INSERT 가 ORA-02290 로 거부한다. │
# │  배포 순서: 스키마 마이그레이션 먼저 → 앱 배포 나중.                  │
# │  상세: docs/schema.md §SPC_POINTS "새 SPC metric 추가 시 필수 절차"    │
# └────────────────────────────────────────────────────────────────────────┘
METRICS = {
    "a3":            {"kind": "continuous", "lambda": 0.1},
    "seg_crack":     {"kind": "continuous", "lambda": 0.2},
    "pinhole_count": {"kind": "count",      "lambda": None},
}

# VALID_METRICS 는 METRICS.keys() 에서 파생 — 매핑 하나만 관리 대상.
# 새 metric 은 METRICS 에 (kind, λ) 를 함께 등록해야만 VALID_METRICS 에 포함됨.
# add_spc_point 등에서 VALID_METRICS 검사로 등록 안 된 metric 은 조기 실패,
# METRICS[metric] 직접 접근 (dict.get 아님) 이라 fallback 기본값 사용도 없음.
VALID_METRICS = tuple(METRICS.keys())

# EWMA_LAMBDAS: METRICS 에서 파생한 (metric → λ) 뷰. FR-31 회귀 방지·기존 호출부
# 호환용. 계수형 metric 은 λ=None 그대로 노출된다. 이 매핑을 직접 수정하지 말고
# 위 METRICS 를 수정할 것.
EWMA_LAMBDAS = {metric: spec["lambda"] for metric, spec in METRICS.items()}


# ============================================================
# 09_spc_monitor.py 계산식 그대로 이식
# ============================================================

def compute_control_limits(baseline_values, sigma_limit):
    """09_spc_monitor.py:204-223 원문. 연속형 지표 전용. sigma_limit 만 파라미터화."""
    array = np.array(baseline_values, dtype=float)
    center = float(np.mean(array))
    deviation = float(np.std(array))
    upper = center + sigma_limit * deviation
    lower = center - sigma_limit * deviation
    if lower < 0.0:
        lower = 0.0
    return {"center": center, "sigma": deviation, "upper": upper, "lower": lower}


def _compute_count_limits(baseline_values):
    """계수형 (Poisson c 관리도) 관리한계. UCL = c̄ + 3·√c̄, LCL = 0.

    Poisson 분산 특성 (Var = mean) 으로 σ 를 별도 추정하지 않고 √c̄ 로 대체.
    L=3 은 Shewhart c 관리도 관례. FR-57 (I). LCL 은 알람 판정에 미사용
    (FR-57 (IV) — 검출기 침묵 감시는 별도 과제, §보완4 설계 §2).

    반환 sigma 는 None (계수형에는 표본 σ 개념이 다름 · UI 표시용 미사용).
    """
    array = np.array(baseline_values, dtype=float)
    center = float(np.mean(array))
    upper = center + 3.0 * math.sqrt(center)
    return {"center": center, "sigma": None, "upper": upper, "lower": 0.0}


def _compute_limits(baseline_values, metric):
    """metric 종류에 따라 관리한계 산출 분기.

    연속형은 기존 compute_control_limits 를 그대로 호출해 반환값·계수 (SIGMA_LIMIT
    3.5) 가 회귀 없이 유지된다. 계수형은 별도 산출식.
    """
    kind = METRICS[metric]["kind"]
    if kind == "continuous":
        return compute_control_limits(baseline_values, SIGMA_LIMIT)
    if kind == "count":
        return _compute_count_limits(baseline_values)
    raise ValueError("unknown metric kind: {}".format(kind))


def _next_ewma(current_value, lambda_value, previous_ewma):
    """09_spc_monitor.py:254 단일 스텝. 재귀식 그대로."""
    return lambda_value * float(current_value) + (1.0 - lambda_value) * float(previous_ewma)


# ============================================================
# 공개 API
# ============================================================

def add_spc_point(inspection_id, metric, value):
    """단일 점을 SPC_POINTS 에 삽입. 반환: dict with is_alarm, phase, center, ucl, ewma."""
    if metric not in VALID_METRICS:
        raise ValueError("invalid metric: {} (valid: {})".format(metric, VALID_METRICS))
    if value is None:
        raise ValueError("value is None")

    value = float(value)
    lambda_value = METRICS[metric]["lambda"]
    use_ewma = lambda_value is not None

    with app_db.get_session() as sqlalchemy_session:
        # 다음 SEQ_NO (연속 번호 부여 목적 — baseline 판정과 무관, FR-56)
        max_seq_scalar = sqlalchemy_session.query(func.max(SpcPoint.seq_no)).filter(
            SpcPoint.metric == metric,
        ).scalar()
        max_seq = int(max_seq_scalar or 0)
        seq_no = max_seq + 1

        # baseline 판정은 실제 저장된 점 수 기준 (FR-56).
        # get_summary 와 동일한 기준을 쓰기 위해 COUNT 사용.
        # SEQ_NO 를 쓰면 CASCADE 삭제로 gap 이 생겼을 때 실제 점 수보다 큰 값이 되어
        # 미완성 상태를 완성으로 오판정할 수 있다.
        count_scalar = sqlalchemy_session.query(func.count(SpcPoint.id)).filter(
            SpcPoint.metric == metric,
        ).scalar()
        existing_count = int(count_scalar or 0)

        if existing_count < BASELINE_SIZE:
            # baseline 수집 단계. 관리한계·EWMA 는 아직 없다.
            row = SpcPoint(
                inspection_id=inspection_id,
                metric=metric,
                seq_no=seq_no,
                value=value,
                ewma=None,
                center=None,
                ucl=None,
                is_alarm=0,
            )
            sqlalchemy_session.add(row)
            sqlalchemy_session.flush()
            return {
                "seq_no": seq_no,
                "phase": "baseline",
                "is_alarm": False,
                "center": None,
                "ucl": None,
                "ewma": None,
            }

        # baseline 이후 (seq_no >= BASELINE_SIZE+1). 관리한계 산출 (metric 종류 분기).
        baseline_rows = sqlalchemy_session.query(SpcPoint.value).filter(
            SpcPoint.metric == metric,
            SpcPoint.seq_no <= BASELINE_SIZE,
        ).order_by(SpcPoint.seq_no).all()
        baseline_values = [float(r.value) for r in baseline_rows]
        limits = _compute_limits(baseline_values, metric)
        center = limits["center"]
        ucl = limits["upper"]

        if use_ewma:
            # 연속형: EWMA 재귀. seq_no == BASELINE_SIZE+1 이면 CENTER 로 초기화.
            if seq_no == BASELINE_SIZE + 1:
                previous_ewma = center
            else:
                previous_row = sqlalchemy_session.query(SpcPoint).filter(
                    SpcPoint.metric == metric,
                    SpcPoint.seq_no == seq_no - 1,
                ).one_or_none()
                if previous_row is None or previous_row.ewma is None:
                    # 방어: 앞 EWMA 가 어떤 사유로 없으면 CENTER 로 시작
                    previous_ewma = center
                else:
                    previous_ewma = float(previous_row.ewma)
            ewma = _next_ewma(value, lambda_value, previous_ewma)
            is_alarm = (value > ucl) or (ewma > ucl)
        else:
            # 계수형: EWMA 미적용 (설계 §1 (다)). value > UCL 만으로 판정.
            ewma = None
            is_alarm = value > ucl

        row = SpcPoint(
            inspection_id=inspection_id,
            metric=metric,
            seq_no=seq_no,
            value=value,
            ewma=ewma,
            center=center,
            ucl=ucl,
            is_alarm=1 if is_alarm else 0,
        )
        sqlalchemy_session.add(row)
        sqlalchemy_session.flush()

        return {
            "seq_no": seq_no,
            "phase": "monitor",
            "is_alarm": is_alarm,
            "center": center,
            "ucl": ucl,
            "ewma": ewma,
        }


def get_spc_series(metric, limit=None):
    """metric 의 SPC 시계열 (seq_no 오름차순). limit 는 최근 N 점만 반환."""
    if metric not in VALID_METRICS:
        raise ValueError("invalid metric: {}".format(metric))

    with app_db.get_session() as sqlalchemy_session:
        query = sqlalchemy_session.query(SpcPoint).filter(SpcPoint.metric == metric)

        if limit is not None:
            # 최근 limit 개 → seq_no 내림차순 상위 → 다시 정렬
            desc_rows = query.order_by(SpcPoint.seq_no.desc()).limit(limit).all()
            rows = list(reversed(desc_rows))
        else:
            rows = query.order_by(SpcPoint.seq_no.asc()).all()

        return [_point_dict(row) for row in rows]


def get_recent_alarms(metric, limit=20):
    """metric 의 최근 알람 목록. 검사자 사용자명 포함."""
    if metric not in VALID_METRICS:
        raise ValueError("invalid metric: {}".format(metric))

    with app_db.get_session() as sqlalchemy_session:
        rows = (
            sqlalchemy_session.query(SpcPoint, Inspection, User)
            .join(Inspection, SpcPoint.inspection_id == Inspection.id)
            .outerjoin(User, Inspection.user_id == User.id)
            .filter(SpcPoint.metric == metric, SpcPoint.is_alarm == 1)
            .order_by(SpcPoint.seq_no.desc())
            .limit(limit)
            .all()
        )
        result = []
        for spc_row, inspection_row, user_row in rows:
            result.append({
                "seq_no": int(spc_row.seq_no),
                "value": float(spc_row.value),
                "ewma": float(spc_row.ewma) if spc_row.ewma is not None else None,
                "ucl": float(spc_row.ucl) if spc_row.ucl is not None else None,
                "inspection_id": int(inspection_row.id),
                "created_at": inspection_row.created_at,
                "username": user_row.username if user_row is not None else None,
            })
        return result


def get_summary(metric):
    """metric 상태 요약: 총 점수, baseline 완료 여부, CENTER/UCL, 알람 수.

    ewma_lambda 필드는 metric 종류에 따라 값이 달라진다.
      - 연속형: EWMA 계수 (float)
      - 계수형: None (EWMA 미적용, 설계 §1 (다))
    """
    if metric not in VALID_METRICS:
        raise ValueError("invalid metric: {}".format(metric))

    with app_db.get_session() as sqlalchemy_session:
        total = int(sqlalchemy_session.query(func.count(SpcPoint.id)).filter(
            SpcPoint.metric == metric,
        ).scalar() or 0)

        alarm_count = int(sqlalchemy_session.query(func.count(SpcPoint.id)).filter(
            SpcPoint.metric == metric,
            SpcPoint.is_alarm == 1,
        ).scalar() or 0)

        center = None
        ucl = None
        if total > BASELINE_SIZE:
            latest = sqlalchemy_session.query(SpcPoint).filter(
                SpcPoint.metric == metric,
                SpcPoint.center.isnot(None),
            ).order_by(SpcPoint.seq_no.desc()).first()
            if latest is not None:
                center = float(latest.center)
                ucl = float(latest.ucl)

        return {
            "total_points": total,
            "baseline_size": BASELINE_SIZE,
            "baseline_complete": total > BASELINE_SIZE,
            "sigma_limit": SIGMA_LIMIT,
            # metric 종류에 따라 λ 값 또는 None (FR-31, FR-57).
            # get_summary 는 VALID_METRICS 검사를 이미 통과.
            "ewma_lambda": METRICS[metric]["lambda"],
            "center": center,
            "ucl": ucl,
            "alarm_count": alarm_count,
        }


# ============================================================
# 내부
# ============================================================

def _point_dict(row):
    return {
        "seq_no": int(row.seq_no),
        "inspection_id": int(row.inspection_id) if row.inspection_id is not None else None,
        "value": float(row.value) if row.value is not None else None,
        "ewma": float(row.ewma) if row.ewma is not None else None,
        "center": float(row.center) if row.center is not None else None,
        "ucl": float(row.ucl) if row.ucl is not None else None,
        "is_alarm": int(row.is_alarm) if row.is_alarm is not None else 0,
    }
