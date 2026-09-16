"""
업로드 파일 생명주기 관리 (§3-8).

두 정리 정책:
- cleanup_orphan_files(): DB 참조가 없는 uploads/*.png 파일 삭제
- cleanup_old_files(days): N 일 이전 검사의 이미지 파일 삭제 + 경로 컬럼 NULL 갱신
  (DB 행 자체는 남긴다)

안전:
- 삭제 대상 경로는 반드시 config.UPLOADS_DIR 안이어야 한다 (traversal 방지).
- 존재하지 않는 파일은 조용히 skip.
- 개별 파일 삭제 실패는 로그로 남기고 다음 파일 진행.
"""

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import or_

from .. import config
from .. import db as app_db
from ..logging_config import get_logger
from ..models import Inspection


log = get_logger(__name__)


PATH_COLUMNS = ("image_path", "heatmap_path", "annotated_path", "seg_path")
# .gitkeep 등 시스템 파일은 정리 대상 제외.
PROTECTED_NAMES = (".gitkeep",)


# ============================================================
# 안전 삭제
# ============================================================

def _safe_delete(basename):
    """UPLOADS_DIR 아래 지정된 basename 파일을 삭제한다. 밖 경로는 거부.
    반환: (removed_bool, size_before_or_zero). 실패는 로그만."""
    if not basename:
        return False, 0
    if basename in PROTECTED_NAMES:
        return False, 0

    uploads_root = config.UPLOADS_DIR.resolve()
    target = (uploads_root / basename).resolve()

    # UPLOADS_DIR 밖 경로 (../.. 등) 거부
    try:
        target.relative_to(uploads_root)
    except ValueError:
        log.warning("Refuse to delete outside uploads: %s", target)
        return False, 0

    if not target.exists() or not target.is_file():
        return False, 0

    try:
        size = target.stat().st_size
        target.unlink()
        return True, size
    except OSError as exc:
        log.error("Failed to delete %s: %s", target, exc)
        return False, 0


def delete_inspection_files(image_path, heatmap_path, annotated_path, seg_path):
    """검사 삭제 시 4 파일 정리. 각각 성공·실패 무관 진행."""
    result = {"removed": 0, "bytes": 0}
    for basename in (image_path, heatmap_path, annotated_path, seg_path):
        removed, size = _safe_delete(basename)
        if removed:
            result["removed"] += 1
            result["bytes"] += size
    return result


# ============================================================
# 고아 파일 정리
# ============================================================

def cleanup_orphan_files():
    """uploads/ 안 실제 파일 중 어떤 INSPECTIONS 행도 참조하지 않는 것 삭제.
    .gitkeep 등 보호 이름 제외."""
    uploads_root = config.UPLOADS_DIR
    if not uploads_root.exists():
        log.info("uploads dir not found: %s", uploads_root)
        return {"scanned": 0, "removed": 0, "bytes": 0, "referenced": 0}

    # DB 참조 basename 수집
    referenced = set()
    with app_db.get_session() as sqlalchemy_session:
        rows = sqlalchemy_session.query(
            Inspection.image_path,
            Inspection.heatmap_path,
            Inspection.annotated_path,
            Inspection.seg_path,
        ).all()
        for row in rows:
            for value in row:
                if value:
                    referenced.add(value)

    scanned = 0
    removed = 0
    total_bytes = 0
    for path in uploads_root.iterdir():
        if not path.is_file():
            continue
        scanned += 1
        if path.name in PROTECTED_NAMES:
            continue
        if path.name in referenced:
            continue
        ok, size = _safe_delete(path.name)
        if ok:
            removed += 1
            total_bytes += size

    log.info(
        "cleanup_orphan_files scanned=%d referenced=%d removed=%d bytes=%d",
        scanned, len(referenced), removed, total_bytes,
    )
    return {
        "scanned": scanned,
        "referenced": len(referenced),
        "removed": removed,
        "bytes": total_bytes,
    }


# ============================================================
# 오래된 파일 정리 (DB 행 유지, 파일 + 경로만 삭제)
# ============================================================

def cleanup_old_files(days):
    """N 일 이전 INSPECTIONS 행의 이미지 파일 삭제 + 경로 컬럼 NULL 갱신.
    DB 행 자체는 남긴다. 반환: 처리한 검사 수, 삭제한 파일 수, 확보 용량."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    processed = 0
    removed_files = 0
    total_bytes = 0

    with app_db.get_session() as sqlalchemy_session:
        # 파일이 아직 있는 오래된 검사만 대상 (경로 컬럼 중 하나라도 non-NULL)
        old = sqlalchemy_session.query(Inspection).filter(
            Inspection.created_at < cutoff,
            or_(
                Inspection.image_path.isnot(None),
                Inspection.heatmap_path.isnot(None),
                Inspection.annotated_path.isnot(None),
                Inspection.seg_path.isnot(None),
            ),
        ).all()

        for row in old:
            for column_name in PATH_COLUMNS:
                basename = getattr(row, column_name)
                if not basename:
                    continue
                ok, size = _safe_delete(basename)
                if ok:
                    removed_files += 1
                    total_bytes += size
                # DB 컬럼은 파일 삭제 성공 여부와 무관하게 NULL 로 세팅
                # (이미 파일이 없다는 사실이 확정되었으므로 참조 유지 무의미)
                setattr(row, column_name, None)
            processed += 1
        sqlalchemy_session.flush()

    log.info(
        "cleanup_old_files days=%d processed=%d removed_files=%d bytes=%d",
        days, processed, removed_files, total_bytes,
    )
    return {
        "days": days,
        "processed": processed,
        "removed": removed_files,
        "bytes": total_bytes,
    }


# ============================================================
# 상태 조회 (관리자 화면용)
# ============================================================

def storage_status():
    """uploads 총 용량·파일 수·고아 수·DB 참조 수."""
    uploads_root = config.UPLOADS_DIR
    total_bytes = 0
    file_count = 0
    if uploads_root.exists():
        for path in uploads_root.iterdir():
            if path.is_file() and path.name not in PROTECTED_NAMES:
                file_count += 1
                total_bytes += path.stat().st_size

    referenced = set()
    with app_db.get_session() as sqlalchemy_session:
        rows = sqlalchemy_session.query(
            Inspection.image_path, Inspection.heatmap_path,
            Inspection.annotated_path, Inspection.seg_path,
        ).all()
        for row in rows:
            for value in row:
                if value:
                    referenced.add(value)

    orphan_count = 0
    if uploads_root.exists():
        for path in uploads_root.iterdir():
            if path.is_file() and path.name not in PROTECTED_NAMES:
                if path.name not in referenced:
                    orphan_count += 1

    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "referenced_count": len(referenced),
        "orphan_count": orphan_count,
    }
