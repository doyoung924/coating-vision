"""
§3-5 마이그레이션: INSPECTIONS 에 REVIEWED_BY, REVIEWED_AT 컬럼 추가.
알람 상태의 검사를 관리자가 조치 완료 처리할 때 기록한다.

실행: python sql/migrate_3_5.py
"""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


from app import db as app_db


app_db.ensure_ld_library_path()


ALTER_STATEMENTS = [
    ("ADD REVIEWED_BY",
     "ALTER TABLE INSPECTIONS ADD (REVIEWED_BY NUMBER)"),
    ("ADD REVIEWED_AT",
     "ALTER TABLE INSPECTIONS ADD (REVIEWED_AT TIMESTAMP)"),
    ("ADD FK_INSPECTIONS_REVIEWED_BY",
     "ALTER TABLE INSPECTIONS ADD CONSTRAINT FK_INSPECTIONS_REVIEWED_BY "
     "FOREIGN KEY (REVIEWED_BY) REFERENCES USERS(ID)"),
]


def run_alter(cursor, label, statement):
    import oracledb
    try:
        cursor.execute(statement)
        print("  OK   : {}".format(label))
    except oracledb.DatabaseError as exc:
        error_obj = exc.args[0]
        code = getattr(error_obj, "code", None)
        message = str(error_obj).split("\n")[0]
        # ORA-01430: column already exists
        # ORA-02275: such a referential constraint already exists
        # ORA-02264: name already used by an existing constraint
        if code in (1430, 2275, 2264):
            print("  SKIP : {} [{}]".format(label, message))
            return
        print("  FAIL : {} [{}]".format(label, message))
        raise


def main():
    engine = app_db.get_engine()
    if engine is None:
        print("DB 엔진 초기화 실패:", app_db.get_engine_error())
        sys.exit(1)

    raw = app_db.get_raw_connection()
    try:
        driver_connection = raw.driver_connection
        cursor = driver_connection.cursor()
        for label, statement in ALTER_STATEMENTS:
            run_alter(cursor, label, statement)
        driver_connection.commit()

        cursor.execute(
            "SELECT COLUMN_NAME, DATA_TYPE FROM USER_TAB_COLUMNS "
            "WHERE TABLE_NAME='INSPECTIONS' "
            "AND COLUMN_NAME IN ('REVIEWED_BY','REVIEWED_AT')"
        )
        print()
        print("== 확인")
        for name, dtype in cursor.fetchall():
            print("   {:15} {}".format(name, dtype))
        cursor.close()
    finally:
        raw.close()


if __name__ == "__main__":
    main()
