"""
로깅 설정 (§3-8).

- 파일 핸들러: logs/app.log (RotatingFileHandler, 10 MB × 5 백업)
- 스트림 핸들러: stdout (개발 환경 편의)
- 레벨: .env 의 LOG_LEVEL (기본 INFO)
- 로거 이름 prefix: 'app'

원칙:
- 비밀번호·세션 토큰·해시 절대 로그에 남기지 않는다.
  로그 호출부에서 자체적으로 마스킹 (이 파일은 인프라만 제공).
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_MAX_BYTES = 10 * 1024 * 1024   # 10 MB
_BACKUP_COUNT = 5

_configured = False


def configure_logging():
    """앱당 1 회 초기화. Flask app.logger 와 'app.*' 로거를 세팅한다."""
    global _configured
    if _configured:
        return
    _configured = True

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    log_level_str = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    formatter = logging.Formatter(_LOG_FORMAT)

    file_handler = RotatingFileHandler(
        str(LOGS_DIR / "app.log"),
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(log_level)
    stream_handler.setFormatter(formatter)

    # 최상위 'app' 로거만 세팅. 하위 로거는 propagate 로 상속.
    root = logging.getLogger("app")
    root.setLevel(log_level)
    # 중복 방지: 이미 핸들러가 있으면 다시 붙이지 않는다.
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        root.addHandler(file_handler)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
               for h in root.handlers):
        root.addHandler(stream_handler)
    root.propagate = False


def get_logger(name):
    """`app.<module>` 로거 획득. configure_logging 을 자동 호출."""
    configure_logging()
    if not name.startswith("app"):
        name = "app." + name
    return logging.getLogger(name)
