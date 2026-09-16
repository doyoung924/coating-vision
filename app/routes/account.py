"""
account blueprint — 내 정보 · 비밀번호 변경 (§3-7). 전 라우트 @login_required.

- GET/POST /account            프로필 (이름·이메일 수정)
- GET/POST /account/password   비밀번호 변경 (세션 유지)
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import db as app_db
from ..auth_utils import login_required
from ..models import User
from ..services import auth as auth_service


bp = Blueprint("account", __name__)


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account_page():
    current_user = auth_service.get_current_user()

    # 표시용 정보는 DB 에서 매번 재조회 (수정 반영)
    try:
        with app_db.get_session() as sqlalchemy_session:
            user = sqlalchemy_session.get(User, current_user["id"])
            profile = {
                "id": int(user.id),
                "username": user.username,
                "role": user.role,
                "email": user.email or "",
                "full_name": user.full_name or "",
                "created_at": user.created_at,
                "last_login_at": user.last_login_at,
            }
    except Exception as exc:
        flash("프로필 조회 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return redirect(url_for("main.index"))

    if request.method == "GET":
        return render_template("account/profile.html", profile=profile)

    full_name = (request.form.get("full_name") or "").strip()
    email = (request.form.get("email") or "").strip()

    try:
        auth_service.update_profile(
            user_id=current_user["id"],
            full_name=full_name or None,
            email=email or None,
        )
    except auth_service.AuthError as exc:
        flash(str(exc), "error")
        profile["email"] = email
        profile["full_name"] = full_name
        return render_template("account/profile.html", profile=profile), 400
    except Exception as exc:
        flash("프로필 갱신 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return redirect(url_for("account.account_page"))

    flash("프로필이 갱신되었습니다.", "success")
    return redirect(url_for("account.account_page"))


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def password_page():
    if request.method == "GET":
        return render_template("account/password.html")

    current_user = auth_service.get_current_user()
    current_password = request.form.get("current_password") or ""
    new_password = request.form.get("new_password") or ""
    new_password_confirm = request.form.get("new_password_confirm") or ""

    if new_password != new_password_confirm:
        flash("새 비밀번호와 확인이 일치하지 않습니다.", "error")
        return render_template("account/password.html"), 400

    try:
        auth_service.change_password(
            user_id=current_user["id"],
            current_password=current_password,
            new_password=new_password,
        )
    except auth_service.AuthError as exc:
        flash(str(exc), "error")
        return render_template("account/password.html"), 400
    except Exception as exc:
        flash("비밀번호 변경 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return render_template("account/password.html"), 500

    # 세션 유지 (session.clear 하지 않는다). user_id·role 그대로.
    flash("비밀번호가 변경되었습니다. 다음 로그인부터 새 비밀번호를 사용하세요.", "success")
    return redirect(url_for("account.account_page"))
