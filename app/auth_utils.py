"""
인증 데코레이터 (§3단계 3-2, 3-3 확장).

- @login_required: 미로그인 시 /login 으로 리다이렉트. 원래 URL 은 next 파라미터.
- @role_required('manager','admin'): 권한 부족 시 403.
- @api_login_required: API 라우트용. 미로그인 시 리다이렉트 대신 401 JSON.

role 위계: admin > manager > inspector.
"""

from functools import wraps

from flask import abort, jsonify, redirect, request, url_for

from .services import auth as auth_service


ROLE_RANK = {
    "inspector": 1,
    "manager": 2,
    "admin": 3,
}


def login_required(view_function):
    @wraps(view_function)
    def wrapped(*args, **kwargs):
        current_user = auth_service.get_current_user()
        if current_user is None:
            return redirect(url_for("auth.login_page", next=request.full_path))
        return view_function(*args, **kwargs)
    return wrapped


def api_login_required(view_function):
    """/api/* 전용. 미로그인 시 HTML 리다이렉트 대신 401 JSON 반환."""
    @wraps(view_function)
    def wrapped(*args, **kwargs):
        current_user = auth_service.get_current_user()
        if current_user is None:
            return jsonify({"error": "authentication required"}), 401
        return view_function(*args, **kwargs)
    return wrapped


def role_required(*allowed_roles):
    """지정 role 중 하나 이상 만족해야 통과. 위계상 상위 role 은 자동 통과.

    예: @role_required('manager') 는 manager, admin 통과. inspector 는 403.
    """
    if not allowed_roles:
        raise ValueError("role_required 는 하나 이상의 role 을 받아야 한다.")
    min_rank = min(ROLE_RANK[role] for role in allowed_roles if role in ROLE_RANK)

    def decorator(view_function):
        @wraps(view_function)
        def wrapped(*args, **kwargs):
            current_user = auth_service.get_current_user()
            if current_user is None:
                return redirect(url_for("auth.login_page", next=request.full_path))
            user_rank = ROLE_RANK.get(current_user.get("role"), 0)
            if user_rank < min_rank:
                abort(403)
            return view_function(*args, **kwargs)
        return wrapped
    return decorator
