"""
인증 서비스 계층 (§3단계 3-2).

라우트는 얇게 유지하고 검증·DB 접근은 여기에.
비밀번호 해싱은 werkzeug.security 만 사용 (직접 구현 금지).

세션에는 user_id·role 만 저장. 비밀번호·해시는 절대 저장하지 않는다.
"""

from datetime import datetime

from flask import session
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from .. import db as app_db
from ..logging_config import get_logger
from ..models import User


log = get_logger(__name__)


MIN_PASSWORD_LENGTH = 8
VALID_ROLES = ("inspector", "manager", "admin")


class AuthError(Exception):
    """검증 실패 · 중복 등 회복 가능한 인증 오류."""
    pass


# ============================================================
# 회원가입
# ============================================================

def register(username, password, email=None, full_name=None, role="inspector"):
    """새 계정을 생성해 User 를 반환한다. 검증 실패 시 AuthError."""
    _validate_registration(username, password, email, role)

    with app_db.get_session() as sqlalchemy_session:
        if _find_user_by_username(sqlalchemy_session, username) is not None:
            raise AuthError("이미 사용 중인 사용자명입니다: {}".format(username))
        if email:
            if _find_user_by_email(sqlalchemy_session, email) is not None:
                raise AuthError("이미 사용 중인 이메일입니다: {}".format(email))

        password_hash = generate_password_hash(password)
        new_user = User(
            username=username,
            password_hash=password_hash,
            email=email,
            full_name=full_name,
            role=role,
        )
        sqlalchemy_session.add(new_user)
        try:
            sqlalchemy_session.flush()
        except SQLAlchemyError as exc:
            raise AuthError("계정 생성 중 DB 오류: {}".format(exc))
        # commit 은 context manager 에서 자동 수행.
        # id 를 반환하기 위해 flush 후 값을 캡처 (session 종료 후 접근 방지).
        created_user_id = int(new_user.id)
        created_username = new_user.username
        created_role = new_user.role

    # 세션 밖에서 안전하게 접근 가능한 dict-like 반환 대신 detached 조회.
    return _load_user_snapshot(created_user_id, created_username, created_role)


def _validate_registration(username, password, email, role):
    if not username or not username.strip():
        raise AuthError("사용자명은 비워둘 수 없습니다.")
    if len(username) > 50:
        raise AuthError("사용자명은 50자 이하여야 합니다.")
    if password is None or len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError("비밀번호는 최소 {}자 이상이어야 합니다.".format(MIN_PASSWORD_LENGTH))
    if email is not None and email != "":
        if "@" not in email or len(email) > 120:
            raise AuthError("이메일 형식이 올바르지 않습니다.")
    if role not in VALID_ROLES:
        raise AuthError("role 은 {} 중 하나여야 합니다.".format(", ".join(VALID_ROLES)))


# ============================================================
# 로그인 (인증)
# ============================================================

def authenticate(username, password):
    """자격 증명 검사. 성공 시 User 스냅샷 반환, 실패 시 None.

    - IS_ACTIVE = 0 이면 거부 (None 반환)
    - 성공 시 LAST_LOGIN_AT 갱신
    - 로그: 성공/실패 사유 (비밀번호는 절대 남기지 않음)
    """
    if not username or password is None:
        log.warning("Login failed: empty username or password")
        return None

    with app_db.get_session() as sqlalchemy_session:
        user = _find_user_by_username(sqlalchemy_session, username)
        if user is None:
            log.warning("Login failed username=%s reason=unknown_user", username)
            return None
        if int(user.is_active) == 0:
            log.warning("Login failed username=%s reason=inactive", username)
            return None
        if not check_password_hash(user.password_hash, password):
            log.warning("Login failed username=%s reason=bad_password", username)
            return None

        user.last_login_at = datetime.utcnow()
        sqlalchemy_session.flush()
        log.info("Login success username=%s role=%s", user.username, user.role)
        return _load_user_snapshot(int(user.id), user.username, user.role)


