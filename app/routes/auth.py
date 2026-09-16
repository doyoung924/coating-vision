"""
auth blueprint — 회원가입 / 로그인 / 로그아웃 (§3단계 3-2).

- /register  GET·POST
- /login     GET·POST (next 파라미터 지원)
- /logout    GET

이미 로그인된 상태에서 /login·/register 접근 시 / 로 리다이렉트.
폼 검증 실패 시 입력값 (비밀번호 제외) 을 재표시.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..services import auth as auth_service


bp = Blueprint("auth", __name__)


def _safe_next_url(candidate):
    """open redirect 방지: 절대 URL / 스킴 포함 URL 은 거부.
    '/' 로 시작하는 상대 경로만 허용."""
    if not candidate:
        return None
    if not candidate.startswith("/"):
        return None
    if candidate.startswith("//"):
        return None
    return candidate


@bp.route("/register", methods=["GET", "POST"])
def register_page():
    if auth_service.get_current_user() is not None:
        return redirect(url_for("main.index"))

    if request.method == "GET":
        return render_template(
            "auth/register.html",
            form={"username": "", "email": "", "full_name": ""},
        )

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    password_confirm = request.form.get("password_confirm") or ""
    email = (request.form.get("email") or "").strip()
    full_name = (request.form.get("full_name") or "").strip()

    form = {
        "username": username,
        "email": email,
        "full_name": full_name,
    }

    if password != password_confirm:
        flash("비밀번호와 확인이 일치하지 않습니다.", "error")
        return render_template("auth/register.html", form=form), 400

    try:
        auth_service.register(
            username=username,
            password=password,
            email=email or None,
            full_name=full_name or None,
            role="inspector",
        )
    except auth_service.AuthError as exc:
        flash(str(exc), "error")
        return render_template("auth/register.html", form=form), 400
    except Exception as exc:
        flash("가입 처리 중 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return render_template("auth/register.html", form=form), 500

    flash("가입 완료. 로그인해 주세요.", "success")
    return redirect(url_for("auth.login_page"))


@bp.route("/login", methods=["GET", "POST"])
def login_page():
    if auth_service.get_current_user() is not None:
        return redirect(url_for("main.index"))

    next_url = _safe_next_url(request.args.get("next"))

    if request.method == "GET":
        return render_template(
            "auth/login.html",
            form={"username": ""},
            next_url=next_url,
        )

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    posted_next = _safe_next_url(request.form.get("next"))

    form = {"username": username}

    if not username or not password:
        flash("사용자명과 비밀번호를 모두 입력하세요.", "error")
        return render_template("auth/login.html", form=form, next_url=posted_next), 400

    try:
        user_snapshot = auth_service.authenticate(username, password)
    except Exception as exc:
        flash("로그인 처리 중 오류: {}: {}".format(type(exc).__name__, exc), "error")
        return render_template("auth/login.html", form=form, next_url=posted_next), 500

    if user_snapshot is None:
        flash("사용자명 또는 비밀번호가 올바르지 않거나 계정이 비활성 상태입니다.", "error")
        return render_template("auth/login.html", form=form, next_url=posted_next), 401

    auth_service.login_session(user_snapshot)
    flash("환영합니다, {}.".format(user_snapshot["username"]), "success")

    target = posted_next or url_for("main.index")
    return redirect(target)


@bp.route("/logout")
def logout_page():
    auth_service.logout_session()
    flash("로그아웃되었습니다.", "info")
    return redirect(url_for("auth.login_page"))
