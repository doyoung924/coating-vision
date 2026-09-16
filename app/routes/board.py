"""
board blueprint — 게시판 (§3-5).

모든 라우트 @login_required.
카테고리 정책·권한 검증은 services/board 안에서. 라우트는 얇게.
"""

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from ..auth_utils import login_required
from ..services import auth as auth_service
from ..services import board as board_service


bp = Blueprint("board", __name__)


VALID_CATEGORIES_WITH_ALL = ("all",) + board_service.VALID_CATEGORIES

DEFAULT_PAGE_SIZE = 20
ALLOWED_PAGE_SIZES = (20, 50, 100)   # §3-9 화이트리스트


def _parse_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_per_page(raw):
    try:
        candidate = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    if candidate in ALLOWED_PAGE_SIZES:
        return candidate
    return DEFAULT_PAGE_SIZE


@bp.route("/board")
@login_required
def board_list():
    category = request.args.get("category", "all").strip()
    if category not in VALID_CATEGORIES_WITH_ALL:
        category = "all"
    page = max(1, _parse_int(request.args.get("page", "1"), 1))
    per_page = _parse_per_page(request.args.get("per_page", ""))

    try:
        rows, total_count, total_pages = board_service.get_posts(category, page, per_page=per_page)
        error_message = None
    except Exception as exc:
        rows, total_count, total_pages = [], 0, 1
        error_message = "게시판 조회 오류: {}: {}".format(type(exc).__name__, exc)

    return render_template(
        "board/list.html",
        rows=rows,
        total_count=total_count,
        page=page,
        total_pages=total_pages,
        page_size=per_page,
        allowed_page_sizes=ALLOWED_PAGE_SIZES,
        category=category,
        valid_categories=VALID_CATEGORIES_WITH_ALL,
        error_message=error_message,
    )


@bp.route("/board/new", methods=["GET", "POST"])
@login_required
def board_new():
    current_user = auth_service.get_current_user()

    # 연결된 검사 미리보기 (GET 은 URL 파라미터, POST 는 form 필드)
    inspection_id_raw = request.args.get("inspection_id") or request.form.get("inspection_id")
    inspection_id = None
    if inspection_id_raw:
        inspection_id = _parse_int(inspection_id_raw, None)

    default_category = request.args.get("category", "free").strip()
    if default_category not in board_service.VALID_CATEGORIES:
        default_category = "free"

    if request.method == "GET":
        inspection_summary = _load_inspection_summary(inspection_id)
        return render_template(
            "board/form.html",
            mode="new",
            form={"title": "", "content": "", "category": default_category,
                  "inspection_id": inspection_id},
            inspection_summary=inspection_summary,
            can_write_notice=(current_user["role"] in ("manager", "admin")),
        )

    title = (request.form.get("title") or "").strip()
    content = request.form.get("content") or ""
    category = (request.form.get("category") or "free").strip()

    form = {"title": title, "content": content, "category": category,
            "inspection_id": inspection_id}

    try:
        new_id = board_service.create_post(
            user_id=current_user["id"],
            user_role=current_user["role"],
            category=category,
            title=title,
            content=content,
            inspection_id=inspection_id,
        )
    except board_service.BoardError as exc:
        flash(str(exc), "error")
        return render_template(
            "board/form.html",
            mode="new",
            form=form,
            inspection_summary=_load_inspection_summary(inspection_id),
            can_write_notice=(current_user["role"] in ("manager", "admin")),
        ), 400
    except Exception as exc:
        flash("작성 중 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return render_template(
            "board/form.html",
            mode="new",
            form=form,
            inspection_summary=_load_inspection_summary(inspection_id),
            can_write_notice=(current_user["role"] in ("manager", "admin")),
        ), 500

    flash("작성 완료.", "success")
    return redirect(url_for("board.board_detail", post_id=new_id))


