"""
spc blueprint — 누적 관리도 (§3-4). manager 이상만 접근.
"""

from flask import Blueprint, render_template, request

from ..auth_utils import role_required
from ..services import spc as spc_service


bp = Blueprint("spc", __name__)


VALID_METRICS = ("a3", "seg_crack")


@bp.route("/spc")
@role_required("manager", "admin")
def spc_page():
    metric = request.args.get("metric", "a3").strip()
    if metric not in VALID_METRICS:
        metric = "a3"

    try:
        summary = spc_service.get_summary(metric)
        recent_alarms = spc_service.get_recent_alarms(metric, limit=20)
        error_message = None
    except Exception as exc:
        summary = None
        recent_alarms = []
        error_message = "SPC 상태 조회 오류: {}: {}".format(type(exc).__name__, exc)

    return render_template(
        "spc.html",
        metric=metric,
        valid_metrics=VALID_METRICS,
        summary=summary,
        recent_alarms=recent_alarms,
        error_message=error_message,
    )
