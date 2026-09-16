"""
§3-4 마이그레이션: INSPECTIONS 에 새 컬럼 4개 추가.

- IS_COLD_START NUMBER(1) DEFAULT 0
- HEATMAP_PATH VARCHAR2(512)
- ANNOTATED_PATH VARCHAR2(512)
- SEG_PATH VARCHAR2(512)

이미 존재하는 컬럼은 ORA-01430 (or ORA-01442 for NOT NULL) 로 SKIP.
schema.sql 은 이번 단계에서 갱신되어 있으므로 새 환경에서는 init_db.py
가 처음부터 이 컬럼들을 만든다. 이 스크립트는 기존 환경의 in-place
업그레이드 전용.

실행: python sql/migrate_3_4.py
"""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


from app import db as app_db


app_db.ensure_ld_library_path()


ALTER_STATEMENTS = [
    ("ADD IS_COLD_START",
     "ALTER TABLE INSPECTIONS ADD (IS_COLD_START NUMBER(1) DEFAULT 0 "
     "CHECK (IS_COLD_START IN (0,1)))"),
    ("ADD HEATMAP_PATH",
     "ALTER TABLE INSPECTIONS ADD (HEATMAP_PATH VARCHAR2(512))"),
    ("ADD ANNOTATED_PATH",
     "ALTER TABLE INSPECTIONS ADD (ANNOTATED_PATH VARCHAR2(512))"),
    ("ADD SEG_PATH",
     "ALTER TABLE INSPECTIONS ADD (SEG_PATH VARCHAR2(512))"),
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
        # ORA-01430: column being added already exists in table
        # ORA-02260: table can have only one primary key
        # ORA-02275: such a referential constraint already exists
        if code in (1430, 2260, 2275):
            print("  SKIP : {} [{}]".format(label, message))
            return
        print("  FAIL : {} [{}]".format(label, message))
        raise


def main():
    engine = app_db.get_engine()
    if engine is None:
        print("DB 엔진 초기화 실패:")
        print("  {}".format(app_db.get_engine_error()))
        sys.exit(1)

    raw = app_db.get_raw_connection()
    try:
        driver_connection = raw.driver_connection
        cursor = driver_connection.cursor()
        for label, statement in ALTER_STATEMENTS:
            run_alter(cursor, label, statement)
        driver_connection.commit()

        # 확인 조회
        cursor.execute(
            "SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH FROM USER_TAB_COLUMNS "
            "WHERE TABLE_NAME = 'INSPECTIONS' ORDER BY COLUMN_ID"
        )
        print()
        print("== INSPECTIONS columns")
        for name, dtype, dlen in cursor.fetchall():
            print("   {:20} {} ({})".format(name, dtype, dlen))
        cursor.close()
    finally:
        raw.close()


if __name__ == "__main__":
    main()
