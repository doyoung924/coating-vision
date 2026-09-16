"""
SPC API — /api/spc/series (§3-4).

정책: 로그인 필요 + manager 이상. 익명은 401, inspector 는 403 JSON.
"""

from flask import Blueprint, jsonify, request

from ..auth_utils import api_login_required
from ..services import auth as auth_service
from ..services import spc as spc_service


bp = Blueprint("api_spc", __name__, url_prefix="/api")


VALID_METRICS = ("a3", "seg_crack")


def _check_manager():
    """manager 이상 확인. 부족하면 (response, status) 튜플, 통과면 None."""
    current_user = auth_service.get_current_user()
    if current_user is None:
        return jsonify({"error": "authentication required"}), 401
    role = current_user.get("role")
    if role not in ("manager", "admin"):
        return jsonify({"error": "role manager or admin required"}), 403
    return None


@bp.route("/spc/series")
@api_login_required
def spc_series():
    denied = _check_manager()
    if denied is not None:
        return denied

    metric = request.args.get("metric", "a3").strip()
    if metric not in VALID_METRICS:
        return jsonify({"error": "invalid metric: {}".format(metric)}), 400

    limit_raw = request.args.get("limit", "200").strip()
    try:
        limit = max(1, min(2000, int(limit_raw)))
    except ValueError:
        limit = 200

    try:
        series = spc_service.get_spc_series(metric, limit=limit)
        summary = spc_service.get_summary(metric)
    except Exception as exc:
        return jsonify({"error": "{}: {}".format(type(exc).__name__, exc)}), 500

    payload = {
        "metric": metric,
        "limit": limit,
        "summary": {
            "total_points": summary["total_points"],
            "baseline_size": summary["baseline_size"],
            "baseline_complete": summary["baseline_complete"],
            "sigma_limit": summary["sigma_limit"],
            "ewma_lambda": summary["ewma_lambda"],
            "center": summary["center"],
            "ucl": summary["ucl"],
            "alarm_count": summary["alarm_count"],
        },
        "points": series,
    }
    return jsonify(payload)
