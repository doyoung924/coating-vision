"""
admin blueprint — 계정 관리 (§3-7). 전 라우트 @role_required('admin').

- GET  /admin/users               계정 목록 + 검사 건수 조인
- POST /admin/users/<id>/role     역할 변경
- POST /admin/users/<id>/active   활성/비활성 토글
- POST /admin/users/<id>/reset    비밀번호 초기화 (랜덤 12자, flash 로 1회 표시)

권한 검사·안전장치는 services/admin.py.
새 비밀번호는 stdout·로그에 남기지 않는다 (flash 만).
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..auth_utils import role_required
from ..logging_config import get_logger
from ..services import admin as admin_service
from ..services import auth as auth_service
from ..services import cleanup as cleanup_service


log = get_logger(__name__)


bp = Blueprint("admin", __name__, url_prefix="/admin")


DEFAULT_PAGE_SIZE = 20          # §3-10 버그 수정: board/history 와 통일
ALLOWED_PAGE_SIZES = (20, 50, 100)


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


@bp.route("/users")
@role_required("admin")
def users_list():
    page = max(1, _parse_int(request.args.get("page", "1"), 1))
    per_page = _parse_per_page(request.args.get("per_page", ""))
    username_filter = (request.args.get("username", "") or "").strip()
    role_filter = (request.args.get("role", "") or "").strip()

    try:
        rows, total_count, total_pages = admin_service.list_users(
            page=page,
            per_page=per_page,
            username_filter=username_filter or None,
            role_filter=role_filter or None,
        )
        error_message = None
    except Exception as exc:
        rows, total_count, total_pages = [], 0, 1
        error_message = "계정 조회 오류: {}: {}".format(type(exc).__name__, exc)

    current_user = auth_service.get_current_user()
    return render_template(
        "admin/users.html",
        rows=rows,
        total_count=total_count,
        page=page,
        total_pages=total_pages,
        page_size=per_page,
        allowed_page_sizes=ALLOWED_PAGE_SIZES,
        filters={"username": username_filter, "role": role_filter},
        valid_roles=admin_service.VALID_ROLES,
        current_user=current_user,
        error_message=error_message,
    )


@bp.route("/users/<int:user_id>/role", methods=["POST"])
@role_required("admin")
def users_change_role(user_id):
    current_user = auth_service.get_current_user()
    new_role = (request.form.get("new_role") or "").strip()
    try:
        result = admin_service.change_role(
            actor_id=current_user["id"],
            target_id=user_id,
            new_role=new_role,
        )
    except admin_service.AdminError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.users_list"))
    except Exception as exc:
        flash("역할 변경 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return redirect(url_for("admin.users_list"))

    if result.get("changed"):
        flash("사용자 #{} 역할 변경: {} → {}".format(
            user_id, result["old_role"], result["new_role"]), "success")
    else:
        flash("변경 없음: {}".format(result.get("reason", "")), "info")
    return redirect(url_for("admin.users_list"))


@bp.route("/users/<int:user_id>/active", methods=["POST"])
@role_required("admin")
def users_toggle_active(user_id):
    current_user = auth_service.get_current_user()
    try:
        result = admin_service.toggle_active(
            actor_id=current_user["id"],
            target_id=user_id,
        )
    except admin_service.AdminError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.users_list"))
    except Exception as exc:
        flash("활성 토글 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return redirect(url_for("admin.users_list"))

    flash("사용자 #{} 활성 상태: {} → {}".format(
        user_id, result["old_active"], result["new_active"]), "success")
    return redirect(url_for("admin.users_list"))


@bp.route("/users/<int:user_id>/reset", methods=["POST"])
@role_required("admin")
def users_reset_password(user_id):
    current_user = auth_service.get_current_user()
    try:
        result = admin_service.reset_password(
            actor_id=current_user["id"],
            target_id=user_id,
        )
    except admin_service.AdminError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.users_list"))
    except Exception as exc:
        flash("비밀번호 초기화 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return redirect(url_for("admin.users_list"))

    # 새 비밀번호는 flash 로만 표시. 서버 로그에 남기지 않는다.
    log.info("Password reset by admin_id=%s target_user_id=%d target_username=%s",
             current_user["id"], user_id, result["username"])
    flash(
        "사용자 '{}' 의 비밀번호가 초기화되었습니다. 새 비밀번호: {}. "
        "이 값은 지금 이 화면에서만 확인 가능합니다. 사용자에게 안전한 채널로 전달하세요.".format(
            result["username"], result["new_password"],
        ),
        "success",
    )
    return redirect(url_for("admin.users_list"))


# ============================================================
# /admin/storage — 업로드 파일 관리 (§3-8)
# ============================================================

@bp.route("/storage")
@role_required("admin")
def storage_page():
    try:
        status = cleanup_service.storage_status()
        error_message = None
    except Exception as exc:
        status = {"file_count": 0, "total_bytes": 0, "orphan_count": 0, "referenced_count": 0}
        error_message = "저장소 상태 조회 오류: {}: {}".format(type(exc).__name__, exc)

    import os
    retention_days = int(os.environ.get("UPLOAD_RETENTION_DAYS", "30"))

    return render_template(
        "admin/storage.html",
        status=status,
        retention_days=retention_days,
        error_message=error_message,
    )


@bp.route("/storage/cleanup-orphan", methods=["POST"])
@role_required("admin")
def storage_cleanup_orphan():
    current_user = auth_service.get_current_user()
    try:
        result = cleanup_service.cleanup_orphan_files()
    except Exception as exc:
        flash("고아 정리 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return redirect(url_for("admin.storage_page"))

    log.info("Orphan cleanup by admin_id=%s scanned=%d removed=%d bytes=%d",
             current_user["id"], result["scanned"], result["removed"], result["bytes"])
    flash("고아 파일 정리 완료: 스캔 {} · 제거 {} 파일 · {} bytes 확보".format(
        result["scanned"], result["removed"], result["bytes"]), "success")
    return redirect(url_for("admin.storage_page"))


@bp.route("/storage/cleanup-old", methods=["POST"])
@role_required("admin")
def storage_cleanup_old():
    current_user = auth_service.get_current_user()
    import os
    days = int(request.form.get("days") or os.environ.get("UPLOAD_RETENTION_DAYS", "30"))
    try:
        result = cleanup_service.cleanup_old_files(days=days)
    except Exception as exc:
        flash("보존 기간 정리 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return redirect(url_for("admin.storage_page"))

    log.info("Old cleanup by admin_id=%s days=%d processed=%d removed=%d bytes=%d",
             current_user["id"], result["days"], result["processed"], result["removed"], result["bytes"])
    flash("{} 일 이전 검사 파일 정리: 검사 {}건 처리 · 파일 {} 제거 · {} bytes 확보".format(
        result["days"], result["processed"], result["removed"], result["bytes"]), "success")
    return redirect(url_for("admin.storage_page"))