@bp.route("/board/<int:post_id>")
@login_required
def board_detail(post_id):
    current_user = auth_service.get_current_user()
    try:
        detail = board_service.get_post(
            post_id, viewer_user_id=current_user["id"], increment=True,
        )
    except Exception as exc:
        flash("상세 조회 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return redirect(url_for("board.board_list"))

    if detail is None:
        abort(404)

    can_edit = (
        (detail["user_id"] is not None and detail["user_id"] == current_user["id"])
        or current_user["role"] == "admin"
    )
    return render_template(
        "board/detail.html",
        post=detail,
        current_user=current_user,
        can_edit=can_edit,
    )


@bp.route("/board/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def board_edit(post_id):
    current_user = auth_service.get_current_user()
    detail = board_service.get_post(post_id, viewer_user_id=current_user["id"], increment=False)
    if detail is None:
        abort(404)

    is_owner = detail["user_id"] is not None and detail["user_id"] == current_user["id"]
    is_admin = current_user["role"] == "admin"
    if not (is_owner or is_admin):
        abort(403)

    if request.method == "GET":
        return render_template(
            "board/form.html",
            mode="edit",
            form={
                "title": detail["title"],
                "content": detail["content"] or "",
                "category": detail["category"],
                "inspection_id": detail["inspection_id"],
            },
            post_id=post_id,
            inspection_summary=_load_inspection_summary(detail["inspection_id"]),
            can_write_notice=(current_user["role"] in ("manager", "admin")),
        )

    title = (request.form.get("title") or "").strip()
    content = request.form.get("content") or ""
    category = (request.form.get("category") or detail["category"]).strip()

    try:
        board_service.update_post(
            post_id=post_id,
            viewer_user_id=current_user["id"],
            viewer_role=current_user["role"],
            title=title,
            content=content,
            category=category,
        )
    except board_service.BoardError as exc:
        flash(str(exc), "error")
        return render_template(
            "board/form.html",
            mode="edit",
            form={"title": title, "content": content, "category": category,
                  "inspection_id": detail["inspection_id"]},
            post_id=post_id,
            inspection_summary=_load_inspection_summary(detail["inspection_id"]),
            can_write_notice=(current_user["role"] in ("manager", "admin")),
        ), 400

    flash("수정 완료.", "success")
    return redirect(url_for("board.board_detail", post_id=post_id))


@bp.route("/board/<int:post_id>/delete", methods=["POST"])
@login_required
def board_delete(post_id):
    current_user = auth_service.get_current_user()
    try:
        ok = board_service.delete_post(
            post_id=post_id,
            viewer_user_id=current_user["id"],
            viewer_role=current_user["role"],
        )
    except Exception as exc:
        flash("삭제 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return redirect(url_for("board.board_detail", post_id=post_id))

    if not ok:
        abort(403)
    flash("게시글 #{} 삭제 완료.".format(post_id), "success")
    return redirect(url_for("board.board_list"))


@bp.route("/board/<int:post_id>/comment", methods=["POST"])
@login_required
def board_comment_new(post_id):
    current_user = auth_service.get_current_user()
    content = request.form.get("content") or ""
    try:
        board_service.create_comment(
            post_id=post_id,
            user_id=current_user["id"],
            content=content,
        )
    except board_service.BoardError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        flash("댓글 작성 오류: {}: {}".format(type(exc).__name__, exc), "error")
    return redirect(url_for("board.board_detail", post_id=post_id))


@bp.route("/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def comment_delete(comment_id):
    current_user = auth_service.get_current_user()
    try:
        post_id, ok = board_service.delete_comment(
            comment_id=comment_id,
            viewer_user_id=current_user["id"],
            viewer_role=current_user["role"],
        )
    except Exception as exc:
        flash("댓글 삭제 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return redirect(url_for("board.board_list"))

    if not ok:
        if post_id is not None:
            abort(403)
        abort(404)
    flash("댓글 삭제 완료.", "success")
    return redirect(url_for("board.board_detail", post_id=post_id))


# ============================================================
# 내부
# ============================================================

def _load_inspection_summary(inspection_id):
    """작성 폼에서 연결된 검사 정보를 요약 표시 (읽기 전용)."""
    if inspection_id is None:
        return None
    from .. import db as app_db
    from ..models import Inspection
    try:
        with app_db.get_session() as s:
            row = s.get(Inspection, inspection_id)
            if row is None:
                return None
            return {
                "id": int(row.id),
                "file_name": row.file_name,
                "status": row.status,
                "a3_ratio": float(row.a3_ratio) if row.a3_ratio is not None else None,
                "seg_crack": float(row.seg_crack) if row.seg_crack is not None else None,
                "pinhole_count": int(row.pinhole_count) if row.pinhole_count is not None else None,
                "created_at": row.created_at,
            }
    except Exception:
        return None
