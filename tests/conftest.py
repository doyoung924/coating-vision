"""
pytest 공통 fixture (§3-10).

DB 격리 전략: (a) 실제 Oracle 에 테스트 전용 데이터 + finalizer 로 삭제.
근거:
- 기존 Oracle 스키마 그대로 사용해 실제 DDL·CHECK·CASCADE·SEQUENCE·TRIGGER 동작 검증
  (테스트 전용 SQLite 등 다른 엔진은 Oracle 11g 특유 규약 검증 못 함)
- 테스트 사용자·게시글·검사는 UUID 접미사로 고유하게 생성해 기존 계정
  (admin/bob/manager1) 과 충돌 없음
- 각 fixture 의 finalizer 가 자신이 만든 데이터를 명시적으로 삭제
- 기존 데이터(admin/bob/manager1 + 그들의 검사·글) 는 절대 건드리지 않는다.
  삭제·수정 대상은 pytest_ 접두사 사용자와 그 소유 데이터로 한정
"""

import os
import uuid
from pathlib import Path

import pytest

# LD_LIBRARY_PATH 세팅 (Oracle Instant Client). pytest 프로세스에서
# ensure_ld_library_path 를 부르면 execvpe 로 재실행되어 pytest 컨텍스트를
# 잃으므로, pytest 실행 전에 shell 에서 export 되어 있어야 한다.
# conftest 는 확인만.
#
# 로컬 Oracle IC 경로는 사용자마다 다르므로 환경변수로 받는다.
# .env.example 의 ORACLE_CLIENT_LIB 참조.
_LIB_DIR = os.environ.get("ORACLE_CLIENT_LIB", "").strip()
if not _LIB_DIR:
    raise RuntimeError(
        "ORACLE_CLIENT_LIB 환경변수를 설정하세요 (예: /opt/oracle/instantclient_19_32).\n"
        ".env.example 을 복사해 .env 로 만들고 실 경로를 채우거나, "
        "shell 에서 export ORACLE_CLIENT_LIB=<경로> 하세요."
    )
if _LIB_DIR not in os.environ.get("LD_LIBRARY_PATH", ""):
    raise RuntimeError(
        "LD_LIBRARY_PATH 에 {} 가 없다. pytest 실행 전에 export 필요:\n"
        "    export LD_LIBRARY_PATH=$ORACLE_CLIENT_LIB\n"
        "    pytest".format(_LIB_DIR)
    )


from werkzeug.security import generate_password_hash

from app import create_app
from app import db as app_db
from app.models import Comment, Detection, Finding, Inspection, Post, SpcPoint, User


# 테스트 사용자 접두사. 기존 admin/bob/manager1 과 겹치지 않는다.
TEST_USER_PREFIX = "pytest_"
# 각 테스트 사용자에 붙일 랜덤 접미사 자릿수
UID_LEN = 8


@pytest.fixture(scope="session")
def app():
    """Flask app fixture. 세션 전체에서 1회 생성."""
    application = create_app()
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    return application


@pytest.fixture()
def client(app):
    """빈 client (익명)."""
    return app.test_client()


# ============================================================
# 사용자 fixture — 각 role 별로 로그인된 client
# ============================================================

def _make_test_user(role, password):
    """DB 에 테스트용 사용자 생성 후 (user_id, username, password) 반환.
    호출자가 finalizer 로 삭제해야 한다."""
    suffix = uuid.uuid4().hex[:UID_LEN]
    username = "{}{}_{}".format(TEST_USER_PREFIX, role, suffix)
    password_hash = generate_password_hash(password)
    with app_db.get_session() as sqlalchemy_session:
        user = User(
            username=username,
            password_hash=password_hash,
            role=role,
            is_active=1,
            full_name="pytest {}".format(role),
        )
        sqlalchemy_session.add(user)
        sqlalchemy_session.flush()
        user_id = int(user.id)
    return user_id, username, password


def _delete_test_user(user_id):
    """user_id 소유 검사·글·댓글까지 CASCADE 정리 후 계정 삭제.
    admin/bob/manager1 은 절대 건드리지 않는다 (user_id 로만 판단)."""
    with app_db.get_session() as sqlalchemy_session:
        # 관련 댓글 (자기가 작성한 것)
        sqlalchemy_session.query(Comment).filter(Comment.user_id == user_id).delete()
        # 자기 게시글 (POSTS CASCADE 로 그 안 댓글도 삭제됨)
        sqlalchemy_session.query(Post).filter(Post.user_id == user_id).delete()
        # 자기 검사 (INSPECTIONS CASCADE 로 DETECTIONS·SPC_POINTS·FINDINGS 삭제)
        # SPC_POINTS 는 inspection_id FK 로 CASCADE.
        sqlalchemy_session.query(Inspection).filter(Inspection.user_id == user_id).delete()
        # reviewed_by 참조 정리 (외래키가 유지되도록 NULL 로)
        sqlalchemy_session.query(Inspection).filter(
            Inspection.reviewed_by == user_id
        ).update({"reviewed_by": None, "reviewed_at": None})
        # 계정 삭제
        sqlalchemy_session.query(User).filter(User.id == user_id).delete()


