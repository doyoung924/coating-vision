"""
§3-6 마이그레이션: SPC_POINTS.METRIC CHECK 제약에 계수형 지표 추가.

기존: METRIC IN ('a3','seg_crack')
변경: METRIC IN ('a3','seg_crack','pinhole_count')

배경: FR-57 (계수형 지표 지원). 설계 문서 docs/design-count-control-chart.md §4·5.
`pinhole_count` 는 프레임당 YOLO 박스 수 (정수). SPC_POINTS.VALUE 는 이미
NUMBER 라 값 저장 컬럼 변경 없음. CHECK 목록만 확장.

CHECK 이름 처리:
  기존 CHECK 는 init_db 시 익명 생성돼 SYS_C0077xx 등 auto-name 이 붙는다.
  환경마다 번호가 달라 하드코딩 불가 → USER_CONSTRAINTS 에서 SEARCH_CONDITION
  으로 조회한 뒤 DROP. ADD 시에는 명시 이름 `CHK_SPC_METRIC` 를 지정해
  향후 마이그레이션이 결정론적으로 찾을 수 있게 한다.

기존 데이터: SPC_POINTS 0 건 (3-6 시점 실측). CHECK DROP → ADD 사이의 짧은
공백 구간에 잘못된 값 INSERT 우려 없음.

롤백:
  python sql/migrate_3_6.py --rollback
  기존 이름 `CHK_SPC_METRIC` 을 찾아 DROP 후 익명 CHECK 로 복원 (원래 상태).
  ⚠️ 롤백 시점에 이미 pinhole_count 값이 들어와 있으면 새 CHECK 가 즉시 위반돼
     제약 추가가 실패한다. 롤백 전 SPC_POINTS WHERE METRIC='pinhole_count' 를
     비우거나, 롤백 자체를 포기해야 한다.

실행: python sql/migrate_3_6.py
      python sql/migrate_3_6.py --rollback
"""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


from app import db as app_db


app_db.ensure_ld_library_path()


NAMED_CONSTRAINT = "CHK_SPC_METRIC"
FORWARD_CHECK = "METRIC IN ('a3','seg_crack','pinhole_count')"
ROLLBACK_CHECK = "METRIC IN ('a3','seg_crack')"


def find_metric_check(cursor):
    """SPC_POINTS.METRIC CHECK 제약 이름 목록을 SEARCH_CONDITION 으로 조회."""
    cursor.execute(
        "SELECT CONSTRAINT_NAME, SEARCH_CONDITION FROM USER_CONSTRAINTS "
        "WHERE TABLE_NAME='SPC_POINTS' AND CONSTRAINT_TYPE='C'"
    )
    matches = []
    for name, cond in cursor.fetchall():
        cond_str = cond.read() if hasattr(cond, "read") else str(cond)
        upper = cond_str.upper()
        if "METRIC" in upper and " IN " in upper:
            matches.append((name, cond_str.strip()))
    return matches


def print_constraints(cursor, label):
    print(f"== {label}")
    for name, cond in find_metric_check(cursor):
        print(f"   {name}: {cond}")
    print()


def forward(cursor):
    matches = find_metric_check(cursor)
    if not matches:
        print("  FAIL : METRIC CHECK 제약을 찾을 수 없음")
        return
    for name, _ in matches:
        cursor.execute(f"ALTER TABLE SPC_POINTS DROP CONSTRAINT {name}")
        print(f"  OK   : DROP CONSTRAINT {name}")
    stmt = (
        f"ALTER TABLE SPC_POINTS ADD CONSTRAINT {NAMED_CONSTRAINT} "
        f"CHECK ({FORWARD_CHECK})"
    )
    cursor.execute(stmt)
    print(f"  OK   : ADD CONSTRAINT {NAMED_CONSTRAINT} CHECK ({FORWARD_CHECK})")


def rollback(cursor):
    import oracledb
    matches = find_metric_check(cursor)
    if not matches:
        print("  FAIL : METRIC CHECK 제약을 찾을 수 없음")
        return
    # 존재하는 pinhole_count 값 사전 점검
    cursor.execute("SELECT COUNT(*) FROM SPC_POINTS WHERE METRIC='pinhole_count'")
    n = cursor.fetchone()[0]
    if n > 0:
        print(f"  FAIL : SPC_POINTS 에 METRIC='pinhole_count' {n} 건 존재 → 롤백 시 새 CHECK 위반")
        print("         롤백 전 해당 행 정리 필요")
        sys.exit(1)

    for name, _ in matches:
        cursor.execute(f"ALTER TABLE SPC_POINTS DROP CONSTRAINT {name}")
        print(f"  OK   : DROP CONSTRAINT {name}")
    stmt = f"ALTER TABLE SPC_POINTS ADD CHECK ({ROLLBACK_CHECK})"
    cursor.execute(stmt)
    print(f"  OK   : ADD CHECK ({ROLLBACK_CHECK})  [익명, SYS_ 재부여됨]")


def main():
    is_rollback = "--rollback" in sys.argv

    engine = app_db.get_engine()
    if engine is None:
        print("DB 엔진 초기화 실패:", app_db.get_engine_error())
        sys.exit(1)

    raw = app_db.get_raw_connection()
    try:
        cursor = raw.driver_connection.cursor()
        print_constraints(cursor, "적용 전 METRIC CHECK 제약")
        if is_rollback:
            print("== --rollback 모드")
            rollback(cursor)
        else:
            forward(cursor)
        raw.driver_connection.commit()
        print()
        print_constraints(cursor, "적용 후 METRIC CHECK 제약")
        cursor.close()
    finally:
        raw.close()


if __name__ == "__main__":
    main()
