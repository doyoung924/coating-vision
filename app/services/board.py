"""
게시판 서비스 계층 (§3-5).

권한 판정을 여기서. 라우트는 얇게 유지.

카테고리 정책:
- notice: manager 이상만 작성 (수정도 동일 권한자 또는 admin)
- report: 검사 건에 연결. 누구나 작성 가능하나 inspection_id 필수 권장
- qna, free: 누구나

nl2br 처리 정책: 이스케이프는 Jinja2 자동. 서비스는 원문을 저장하고,
템플릿의 nl2br 커스텀 필터가 (이스케이프 후) 개행만 <br> 로 변환한다.
"""

from datetime import datetime
from math import ceil

from sqlalchemy import func

from .. import db as app_db
from ..models import Comment, Inspection, Post, User


PAGE_SIZE = 20
VALID_CATEGORIES = ("notice", "report", "qna", "free")
NOTICE_CATEGORY = "notice"
REPORT_CATEGORY = "report"


class BoardError(Exception):
    """검증 실패 · 권한 부족 등 회복 가능한 오류."""
    pass


# ============================================================
# 게시글
# ============================================================

def create_post(user_id, user_role, category, title, content, inspection_id=None):
    _validate_post_input(category, title, content)
    if category == NOTICE_CATEGORY and user_role not in ("manager", "admin"):
        raise BoardError("공지사항은 매니저 이상만 작성할 수 있습니다.")

    if inspection_id is not None:
        _ensure_inspection_exists(inspection_id)

    with app_db.get_session() as sqlalchemy_session:
        post = Post(
            user_id=user_id,
            inspection_id=inspection_id,
            category=category,
            title=title.strip(),
            content=content,
            view_count=0,
        )
        sqlalchemy_session.add(post)
        sqlalchemy_session.flush()
        return int(post.id)


def get_posts(category, page, per_page=PAGE_SIZE):
    """카테고리 목록. notice 는 상단 고정 (다른 카테고리 목록에서도 notice 상단에).
    category='all' 은 모든 카테고리. """
    if page < 1:
        page = 1
    offset = (page - 1) * per_page

    with app_db.get_session() as sqlalchemy_session:
        base = (
            sqlalchemy_session.query(Post, User)
            .outerjoin(User, Post.user_id == User.id)
        )

        if category != "all":
            if category not in VALID_CATEGORIES:
                raise BoardError("잘못된 카테고리: {}".format(category))
            # 다른 카테고리 탭이라도 notice 는 상단 고정. category 필터에 notice 를 OR 로 포함.
            if category == NOTICE_CATEGORY:
                filtered = base.filter(Post.category == NOTICE_CATEGORY)
            else:
                filtered = base.filter(Post.category.in_((category, NOTICE_CATEGORY)))
        else:
            filtered = base

        # 정렬: notice 를 위로, 그 다음 최신순.
        # Oracle 에서 CASE 정렬 사용.
        from sqlalchemy import case, desc
        notice_priority = case((Post.category == NOTICE_CATEGORY, 0), else_=1)
        query = filtered.order_by(notice_priority.asc(),
                                   Post.created_at.desc(), Post.id.desc())

        rows = query.offset(offset).limit(per_page).all()

        # 총 개수 (같은 필터)
        if category != "all":
            if category == NOTICE_CATEGORY:
                total_q = sqlalchemy_session.query(func.count(Post.id)).filter(
                    Post.category == NOTICE_CATEGORY,
                )
            else:
                total_q = sqlalchemy_session.query(func.count(Post.id)).filter(
                    Post.category.in_((category, NOTICE_CATEGORY)),
                )
        else:
            total_q = sqlalchemy_session.query(func.count(Post.id))
        total_count = int(total_q.scalar() or 0)

        result = []
        for post, user in rows:
            result.append(_post_dict(post, user))
    total_pages = max(1, ceil(total_count / per_page)) if total_count else 1
    return result, total_count, total_pages


def get_post(post_id, viewer_user_id=None, increment=False):
    with app_db.get_session() as sqlalchemy_session:
        post = sqlalchemy_session.get(Post, post_id)
        if post is None:
            return None
        author = sqlalchemy_session.get(User, int(post.user_id)) if post.user_id else None

        if increment and viewer_user_id is not None:
            # 본인 글은 조회수 증가 안 함
            if int(post.user_id) != int(viewer_user_id):
                post.view_count = int(post.view_count or 0) + 1
                sqlalchemy_session.flush()

        inspection_summary = None
        if post.inspection_id is not None:
            inspection = sqlalchemy_session.get(Inspection, int(post.inspection_id))
            if inspection is not None:
                inspection_summary = {
                    "id": int(inspection.id),
                    "file_name": inspection.file_name,
                    "status": inspection.status,
                    "a3_ratio": float(inspection.a3_ratio) if inspection.a3_ratio is not None else None,
                    "seg_crack": float(inspection.seg_crack) if inspection.seg_crack is not None else None,
                    "pinhole_count": int(inspection.pinhole_count) if inspection.pinhole_count is not None else None,
                    "created_at": inspection.created_at,
                }

        comments = (
            sqlalchemy_session.query(Comment, User)
            .outerjoin(User, Comment.user_id == User.id)
            .filter(Comment.post_id == post.id)
            .order_by(Comment.created_at.asc(), Comment.id.asc())
            .all()
        )
        comment_list = []
        for comment, comment_author in comments:
            comment_list.append({
                "id": int(comment.id),
                "user_id": int(comment.user_id) if comment.user_id is not None else None,
                "username": comment_author.username if comment_author is not None else None,
                "content": comment.content,
                "created_at": comment.created_at,
            })

        detail = _post_dict(post, author)
        detail["inspection"] = inspection_summary
        detail["comments"] = comment_list
    return detail


