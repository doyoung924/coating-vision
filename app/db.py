"""
Oracle DB 연결 계층 (§3단계 3-1).

원칙:
- 임포트 시 DB 접근 없음. 최초 get_engine() 호출 시 lazy 초기화.
- oracledb.init_oracle_client() 는 앱당 1 회만 실행. 이미 초기화됐으면 skip.
- 연결 실패 시 앱이 죽지 않도록 에러를 슬롯 (_engine_error) 에 보관.
  (모델 lazy 로드 패턴과 동일.)
- 커넥션 풀 5+5, pool_pre_ping=True.
- import 방향: db → config. 역참조 금지.

사용:
    from app import db as app_db

    with app_db.get_session() as session:
        result = session.execute(text("SELECT 1 FROM DUAL")).scalar()
"""

import threading
from contextlib import contextmanager

from . import config


_engine = None
_engine_error = None
_engine_lock = threading.Lock()
_client_initialized = False
_session_factory = None


def _ensure_client():
    """python-oracledb thick 모드 초기화. 앱당 1 회.

    Oracle Instant Client 의 shared library 들은 rpath 없이 서로를 참조하며
    (libnnz ↔ libclntshcore ↔ libclntsh) 순환이 있어, 프로세스 시작 후
    ctypes.CDLL 선로딩으로는 해결되지 않는다. 진입점 (run.py, sql/init_db.py)
    에서 `_ensure_ld_library_path()` 로 LD_LIBRARY_PATH 를 설정하고
    execvpe 로 자기 자신을 재실행하는 방식으로 미리 처리한다.
    """
    global _client_initialized
    if _client_initialized:
        return
    import oracledb
    try:
        oracledb.init_oracle_client(lib_dir=config.ORACLE_CLIENT_LIB)
    except oracledb.ProgrammingError as exc:
        # "Oracle Client library has already been initialized" 는 정상 skip.
        if "already been initialized" not in str(exc):
            raise
    _client_initialized = True


def ensure_ld_library_path():
    """진입점 최상단에서 호출. LD_LIBRARY_PATH 에 Instant Client 가 없으면
    os.execvpe 로 자기 자신을 재실행한다 (같은 pid 유지, 무한 루프 없음).

    Instant Client 의 libnnz19.so ↔ libclntshcore.so.19.1 ↔ libclntsh.so.19.1
    순환 의존 때문에 rpath 없는 dlopen 은 실패한다. LD_LIBRARY_PATH 로
    사전 지정하는 것이 유일하게 안정적인 방법.

    이 함수는 config 를 임포트한다. import app.db 만으로는 실행되지 않음 —
    진입점에서 명시적으로 호출해야 한다.
    """
    import os
    import sys

    lib_dir = config.ORACLE_CLIENT_LIB
    if not lib_dir:
        return
    current = os.environ.get("LD_LIBRARY_PATH", "")
    if lib_dir in current.split(":"):
        return
    new_env = os.environ.copy()
    new_env["LD_LIBRARY_PATH"] = (
        lib_dir + (":" + current if current else "")
    )
    # sys.orig_argv 는 python 실행 시 원래 argv 전체 (`-c "..."`, `-m ...`, script.py ...).
    # sys.argv 만 쓰면 -c 로 실행된 경우 명령 문자열이 사라져 재실행이 깨진다.
    original_argv = getattr(sys, "orig_argv", None)
    if original_argv:
        exec_argv = list(original_argv)
    else:
        exec_argv = [sys.executable] + sys.argv
    os.execvpe(exec_argv[0], exec_argv, new_env)


def get_engine():
    """Lazy 엔진 반환. 실패 시 None (에러는 get_engine_error())."""
    global _engine, _engine_error
    if _engine is not None or _engine_error is not None:
        return _engine
    with _engine_lock:
        if _engine is not None or _engine_error is not None:
            return _engine
        try:
            _ensure_client()
            from sqlalchemy import create_engine
            url = "oracle+oracledb://{user}:{password}@{dsn}".format(
                user=config.ORACLE_USER,
                password=config.ORACLE_PASSWORD,
                dsn=config.get_oracle_dsn(),
            )
            _engine = create_engine(
                url,
                pool_size=5,
                max_overflow=5,
                pool_pre_ping=True,
            )
        except Exception as exc:
            _engine_error = "DB 엔진 초기화 실패: {}: {}".format(
                type(exc).__name__, exc,
            )
    return _engine


def get_engine_error():
    return _engine_error


def get_session_factory():
    """Lazy sessionmaker. 엔진 없으면 None."""
    global _session_factory
    if _session_factory is not None:
        return _session_factory
    engine = get_engine()
    if engine is None:
        return None
    from sqlalchemy.orm import sessionmaker
    _session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    return _session_factory


@contextmanager
def get_session():
    """context manager. 사용 후 자동 commit / rollback / close."""
    factory = get_session_factory()
    if factory is None:
        raise RuntimeError(get_engine_error() or "DB 엔진 없음 (초기화 실패)")
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_raw_connection():
    """oracledb 커서를 직접 쓰고 싶은 경우 (예: DDL 실행) 원시 커넥션 반환.
    호출자가 close() 책임."""
    engine = get_engine()
    if engine is None:
        raise RuntimeError(get_engine_error() or "DB 엔진 없음 (초기화 실패)")
    return engine.raw_connection()
