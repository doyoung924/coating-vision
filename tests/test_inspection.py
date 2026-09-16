"""검사 저장·상세·삭제·CASCADE·CSV export (§3-10).
모델 로드·실제 추론 없이 fake_inspection_result fixture 사용."""
import uuid
from pathlib import Path

import pytest

from app import db as app_db
from app.models import Detection, Finding, Inspection, SpcPoint
from app.services import inspection_store


UID = lambda: uuid.uuid4().hex[:8]


class TestSaveInspection:

    def test_save_creates_rows(self, inspector_user, fake_inspection_result):
        insp_id = inspection_store.save_inspection(
            result=fake_inspection_result,
            user_id=inspector_user["id"],
            file_name="pytest_" + UID() + ".jpg",
            source="upload",
            is_cold_start=False,
        )
        assert isinstance(insp_id, int) and insp_id > 0

        with app_db.get_session() as s:
            insp = s.query(Inspection).filter(Inspection.id == insp_id).one()
            assert insp.a3_ratio is not None
            assert insp.status in ("normal", "alarm")

            n_det = s.query(Detection).filter(Detection.inspection_id == insp_id).count()
            # fake result 는 detection 1개
            assert n_det == 1

            n_fin = s.query(Finding).filter(Finding.inspection_id == insp_id).count()
            # observation 6 + interpretation 1 + reference 1 = 8
            assert n_fin >= 3


class TestPermissions:

    def test_owner_can_view_detail(self, inspector_user, fake_inspection_result):
        insp_id = inspection_store.save_inspection(
            result=fake_inspection_result, user_id=inspector_user["id"],
            file_name="own.jpg", source="upload", is_cold_start=False,
        )
        resp = inspector_user["client"].get("/history/{}".format(insp_id))
        assert resp.status_code == 200

    def test_other_inspector_cannot_view(self, inspector_user, manager_user, fake_inspection_result):
        # inspector 검사 저장
        insp_id = inspection_store.save_inspection(
            result=fake_inspection_result, user_id=inspector_user["id"],
            file_name="other.jpg", source="upload", is_cold_start=False,
        )
        # 다른 inspector (test 안에서는 manager 대신 새 inspector 만들지 않고
        # 서비스로 검증). 여기서는 manager 도 접근 가능하므로 서비스 함수 직접 호출로 검증.
        # 소유자 아닌 inspector 시나리오는 서비스 계층에서만 검증 가능.
        from app.services import inspection_store as store
        detail = store.get_inspection_detail(
            inspection_id=insp_id,
            viewer_user_id=inspector_user["id"] + 99999,  # 존재하지 않는 사용자
            viewer_role="inspector",
        )
        assert detail is None

    def test_manager_can_view_other_users(self, inspector_user, manager_user, fake_inspection_result):
        insp_id = inspection_store.save_inspection(
            result=fake_inspection_result, user_id=inspector_user["id"],
            file_name="viewable_by_manager.jpg", source="upload", is_cold_start=False,
        )
        resp = manager_user["client"].get("/history/{}".format(insp_id))
        assert resp.status_code == 200


class TestCascadeDelete:

    def test_delete_removes_child_rows(self, inspector_user, fake_inspection_result):
        insp_id = inspection_store.save_inspection(
            result=fake_inspection_result, user_id=inspector_user["id"],
            file_name="delete_me.jpg", source="upload", is_cold_start=False,
        )
        # 자식 행 확인
        with app_db.get_session() as s:
            assert s.query(Detection).filter(Detection.inspection_id == insp_id).count() > 0
            assert s.query(Finding).filter(Finding.inspection_id == insp_id).count() > 0

        # 삭제
        ok = inspection_store.delete_inspection(
            inspection_id=insp_id,
            viewer_user_id=inspector_user["id"],
            viewer_role="inspector",
        )
        assert ok

        with app_db.get_session() as s:
            assert s.query(Inspection).filter(Inspection.id == insp_id).count() == 0
            # CASCADE
            assert s.query(Detection).filter(Detection.inspection_id == insp_id).count() == 0
            assert s.query(Finding).filter(Finding.inspection_id == insp_id).count() == 0
            assert s.query(SpcPoint).filter(SpcPoint.inspection_id == insp_id).count() == 0


class TestCSVExport:

    def test_export_bom_and_columns(self, inspector_user, fake_inspection_result):
        # 검사 3건
        for _ in range(3):
            inspection_store.save_inspection(
                result=fake_inspection_result, user_id=inspector_user["id"],
                file_name="exp_" + UID() + ".jpg", source="upload", is_cold_start=False,
            )

        resp = inspector_user["client"].get("/history/export")
        assert resp.status_code == 200
        # BOM 확인
        body = resp.get_data()
        assert body[:3] == b"\xef\xbb\xbf"
        # 헤더 라인 컬럼 수 (17)
        text = body.decode("utf-8-sig")
        lines = text.strip().split("\n")
        header_cols = lines[0].split(",")
        assert len(header_cols) == 17
        # X-Row-Count-Total 헤더
        assert int(resp.headers.get("X-Row-Count-Total", 0)) >= 3
        assert resp.headers.get("X-Row-Truncated") == "false"

    def test_export_no_password_column(self, inspector_user, fake_inspection_result):
        inspection_store.save_inspection(
            result=fake_inspection_result, user_id=inspector_user["id"],
            file_name="pw_check.jpg", source="upload", is_cold_start=False,
        )
        resp = inspector_user["client"].get("/history/export")
        text = resp.get_data(as_text=True).lower()
        assert "password" not in text
        assert "hash" not in text
