"""
Flask 진입점.

§3단계 3-1 이후 최상단에서 LD_LIBRARY_PATH 를 설정하고 필요 시 자기
자신을 재실행한다 (Oracle Instant Client shared library 순환 의존).

사용법:
    python run.py
"""

from app import db as app_db


# LD_LIBRARY_PATH 미설정이면 재실행. 이 호출 이후 라인은 재실행된 프로세스에서만 도달.
app_db.ensure_ld_library_path()


from app import create_app


application = create_app()


if __name__ == "__main__":
    application.run(host="0.0.0.0", port=5000, debug=False)