def update_post(post_id, viewer_user_id, viewer_role, title, content, category=None):
    _validate_post_input(category or "free", title, content, require_category=False)
    with app_db.get_session() as sqlalchemy_session:
        post = sqlalchemy_session.get(Post, post_id)
        if post is None:
            raise BoardError("게시글이 없습니다.")
        if not _can_edit_post(post, viewer_user_id, viewer_role):
            raise BoardError("수정 권한이 없습니다.")

        # category 변경 시 notice 승격은 manager 이상만
        if category is not None and category != post.category:
            if category not in VALID_CATEGORIES:
                raise BoardError("잘못된 카테고리: {}".format(category))
            if category == NOTICE_CATEGORY and viewer_role not in ("manager", "admin"):
                raise BoardError("공지사항으로 변경할 권한이 없습니다.")
            post.category = category

        post.title = title.strip()
        post.content = content
        post.updated_at = datetime.utcnow()
        sqlalchemy_session.flush()


def delete_post(post_id, viewer_user_id, viewer_role):
    with app_db.get_session() as sqlalchemy_session:
        post = sqlalchemy_session.get(Post, post_id)
        if post is None:
            return False
        if not _can_edit_post(post, viewer_user_id, viewer_role):
            return False
        sqlalchemy_session.delete(post)
    return True


# ============================================================
# 댓글
# ============================================================

def create_comment(post_id, user_id, content):
    if content is None or not content.strip():
        raise BoardError("댓글 내용을 입력하세요.")
    if len(content) > 1000:
        raise BoardError("댓글은 1000자 이하여야 합니다.")

    with app_db.get_session() as sqlalchemy_session:
        post = sqlalchemy_session.get(Post, post_id)
        if post is None:
            raise BoardError("게시글이 없습니다.")

        comment = Comment(post_id=post_id, user_id=user_id, content=content)
        sqlalchemy_session.add(comment)
        sqlalchemy_session.flush()
        return int(comment.id)


def delete_comment(comment_id, viewer_user_id, viewer_role):
    with app_db.get_session() as sqlalchemy_session:
        comment = sqlalchemy_session.get(Comment, comment_id)
        if comment is None:
            return None, False
        is_owner = comment.user_id is not None and int(comment.user_id) == int(viewer_user_id)
        is_admin = viewer_role == "admin"
        if not (is_owner or is_admin):
            return int(comment.post_id) if comment.post_id else None, False
        post_id = int(comment.post_id) if comment.post_id else None
        sqlalchemy_session.delete(comment)
    return post_id, True


# ============================================================
# 알람 조치 완료 헬퍼
# ============================================================

def has_report_for_inspection(inspection_id):
    """검사에 연결된 report 카테고리 글이 최소 1건 있는지."""
    with app_db.get_session() as sqlalchemy_session:
        count = sqlalchemy_session.query(func.count(Post.id)).filter(
            Post.inspection_id == inspection_id,
            Post.category == REPORT_CATEGORY,
        ).scalar()
        return int(count or 0) > 0


# ============================================================
# 내부
# ============================================================

def _validate_post_input(category, title, content, require_category=True):
    if require_category:
        if category not in VALID_CATEGORIES:
            raise BoardError("잘못된 카테고리: {}".format(category))
    if not title or not title.strip():
        raise BoardError("제목을 입력하세요.")
    if len(title) > 200:
        raise BoardError("제목은 200자 이하여야 합니다.")
    if content is not None and len(content) > 4000:
        raise BoardError("본문은 4000자 이하여야 합니다.")


def _ensure_inspection_exists(inspection_id):
    with app_db.get_session() as sqlalchemy_session:
        exists = sqlalchemy_session.query(Inspection.id).filter(
            Inspection.id == inspection_id,
        ).one_or_none()
        if exists is None:
            raise BoardError("연결된 검사가 존재하지 않습니다 (id={}).".format(inspection_id))


def _can_edit_post(post, viewer_user_id, viewer_role):
    is_owner = post.user_id is not None and int(post.user_id) == int(viewer_user_id)
    is_admin = viewer_role == "admin"
    return is_owner or is_admin


def _post_dict(post, author):
    return {
        "id": int(post.id),
        "user_id": int(post.user_id) if post.user_id is not None else None,
        "username": author.username if author is not None else None,
        "inspection_id": int(post.inspection_id) if post.inspection_id is not None else None,
        "category": post.category,
        "title": post.title,
        "content": post.content,
        "view_count": int(post.view_count or 0),
        "created_at": post.created_at,
        "updated_at": post.updated_at,
    }
