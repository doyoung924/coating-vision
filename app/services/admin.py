"""
관리자 계정 관리 서비스 (§3-7).

세 가지 안전장치를 서비스 계층에서 검증한다 (라우트/템플릿에만 의존하지 않음):
1. 자기 자신의 역할을 낮출 수 없다.
2. 자기 자신을 비활성화할 수 없다.
3. 마지막 활성 admin 의 역할을 내리거나 비활성화할 수 없다.

권한 판정 (@role_required('admin')) 은 라우트에서. 이 서비스는 admin 만
호출한다는 가정이지만, actor_id 파라미터로 안전장치를 재확인한다.
"""

import secrets
from math import ceil

from sqlalchemy import func
from werkzeug.security import generate_password_hash

from .. import db as app_db
from ..models import Inspection, User


PAGE_SIZE = 30
VALID_ROLES = ("inspector", "manager", "admin")
RESET_PASSWORD_BYTES = 9  # secrets.token_urlsafe(9) → 12자 base64


class AdminError(Exception):
    """관리자 조작 실패 (안전장치 위반 · 대상 미존재 등)."""
    pass


# ============================================================
# 목록
# ============================================================

def list_users(page=1, per_page=PAGE_SIZE, username_filter=None, role_filter=None):
    """전체 계정. 검사 건수를 서브쿼리로 조인. 최신 가입순."""
    if page < 1:
        page = 1
    offset = (page - 1) * per_page

    with app_db.get_session() as sqlalchemy_session:
        # 검사 건수 서브쿼리
        inspection_count_subq = (
            sqlalchemy_session.query(
                Inspection.user_id.label("uid"),
                func.count(Inspection.id).label("cnt"),
            )
            .group_by(Inspection.user_id)
            .subquery()
        )

        query = (
            sqlalchemy_session.query(User, inspection_count_subq.c.cnt)
            .outerjoin(inspection_count_subq, User.id == inspection_count_subq.c.uid)
        )

        total_query = sqlalchemy_session.query(func.count(User.id))
        if username_filter:
            like = "%{}%".format(username_filter)
            query = query.filter(User.username.like(like))
            total_query = total_query.filter(User.username.like(like))
        if role_filter and role_filter in VALID_ROLES:
            query = query.filter(User.role == role_filter)
            total_query = total_query.filter(User.role == role_filter)

        query = query.order_by(User.created_at.desc(), User.id.desc())
        rows = query.offset(offset).limit(per_page).all()

        total_count = int(total_query.scalar() or 0)
        total_pages = max(1, ceil(total_count / per_page)) if total_count else 1

        result = []
        for user, cnt in rows:
            result.append({
                "id": int(user.id),
                "username": user.username,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "is_active": int(user.is_active) if user.is_active is not None else 1,
                "created_at": user.created_at,
                "last_login_at": user.last_login_at,
                "inspection_count": int(cnt) if cnt is not None else 0,
            })
    return result, total_count, total_pages


# ============================================================
# 역할 변경
# ============================================================

def change_role(actor_id, target_id, new_role):
    if new_role not in VALID_ROLES:
        raise AdminError("유효하지 않은 역할: {}".format(new_role))

    with app_db.get_session() as sqlalchemy_session:
        target = sqlalchemy_session.get(User, target_id)
        if target is None:
            raise AdminError("대상 계정이 존재하지 않습니다 (id={}).".format(target_id))

        # 안전장치 1: 자기 자신의 역할을 낮출 수 없다.
        if int(target.id) == int(actor_id):
            if target.role == "admin" and new_role != "admin":
                raise AdminError(
                    "자기 자신의 역할을 낮출 수 없습니다. "
                    "다른 admin 이 처리하도록 요청하세요."
                )
            # 같거나 올리는 경우는 허용 (admin → admin 유지)

        # 안전장치 3: 마지막 활성 admin 을 non-admin 으로 변경 불가
        if target.role == "admin" and new_role != "admin":
            _ensure_not_last_admin(sqlalchemy_session, exclude_id=target.id)

        if target.role == new_role:
            return {"changed": False, "reason": "이미 {} 역할입니다.".format(new_role)}

        old_role = target.role
        target.role = new_role
        sqlalchemy_session.flush()
    return {
        "changed": True,
        "old_role": old_role,
        "new_role": new_role,
    }


# ============================================================
# 활성 토글
# ============================================================

def toggle_active(actor_id, target_id):
    with app_db.get_session() as sqlalchemy_session:
        target = sqlalchemy_session.get(User, target_id)
        if target is None:
            raise AdminError("대상 계정이 존재하지 않습니다 (id={}).".format(target_id))

        current_active = int(target.is_active or 0)
        new_active = 0 if current_active == 1 else 1

        # 안전장치 2: 자기 자신을 비활성화할 수 없다.
        if int(target.id) == int(actor_id) and new_active == 0:
            raise AdminError("자기 자신을 비활성화할 수 없습니다.")

        # 안전장치 3: 마지막 활성 admin 을 비활성화 불가
        if target.role == "admin" and current_active == 1 and new_active == 0:
            _ensure_not_last_admin(sqlalchemy_session, exclude_id=target.id)

        target.is_active = new_active
        sqlalchemy_session.flush()
    return {
        "old_active": current_active,
        "new_active": new_active,
    }


# ============================================================
# 비밀번호 초기화
# ============================================================

def reset_password(actor_id, target_id):
    with app_db.get_session() as sqlalchemy_session:
        target = sqlalchemy_session.get(User, target_id)
        if target is None:
            raise AdminError("대상 계정이 존재하지 않습니다 (id={}).".format(target_id))

        new_password = secrets.token_urlsafe(RESET_PASSWORD_BYTES)
        target.password_hash = generate_password_hash(new_password)
        sqlalchemy_session.flush()
    return {
        "username": target.username,
        "new_password": new_password,
    }


# ============================================================
# 내부
# ============================================================

def _ensure_not_last_admin(sqlalchemy_session, exclude_id):
    """`exclude_id` 를 제외했을 때 활성 admin 이 남지 않으면 예외.
    (마지막 admin 안전장치)"""
    remaining = sqlalchemy_session.query(func.count(User.id)).filter(
        User.role == "admin",
        User.is_active == 1,
        User.id != exclude_id,
    ).scalar()
    if int(remaining or 0) == 0:
        raise AdminError(
            "마지막 활성 admin 입니다. 이 조작을 수행하면 시스템에 admin 이 "
            "0명이 되어 잠깁니다. 먼저 다른 계정을 admin 으로 승격하세요."
        )