# ============================================================
# 세션 헬퍼
# ============================================================

def login_session(user_snapshot):
    """authenticate 로 얻은 user 를 Flask 세션에 등록. 값 최소화."""
    session.clear()
    session["user_id"] = int(user_snapshot["id"])
    session["role"] = user_snapshot["role"]
    session.permanent = False


def logout_session():
    session.clear()


def change_password(user_id, current_password, new_password):
    """비밀번호 변경. 현재 비밀번호 검증 필수.
    성공 시 True, 실패 시 AuthError raise.
    세션은 유지 (호출자가 처리하지 않아도 됨)."""
    if current_password is None or new_password is None:
        raise AuthError("현재 비밀번호와 새 비밀번호를 모두 입력하세요.")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise AuthError("새 비밀번호는 최소 {}자 이상이어야 합니다.".format(MIN_PASSWORD_LENGTH))

    with app_db.get_session() as sqlalchemy_session:
        user = sqlalchemy_session.get(User, user_id)
        if user is None:
            raise AuthError("사용자를 찾을 수 없습니다.")
        if not check_password_hash(user.password_hash, current_password):
            raise AuthError("현재 비밀번호가 올바르지 않습니다.")
        if check_password_hash(user.password_hash, new_password):
            raise AuthError("새 비밀번호가 현재 비밀번호와 동일합니다.")
        user.password_hash = generate_password_hash(new_password)
        sqlalchemy_session.flush()
    return True


def update_profile(user_id, full_name, email):
    """이름·이메일 수정. 이메일은 다른 사용자와 중복 시 거부."""
    with app_db.get_session() as sqlalchemy_session:
        user = sqlalchemy_session.get(User, user_id)
        if user is None:
            raise AuthError("사용자를 찾을 수 없습니다.")

        # 이메일 형식 검증
        if email is not None and email != "":
            if "@" not in email or len(email) > 120:
                raise AuthError("이메일 형식이 올바르지 않습니다.")
            # 다른 사용자 중복 확인
            existing = sqlalchemy_session.query(User).filter(
                User.email == email,
                User.id != user_id,
            ).one_or_none()
            if existing is not None:
                raise AuthError("이미 사용 중인 이메일입니다: {}".format(email))

        if full_name is not None and len(full_name) > 100:
            raise AuthError("이름은 100자 이하여야 합니다.")

        user.full_name = full_name if full_name else None
        user.email = email if email else None
        sqlalchemy_session.flush()

        return {
            "id": int(user.id),
            "username": user.username,
            "role": user.role,
            "email": user.email,
            "full_name": user.full_name,
        }


def get_current_user():
    """세션 user_id 로 요청마다 DB 조회. 세션 없으면 None."""
    user_id = session.get("user_id")
    if user_id is None:
        return None
    try:
        with app_db.get_session() as sqlalchemy_session:
            user = sqlalchemy_session.get(User, user_id)
            if user is None:
                return None
            if int(user.is_active) == 0:
                return None
            return _load_user_snapshot(int(user.id), user.username, user.role, extra={
                "email": user.email,
                "full_name": user.full_name,
            })
    except Exception:
        # DB 연결 실패 등. 라우트 렌더링이 죽지 않도록 None 반환.
        return None


# ============================================================
# 내부 헬퍼
# ============================================================

def _find_user_by_username(sqlalchemy_session, username):
    return sqlalchemy_session.query(User).filter(User.username == username).one_or_none()


def _find_user_by_email(sqlalchemy_session, email):
    return sqlalchemy_session.query(User).filter(User.email == email).one_or_none()


def _load_user_snapshot(user_id, username, role, extra=None):
    """세션 종료 후에도 안전하게 다루기 위한 dict 스냅샷.
    ORM User 를 라우트/템플릿에 넘기면 detached 상태 접근 오류가 날 수 있다."""
    snapshot = {
        "id": user_id,
        "username": username,
        "role": role,
    }
    if extra:
        snapshot.update(extra)
    return snapshot