def _login_client(app, username, password):
    """새 test_client 로 로그인. 세션 쿠키 유지."""
    client = app.test_client()
    client.post("/login", data={"username": username, "password": password})
    return client


@pytest.fixture()
def inspector_user(app):
    """inspector role 사용자 fixture. (user_id, username, password, client) 반환."""
    password = "test-pw-inspector-1234"
    user_id, username, _ = _make_test_user("inspector", password)
    client = _login_client(app, username, password)
    yield {
        "id": user_id, "username": username, "password": password,
        "role": "inspector", "client": client,
    }
    _delete_test_user(user_id)


@pytest.fixture()
def manager_user(app):
    password = "test-pw-manager-1234"
    user_id, username, _ = _make_test_user("manager", password)
    client = _login_client(app, username, password)
    yield {
        "id": user_id, "username": username, "password": password,
        "role": "manager", "client": client,
    }
    _delete_test_user(user_id)


@pytest.fixture()
def admin_user(app):
    """admin role 사용자 fixture. 마지막 admin 안전장치 검증 시 필요하므로
    항상 활성 admin >= 2 상태를 만든다 (기존 admin 계정과 이 fixture)."""
    password = "test-pw-admin-1234"
    user_id, username, _ = _make_test_user("admin", password)
    client = _login_client(app, username, password)
    yield {
        "id": user_id, "username": username, "password": password,
        "role": "admin", "client": client,
    }
    _delete_test_user(user_id)


@pytest.fixture()
def anon_client(app):
    return app.test_client()


# ============================================================
# 데이터 헬퍼
# ============================================================

@pytest.fixture()
def fake_inspection_result():
    """/inspect 파이프라인 결과 fake dict. 모델 로드·추론 없이 save_inspection 테스트용.
    각 테스트가 이 dict 를 그대로 쓰거나 필드를 오버라이드."""
    return {
        "orig_url": "/static/uploads/pytest_orig.png",
        "anno_url": "/static/uploads/pytest_anno.png",
        "heat_url": "/static/uploads/pytest_heat.png",
        "seg_url": "/static/uploads/pytest_seg.png",
        "file_names": {
            "orig": "pytest_orig.png",
            "annotated": "pytest_anno.png",
            "heatmap": "pytest_heat.png",
            "seg": "pytest_seg.png",
        },
        "image_size": {"w": 480, "h": 640},
        "anomaly": {
            "total_cells": 70,
            "anomalous_cells": 36,
            "ratio": 0.5143,
            "threshold": 3.0,
            "elapsed_ms_per_patch": 8.0,
            "per_cell_ms": 0.11,
        },
        "detect": {
            "n_detections": 1,
            "detections": [{
                "index": 1, "x1": 100, "y1": 200, "x2": 130, "y2": 240,
                "w": 30, "h": 40, "conf": 0.72, "aspect": 1.33,
            }],
            "infer_ms": 50.0,
            "pre_post_ms": 5.0,
            "total_ms": 55.0,
        },
        "seg": {
            "available": True,
            "elapsed_ms": 985.0,
            "crack_ratio": 0.0301,
            "delam_ratio": 0.0000,
            "crack_pixels": 9247,
            "delam_pixels": 0,
        },
        "shape": {
            "per_detection": [{"index": 1, "aspect": 1.33, "roundness": 0.71}],
            "median_aspect": 1.33,
            "median_roundness": 0.71,
            "elapsed_ms": 1.0,
            "reference": {"description": "", "threshold_caveat": "", "rules": []},
        },
        "advisor": {
            "observation": {
                "anomaly_ratio": 0.5143,
                "anomaly_cells": "36/70",
                "patch_threshold": 3.0,
                "n_detections": 1,
                "median_aspect": 1.33,
                "median_roundness": 0.71,
            },
            "interpretations": [
                {"confidence": "high", "message": "test interpretation"},
            ],
            "references": [
                {"topic": "test topic", "lines": ["- line 1", "- line 2"]},
            ],
        },
    }


@pytest.fixture()
def cleanup_spc():
    """SPC_POINTS 는 metric 별로 SEQ_NO 가 이어지므로 테스트가 만든 점은
    테스트 후 정리해야 baseline 시나리오가 다음 테스트에 영향 없다.
    이 fixture 는 검사 삭제 시 CASCADE 로 SPC_POINTS 도 삭제됨을 활용 —
    별도 처리 불필요하지만 명시적 finalizer 로 남은 orphan 정리."""
    yield
    # 접두사 테스트 검사 소유자가 모두 정리되므로 spc_points 도 CASCADE 로 정리됨
