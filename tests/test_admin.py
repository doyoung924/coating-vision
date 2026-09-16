"""관리자 계정·저장소 관리 (§3-10)."""
import os
import uuid
from pathlib import Path

import pytest

from app import config as app_config
from app import db as app_db
from app.models import User
from app.services import admin as admin_service
from app.services import cleanup as cleanup_service


UID = lambda: uuid.uuid4().hex[:8]


class TestRoleChange:

    def test_change_role_promote_demote(self, admin_user, inspector_user):
        # inspector → manager
        result = admin_service.change_role(
            actor_id=admin_user["id"], target_id=inspector_user["id"], new_role="manager",
        )
        assert result["changed"]
        assert result["new_role"] == "manager"

        with app_db.get_session() as s:
            u = s.query(User).filter(User.id == inspector_user["id"]).one()
            assert u.role == "manager"

        # 되돌리기
        admin_service.change_role(
            actor_id=admin_user["id"], target_id=inspector_user["id"], new_role="inspector",
        )

    def test_self_downgrade_blocked(self, admin_user):
        # admin 이 자기 자신을 manager 로 강등 시도
        with pytest.raises(admin_service.AdminError):
            admin_service.change_role(
                actor_id=admin_user["id"], target_id=admin_user["id"], new_role="manager",
            )

        # DB 확인: 여전히 admin
        with app_db.get_session() as s:
            u = s.query(User).filter(User.id == admin_user["id"]).one()
            assert u.role == "admin"


class TestToggleActive:

    def test_self_deactivate_blocked(self, admin_user):
        with pytest.raises(admin_service.AdminError):
            admin_service.toggle_active(
                actor_id=admin_user["id"], target_id=admin_user["id"],
            )

    def test_toggle_other(self, admin_user, inspector_user):
        # inspector 비활성화
        result = admin_service.toggle_active(
            actor_id=admin_user["id"], target_id=inspector_user["id"],
        )
        assert result["old_active"] == 1
        assert result["new_active"] == 0

        # 재활성화
        admin_service.toggle_active(
            actor_id=admin_user["id"], target_id=inspector_user["id"],
        )


class TestLastAdminSafety:
    """안전장치 3: 마지막 활성 admin 강등·비활성화 금지."""

    def test_last_active_admin_downgrade_blocked(self, admin_user):
        """admin_user fixture 하나만 존재하는 상황을 시뮬레이션하기 위해
        기존 admin(id=1) 을 잠시 admin 이 아닌 것으로 낮췄다가 복원."""
        # 기존 admin(id=1) 을 잠시 manager 로. 그러면 활성 admin = admin_user 하나만.
        with app_db.get_session() as s:
            u = s.query(User).filter(User.id == 1).one()
            original_role = u.role
            u.role = "manager"

        try:
            # 지금 활성 admin 은 admin_user 하나. 다른 admin(actor_id=99, 가공) 이
            # admin_user 를 강등 시도 → 안전장치 3 발동
            with pytest.raises(admin_service.AdminError):
                admin_service.change_role(
                    actor_id=99, target_id=admin_user["id"], new_role="manager",
                )
            # toggle 도 마찬가지
            with pytest.raises(admin_service.AdminError):
                admin_service.toggle_active(
                    actor_id=99, target_id=admin_user["id"],
                )
        finally:
            # 원상 복구
            with app_db.get_session() as s:
                u = s.query(User).filter(User.id == 1).one()
                u.role = original_role


class TestPasswordReset:

    def test_reset_generates_new_password(self, admin_user, inspector_user):
        old_password = inspector_user["password"]
        result = admin_service.reset_password(
            actor_id=admin_user["id"], target_id=inspector_user["id"],
        )
        assert "new_password" in result
        new_password = result["new_password"]
        assert new_password != old_password
        assert len(new_password) >= 10   # secrets.token_urlsafe(9) ≥ 12

        # 새 비밀번호로 로그인 성공, 옛 비밀번호 실패
        c = admin_user["client"].application.test_client()
        resp = c.post("/login", data={"username": inspector_user["username"], "password": new_password})
        assert resp.status_code == 302

        c2 = admin_user["client"].application.test_client()
        resp = c2.post("/login", data={"username": inspector_user["username"], "password": old_password})
        assert resp.status_code == 401


class TestCleanupPathTraversal:
    """경로 탈출 방어: uploads 밖 경로는 절대 삭제되지 않아야 한다."""

    def test_traversal_refused(self, tmp_path, monkeypatch):
        # uploads 밖에 파일 하나 만들고 basename 에 ../ 를 주는 시나리오
        outside = tmp_path / "outside.txt"
        outside.write_text("do not delete me")
        assert outside.exists()

        # basename 에 traversal 시도 (uploads/../../tmp/pytest-xxx/outside.txt 형태)
        # _safe_delete 는 basename 만 받고 resolve 후 UPLOADS_DIR 안 확인.
        # 상대 경로 traversal 시도: "../../../{tmp_path}/outside.txt"
        rel = "../" * 20 + str(outside).lstrip("/")
        removed, size = cleanup_service._safe_delete(rel)
        assert removed is False
        assert outside.exists()  # 원본 파일 유지

    def test_gitkeep_protected(self):
        # .gitkeep 은 PROTECTED_NAMES 에 포함
        removed, size = cleanup_service._safe_delete(".gitkeep")
        assert removed is False


class TestStorageCleanup:

    def test_orphan_cleanup(self, admin_user):
        # uploads 에 임시 파일 하나 생성 (DB 참조 없음)
        uploads = app_config.UPLOADS_DIR
        orphan = uploads / ("pytest_orphan_" + UID() + ".png")
        orphan.write_bytes(b"\x00" * 100)  # 100 bytes
        assert orphan.exists()

        # 정리
        result = cleanup_service.cleanup_orphan_files()
        assert result["removed"] >= 1
        assert not orphan.exists()

    def test_old_cleanup_nulls_paths(self, admin_user, inspector_user, fake_inspection_result):
        from app.services import inspection_store
        from datetime import datetime, timedelta

        insp_id = inspection_store.save_inspection(
            result=fake_inspection_result, user_id=inspector_user["id"],
            file_name="old_test.jpg", source="upload", is_cold_start=False,
        )
        # created_at 을 40일 전으로 조작
        from app.models import Inspection
        with app_db.get_session() as s:
            row = s.query(Inspection).filter(Inspection.id == insp_id).one()
            row.created_at = datetime.utcnow() - timedelta(days=40)

        result = cleanup_service.cleanup_old_files(days=30)
        assert result["processed"] >= 1

        # 경로 컬럼이 NULL 이어야 (fake result 에는 실제 파일이 없지만 DB 로직 검증)
        with app_db.get_session() as s:
            row = s.query(Inspection).filter(Inspection.id == insp_id).one()
            assert row.image_path is None
            assert row.heatmap_path is None
