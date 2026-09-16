"""
경로 상수 + Flask 설정 + §3단계 3-1 DB 설정.

원칙:
- 임포트 시 파일·네트워크 접근 없음.
- WSL 호스트 IP 자동 감지 (`_detect_wsl_host_ip`) 는 `get_oracle_host()` 가
  최초 호출될 때만 실행 (lazy). 앱 시작 시간에 영향 없음.
- .env 로드는 config 임포트 시 1 회 수행 (매우 짧음, IO 미미).
- 기존 경로 상수 (§2단계 (d)) 는 손대지 않는다.
"""

import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv


APP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = APP_ROOT.parent

# .env 로드 (프로젝트 루트). 파일 없으면 조용히 skip.
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")


# ============================================================
# 경로 상수 (§2단계 (d) — 유지)
# ============================================================

# 프로젝트 루트 산출물 (오프라인 스크립트 공유)
FIGURES_DIR = PROJECT_ROOT / "figures"
IMAGE_DIR = PROJECT_ROOT / "segmentation" / "images"
STREAM_DIR = PROJECT_ROOT / "stream_data"
STREAM_INDEX = STREAM_DIR / "index.json"
YOLO_WEIGHTS = PROJECT_ROOT / "runs" / "pinhole_v1" / "weights" / "best.pt"
SEG_WEIGHTS = PROJECT_ROOT / "runs" / "semantic" / "seed0" / "best.pt"

# app 패키지 내부 (templates/·static/ 이동 후)
STATIC_DIR = APP_ROOT / "static"
UPLOADS_DIR = STATIC_DIR / "uploads"
SAMPLES_DIR = STATIC_DIR / "samples"

# 업로드 폴더 보장.
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# Flask 설정 상수
# ============================================================

MAX_CONTENT_LENGTH = 8 * 1024 * 1024
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-do-not-use-in-production")


# ============================================================
# DB 설정 (§3단계 3-1)
# ============================================================

ORACLE_USER = os.environ.get("ORACLE_USER", "scott")
ORACLE_PASSWORD = os.environ.get("ORACLE_PASSWORD", "tiger")
ORACLE_PORT = os.environ.get("ORACLE_PORT", "1521")
ORACLE_SERVICE = os.environ.get("ORACLE_SERVICE", "XE")
ORACLE_CLIENT_LIB = os.environ.get(
    "ORACLE_CLIENT_LIB",
    str(Path.home() / "oracle" / "instantclient_19_32"),
)


def _detect_wsl_host_ip():
    """`ip route show | grep default` 로 WSL 호스트 IP 자동 감지.
    실패 시 None. 재부팅으로 IP 가 바뀌어도 즉시 반영."""
    try:
        result = subprocess.run(
            ["ip", "route", "show"],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.split("\n"):
        if line.startswith("default"):
            parts = line.split()
            if len(parts) >= 3:
                return parts[2]
    return None


def get_oracle_host():
    """.env 의 ORACLE_HOST 우선. 비어 있으면 WSL 호스트 IP 자동 감지."""
    env_host = os.environ.get("ORACLE_HOST", "").strip()
    if env_host:
        return env_host
    detected = _detect_wsl_host_ip()
    if detected:
        return detected
    return "127.0.0.1"


def get_oracle_dsn():
    """Oracle Easy Connect DSN: host:port/service_name."""
    return "{}:{}/{}".format(get_oracle_host(), ORACLE_PORT, ORACLE_SERVICE)
