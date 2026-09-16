"""
main blueprint — 개요 페이지 + 정적 파일 서빙.
§3-3: @login_required 적용. 대시보드 상단에 로그인 사용자 요약 카드 추가.
계산 로직 미변경.
"""

from flask import Blueprint, render_template, request, send_from_directory

from .. import config
from .. import store
from ..auth_utils import login_required
from ..services import auth as auth_service
from ..services import inspection_store


bp = Blueprint("main", __name__)


def spc_figure_name(sequence):
    return "spc_{}_{}_{}.png".format(
        sequence["run_id"],
        sequence["coating_gap"],
        sequence["position"],
    )


def sequence_label(sequence):
    return "{}/{}µm/{}".format(
        sequence["run_id"],
        sequence["coating_gap"],
        sequence["position"],
    )


def interpretation_priority(item):
    return item.get("priority", 99)


def sort_interpretations(interpretations):
    ordered = []
    for item in interpretations:
        ordered.append(item)
    ordered.sort(key=interpretation_priority)
    return ordered


@bp.route("/")
@login_required
def index():
    advisor = store.get_advisor()
    sequences = advisor["sequences"]

    seq_index_raw = request.args.get("seq", "0")
    try:
        seq_index = int(seq_index_raw)
    except ValueError:
        seq_index = 0
    if seq_index < 0 or seq_index >= len(sequences):
        seq_index = 0

    selected = sequences[seq_index]

    sidebar = []
    for idx in range(len(sequences)):
        sequence = sequences[idx]
        entry = {
            "index": idx,
            "label": sequence_label(sequence),
            "capability": sequence["observation"]["spc"]["capability"],
            "center": sequence["observation"]["spc"]["center"],
            "ucl": sequence["observation"]["spc"]["ucl"],
            "alerts": sequence["observation"]["spc"]["alert_total"],
            "selected": (idx == seq_index),
        }
        sidebar.append(entry)

    # §3-3 대시보드 요약 (DB 실패해도 페이지가 깨지지 않게 None 처리)
    current_user = auth_service.get_current_user()
    user_summary = None
    if current_user is not None:
        try:
            user_summary = inspection_store.get_dashboard_summary(current_user["id"])
        except Exception:
            user_summary = None

    return render_template(
        "index.html",
        headline=store.get_headline(),
        sidebar=sidebar,
        selected=selected,
        selected_index=seq_index,
        selected_label=sequence_label(selected),
        spc_figure=spc_figure_name(selected),
        interpretations=sort_interpretations(selected["interpretations"]),
        domain_gap_note=advisor.get("domain_gap_note", {}),
        user_summary=user_summary,
    )


@bp.route("/figures/<path:name>")
@login_required
def serve_figure(name):
    return send_from_directory(config.FIGURES_DIR, name)


@bp.route("/frame/<path:name>")
@login_required
def serve_frame(name):
    return send_from_directory(config.IMAGE_DIR, name)
