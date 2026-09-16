"""
업로드 파일 정리 CLI (§3-8). 크론에서 실행 가능.

사용:
    python sql/cleanup.py --orphan            고아 파일만
    python sql/cleanup.py --old 30            30일 이전 파일만 (경로 NULL 갱신)
    python sql/cleanup.py --orphan --old 30   둘 다
    python sql/cleanup.py --status            상태만 출력 (변경 없음)
"""

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


from app import db as app_db


app_db.ensure_ld_library_path()


from app.services import cleanup as cleanup_service


def main():
    parser = argparse.ArgumentParser(description="업로드 파일 정리 CLI")
    parser.add_argument("--orphan", action="store_true", help="고아 파일 정리")
    parser.add_argument("--old", type=int, metavar="DAYS", help="DAYS 이전 검사 파일 정리")
    parser.add_argument("--status", action="store_true", help="상태만 출력")
    args = parser.parse_args()

    if not (args.orphan or args.old or args.status):
        parser.print_help()
        sys.exit(1)

    if args.status:
        status = cleanup_service.storage_status()
        print("== uploads 상태")
        print("   파일 수:         {}".format(status["file_count"]))
        print("   총 용량:         {:,} bytes ({:.1f} MB)".format(
            status["total_bytes"], status["total_bytes"] / 1024 / 1024))
        print("   DB 참조 basename: {}".format(status["referenced_count"]))
        print("   고아 파일:        {}".format(status["orphan_count"]))
        return

    if args.orphan:
        result = cleanup_service.cleanup_orphan_files()
        print("== 고아 정리")
        print("   스캔 파일:  {}".format(result["scanned"]))
        print("   DB 참조:    {}".format(result["referenced"]))
        print("   제거 파일:  {}".format(result["removed"]))
        print("   확보 용량:  {:,} bytes".format(result["bytes"]))

    if args.old is not None:
        result = cleanup_service.cleanup_old_files(days=args.old)
        print("== 보존 기간 정리 (days={})".format(result["days"]))
        print("   처리 검사:  {}".format(result["processed"]))
        print("   제거 파일:  {}".format(result["removed"]))
        print("   확보 용량:  {:,} bytes".format(result["bytes"]))


if __name__ == "__main__":
    main()
