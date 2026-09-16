"""
sequence api — /api/sequence/<seq_id>.
§3-3: @api_login_required (미로그인 시 401 JSON, HTML 리다이렉트 하지 않음).
"""

from flask import Blueprint, jsonify, send_from_directory

from .. import config
from .. import store
from ..auth_utils import api_login_required


bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/sequence/<seq_id>")
@api_login_required
def api_sequence(seq_id):
    stream_index_data, stream_index_error = store.get_stream_index()
    if stream_index_data is None:
        return jsonify({"error": stream_index_error}), 503
    target = config.STREAM_DIR / (seq_id + ".json")
    if target.exists() is False:
        return jsonify({"error": "unknown sequence: {}".format(seq_id)}), 404
    return send_from_directory(config.STREAM_DIR, seq_id + ".json")
