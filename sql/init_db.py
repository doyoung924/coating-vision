"""
Oracle DB 초기화 스크립트 (§3단계 3-1).

역할:
1. sql/schema.sql 을 파싱해 각 DDL statement 실행.
   이미 존재하는 객체(ORA-955 등)는 건너뛰고 SKIP 로그.
2. admin 계정 (username=admin) 이 없으면 생성. 비밀번호는
   werkzeug.security.generate_password_hash 로 해싱.
3. USER_TABLES / USER_SEQUENCES / USER_TRIGGERS / USER_INDEXES 조회 결과 출력.

실행: python sql/init_db.py
"""

import sys
from pathlib import Path


# 프로젝트 루트를 sys.path 에 추가 (app 패키지 임포트용)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


from app import db as app_db


# LD_LIBRARY_PATH 미설정이면 재실행 (Oracle Instant Client). 이 호출 이후 라인은
# 재실행된 프로세스에서만 도달.
app_db.ensure_ld_library_path()


from werkzeug.security import generate_password_hash


SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"
ADMIN_USERNAME = "admin"
# ADMIN_PASSWORD 는 .env 에서 읽는다. 미설정 시 안전한 랜덤값을 생성해 stdout 로 알린다.
import os
import secrets
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()
_ADMIN_PASSWORD_GENERATED = False
if not ADMIN_PASSWORD:
    ADMIN_PASSWORD = secrets.token_urlsafe(12)
    _ADMIN_PASSWORD_GENERATED = True


def split_statements(sql_text):
    """schema.sql 을 '/' 만 있는 줄을 구분자로 statement 로 분할.
    PL/SQL 블록 내부의 END; 세미콜론은 유지된다 (구분자가 '/' 이므로)."""
    statements = []
    buffer = []
    for line in sql_text.split("\n"):
        stripped = line.strip()
        if stripped == "/":
            statement = "\n".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
        else:
            buffer.append(line)
    remaining = "\n".join(buffer).strip()
    if remaining:
        statements.append(remaining)
    return statements


def _first_line(statement):
    for line in statement.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return stripped[:60]
    return statement[:60]


def run_statement(cursor, statement):
    """실행. 이미 존재하는 객체 관련 오류는 SKIP 로그로 처리."""
    import oracledb
    label = _first_line(statement)
    try:
        cursor.execute(statement)
        print("  OK   : {}".format(label))
        return True
    except oracledb.DatabaseError as exc:
        error_obj = exc.args[0]
        code = getattr(error_obj, "code", None)
        message = str(error_obj).split("\n")[0]
        # ORA-00955: name is already used by an existing object
        # ORA-01408: such column list already indexed
        # ORA-02275: such a referential constraint already exists
        # ORA-04081: trigger already exists (CREATE OR REPLACE 이면 안 발생)
        if code in (955, 1408, 2275, 4081):
            print("  SKIP : {} [{}]".format(label, message))
            return True
        print("  FAIL : {} [{}]".format(label, message))
        raise


def apply_schema(connection):
    with open(SCHEMA_SQL, "r", encoding="utf-8") as file_handle:
        sql_text = file_handle.read()
    statements = split_statements(sql_text)
    print("== schema.sql: {} statements 발견".format(len(statements)))
    cursor = connection.cursor()
    for statement in statements:
        run_statement(cursor, statement)
    connection.commit()
    cursor.close()


def ensure_admin(connection):
    """admin 계정을 확보한다.
    - 없으면 생성 (PASSWORD_HASH = generate_password_hash(ADMIN_PASSWORD))
    - 있으면 PASSWORD_HASH 를 ADMIN_PASSWORD 로 재설정 (환경변수 값을 항상 반영).
      IS_ACTIVE=1, ROLE='admin' 도 강제 (계정 잠금 방지)."""
    cursor = connection.cursor()
    password_hash = generate_password_hash(ADMIN_PASSWORD)

    cursor.execute(
        "SELECT ID FROM USERS WHERE USERNAME = :username",
        username=ADMIN_USERNAME,
    )
    row = cursor.fetchone()
    if row is not None:
        admin_id = row[0]
        cursor.execute(
            "UPDATE USERS SET PASSWORD_HASH = :h, ROLE = 'admin', IS_ACTIVE = 1 "
            "WHERE ID = :id",
            h=password_hash, id=admin_id,
        )
        connection.commit()
        print("== admin 계정: 갱신 (ID={}, PASSWORD_HASH 재설정)".format(admin_id))
    else:
        cursor.execute(
            "INSERT INTO USERS (USERNAME, PASSWORD_HASH, FULL_NAME, ROLE) "
            "VALUES (:username, :password_hash, :full_name, :role)",
            username=ADMIN_USERNAME,
            password_hash=password_hash,
            full_name="관리자",
            role="admin",
        )
        connection.commit()
        cursor.execute(
            "SELECT ID FROM USERS WHERE USERNAME = :username",
            username=ADMIN_USERNAME,
        )
        admin_id = cursor.fetchone()[0]
        print("== admin 계정 생성 (ID={})".format(admin_id))
    cursor.close()

    if _ADMIN_PASSWORD_GENERATED:
        print()
        print("!!! ADMIN_PASSWORD 환경변수 미설정 !!!")
        print("    임시 생성한 비밀번호: {}".format(ADMIN_PASSWORD))
        print("    이 값을 .env 의 ADMIN_PASSWORD 에 저장하고 재실행하면")
        print("    다음 실행부터 이 값을 유지한다.")
    else:
        print("== admin 비밀번호는 .env 의 ADMIN_PASSWORD 를 따른다")


def inspect_objects(connection):
    cursor = connection.cursor()
    print()
    print("== USER_TABLES")
    cursor.execute("SELECT TABLE_NAME FROM USER_TABLES ORDER BY TABLE_NAME")
    for (name,) in cursor.fetchall():
        print("   {}".format(name))
    print()
    print("== USER_SEQUENCES")
    cursor.execute("SELECT SEQUENCE_NAME FROM USER_SEQUENCES ORDER BY SEQUENCE_NAME")
    for (name,) in cursor.fetchall():
        print("   {}".format(name))
    print()
    print("== USER_TRIGGERS")
    cursor.execute("SELECT TRIGGER_NAME FROM USER_TRIGGERS ORDER BY TRIGGER_NAME")
    for (name,) in cursor.fetchall():
        print("   {}".format(name))
    print()
    print("== USER_INDEXES (사용자 명명한 것만)")
    cursor.execute(
        "SELECT INDEX_NAME, TABLE_NAME FROM USER_INDEXES "
        "WHERE INDEX_NAME LIKE 'IDX_%' "
        "ORDER BY TABLE_NAME, INDEX_NAME"
    )
    for name, table in cursor.fetchall():
        print("   {} on {}".format(name, table))
    cursor.close()


def main():
    # 엔진 초기화 시도. 실패 시 진단 후 종료.
    engine = app_db.get_engine()
    if engine is None:
        print("DB 엔진 초기화 실패:")
        print("  {}".format(app_db.get_engine_error()))
        sys.exit(1)

    raw = app_db.get_raw_connection()
    try:
        # sqlalchemy pool wrapper. driver_connection 이 실제 oracledb.Connection.
        driver_connection = raw.driver_connection
        apply_schema(driver_connection)
        ensure_admin(driver_connection)
        inspect_objects(driver_connection)
    finally:
        raw.close()


if __name__ == "__main__":
    main()
