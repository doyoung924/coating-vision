"""
stream blueprint — /stream 페이지.
§3-3: @login_required 적용.
"""

from flask import Blueprint, render_template

from .. import store
from ..auth_utils import login_required


bp = Blueprint("stream", __name__)


@bp.route("/stream")
@login_required
def stream_page():
    stream_index_data, stream_index_error = store.get_stream_index()
    if stream_index_data is None:
        return render_template(
            "stream.html",
            index_ready=False,
            index_error=stream_index_error,
            sequences=[],
            patch_threshold=None,
            initial_seq=None,
        )

    sequences_list = stream_index_data.get("sequences", [])
    patch_config = stream_index_data.get("patch_config", {})
    initial_seq = None
    if len(sequences_list) > 0:
        initial_seq = sequences_list[0]["sequence_id"]

    return render_template(
        "stream.html",
        index_ready=True,
        index_error=None,
        sequences=sequences_list,
        patch_threshold=patch_config.get("threshold"),
        initial_seq=initial_seq,
    )
