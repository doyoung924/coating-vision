"""회원가입 · 로그인 · 로그아웃 · 비밀번호 변경 · 프로필 (§3-10)."""
import uuid

import pytest

from app import db as app_db
from app.models import User
from app.services import auth as auth_service


UID = lambda: uuid.uuid4().hex[:8]


class TestRegister:

    def test_register_success(self, client):
        username = "pytest_reg_" + UID()
        resp = client.post("/register", data={
            "username": username,
            "password": "GoodPw12345",
            "password_confirm": "GoodPw12345",
            "email": "{}@example.com".format(username),
            "full_name": "Test",
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

        # 정리
        with app_db.get_session() as s:
            s.query(User).filter(User.username == username).delete()

    def test_register_duplicate_username(self, client, inspector_user):
        # 기존 inspector_user 의 username 으로 회원가입 시도 → 400 + flash
        resp = client.post("/register", data={
            "username": inspector_user["username"],
            "password": "AnyPw12345",
            "password_confirm": "AnyPw12345",
        })
        assert resp.status_code == 400
        assert "이미 사용 중" in resp.get_data(as_text=True)

    def test_register_duplicate_email(self, client, inspector_user):
        email = "pytest_dupe_{}@example.com".format(UID())
        # inspector_user 계정에 이메일 세팅
        with app_db.get_session() as s:
            s.query(User).filter(User.id == inspector_user["id"]).update({"email": email})

        # 신규 계정에 같은 이메일 → 400
        username = "pytest_reg2_" + UID()
        resp = client.post("/register", data={
            "username": username,
            "password": "GoodPw12345",
            "password_confirm": "GoodPw12345",
            "email": email,
        })
        assert resp.status_code == 400
        assert "이메일" in resp.get_data(as_text=True)

    def test_register_short_password(self, client):
        username = "pytest_short_" + UID()
        resp = client.post("/register", data={
            "username": username,
            "password": "short12",   # 7자
            "password_confirm": "short12",
        })
        assert resp.status_code == 400
        assert "8자" in resp.get_data(as_text=True)

    def test_register_password_mismatch(self, client):
        username = "pytest_mismatch_" + UID()
        resp = client.post("/register", data={
            "username": username,
            "password": "GoodPw12345",
            "password_confirm": "Different99!",
        })
        assert resp.status_code == 400
        assert "일치" in resp.get_data(as_text=True)


class TestLogin:

    def test_login_success(self, client, inspector_user):
        # inspector_user fixture 는 이미 로그인된 client 를 갖지만
        # 여기서는 새 client 로 자격 검증
        resp = client.post("/login", data={
            "username": inspector_user["username"],
            "password": inspector_user["password"],
        })
        assert resp.status_code == 302

    def test_login_bad_password(self, client, inspector_user):
        resp = client.post("/login", data={
            "username": inspector_user["username"],
            "password": "WRONG_PASS",
        })
        assert resp.status_code == 401

    def test_login_unknown_user(self, client):
        resp = client.post("/login", data={
            "username": "pytest_ghost_" + UID(),
            "password": "AnyPw12345",
        })
        assert resp.status_code == 401

    def test_login_inactive(self, client, inspector_user):
        # is_active=0 으로 만들고 로그인 시도
        with app_db.get_session() as s:
            s.query(User).filter(User.id == inspector_user["id"]).update({"is_active": 0})
        resp = client.post("/login", data={
            "username": inspector_user["username"],
            "password": inspector_user["password"],
        })
        assert resp.status_code == 401


class TestLogout:

    def test_logout_clears_session(self, inspector_user):
        c = inspector_user["client"]
        # 로그인 상태에서 / 접근 가능
        resp = c.get("/")
        assert resp.status_code == 200
        # 로그아웃
        resp = c.get("/logout", follow_redirects=False)
        assert resp.status_code == 302
        # 세션 비었는지: / 접근 시 302 → /login
        resp = c.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


class TestPasswordChange:

    def test_password_change_success(self, inspector_user):
        c = inspector_user["client"]
        new_pw = "NewInspector2026"
        resp = c.post("/account/password", data={
            "current_password": inspector_user["password"],
            "new_password": new_pw,
            "new_password_confirm": new_pw,
        }, follow_redirects=False)
        assert resp.status_code == 302

        # 세션 유지 확인
        resp = c.get("/account")
        assert resp.status_code == 200

        # 옛 비번 로그인 실패
        c2 = c.application.test_client()
        resp = c2.post("/login", data={
            "username": inspector_user["username"],
            "password": inspector_user["password"],
        })
        assert resp.status_code == 401

        # 새 비번 로그인 성공
        resp = c2.post("/login", data={
            "username": inspector_user["username"],
            "password": new_pw,
        })
        assert resp.status_code == 302

    def test_password_change_wrong_current(self, inspector_user):
        c = inspector_user["client"]
        resp = c.post("/account/password", data={
            "current_password": "WRONG_PASS_xxx",
            "new_password": "NewGoodPw2026",
            "new_password_confirm": "NewGoodPw2026",
        })
        assert resp.status_code == 400
        assert "현재 비밀번호" in resp.get_data(as_text=True)

    def test_password_change_short_new(self, inspector_user):
        c = inspector_user["client"]
        resp = c.post("/account/password", data={
            "current_password": inspector_user["password"],
            "new_password": "Short12",     # 7 자
            "new_password_confirm": "Short12",
        })
        assert resp.status_code == 400
        assert "8자" in resp.get_data(as_text=True)


class TestProfile:

    def test_profile_update_success(self, inspector_user):
        c = inspector_user["client"]
        email = "pytest_prof_{}@example.com".format(UID())
        resp = c.post("/account", data={
            "full_name": "테스트 이름",
            "email": email,
        }, follow_redirects=False)
        assert resp.status_code == 302

        with app_db.get_session() as s:
            u = s.query(User).filter(User.id == inspector_user["id"]).one()
            assert u.full_name == "테스트 이름"
            assert u.email == email

    def test_profile_update_email_duplicate(self, inspector_user, manager_user):
        # manager_user 에 이메일 세팅
        email = "pytest_dup_{}@example.com".format(UID())
        with app_db.get_session() as s:
            s.query(User).filter(User.id == manager_user["id"]).update({"email": email})

        # inspector_user 가 같은 이메일 시도 → 400
        resp = inspector_user["client"].post("/account", data={
            "full_name": "",
            "email": email,
        })
        assert resp.status_code == 400
        assert "이메일" in resp.get_data(as_text=True)
