"""게시판 CRUD · 권한 · CASCADE · XSS · 페이지네이션 (§3-10)."""
import uuid

import pytest

from app import db as app_db
from app.models import Comment, Post
from app.services import board as board_service


UID = lambda: uuid.uuid4().hex[:8]


class TestPostCRUD:

    def test_create_post_success(self, inspector_user):
        title = "pytest 자유글 " + UID()
        resp = inspector_user["client"].post("/board/new", data={
            "title": title, "content": "본문 A\n본문 B", "category": "free",
        }, follow_redirects=False)
        assert resp.status_code == 302

        with app_db.get_session() as s:
            posts = s.query(Post).filter(Post.title == title).all()
            assert len(posts) == 1

    def test_notice_by_inspector_denied(self, inspector_user):
        resp = inspector_user["client"].post("/board/new", data={
            "title": "notice from inspector", "content": "x", "category": "notice",
        })
        assert resp.status_code == 400
        assert "공지사항" in resp.get_data(as_text=True)

    def test_notice_by_manager_ok(self, manager_user):
        title = "pytest notice " + UID()
        resp = manager_user["client"].post("/board/new", data={
            "title": title, "content": "중요", "category": "notice",
        }, follow_redirects=False)
        assert resp.status_code == 302

    def test_edit_other_post_denied(self, inspector_user, manager_user):
        # inspector 가 글 작성
        with app_db.get_session() as s:
            post = Post(user_id=inspector_user["id"], category="free",
                        title="pytest edit target " + UID(), content="x", view_count=0)
            s.add(post); s.flush()
            post_id = int(post.id)

        # manager 가 수정 시도 (본인 아니고 admin 도 아님) → 403
        resp = manager_user["client"].get("/board/{}/edit".format(post_id))
        assert resp.status_code == 403

    def test_admin_can_edit_other_post(self, inspector_user, admin_user):
        with app_db.get_session() as s:
            post = Post(user_id=inspector_user["id"], category="free",
                        title="pytest admin edit " + UID(), content="x", view_count=0)
            s.add(post); s.flush()
            post_id = int(post.id)

        new_title = "pytest admin edited " + UID()
        resp = admin_user["client"].post("/board/{}/edit".format(post_id), data={
            "title": new_title, "content": "edited", "category": "free",
        }, follow_redirects=False)
        assert resp.status_code == 302

        with app_db.get_session() as s:
            p = s.query(Post).filter(Post.id == post_id).one()
            assert p.title == new_title

    def test_delete_cascade_comments(self, inspector_user, manager_user):
        with app_db.get_session() as s:
            post = Post(user_id=inspector_user["id"], category="free",
                        title="pytest cascade " + UID(), content="x", view_count=0)
            s.add(post); s.flush()
            post_id = int(post.id)

            # 댓글 2개 (manager 가 작성)
            for i in range(2):
                s.add(Comment(post_id=post_id, user_id=manager_user["id"], content="c" + str(i)))
            s.flush()

        # 삭제 (본인)
        resp = inspector_user["client"].post("/board/{}/delete".format(post_id))
        assert resp.status_code == 302

        with app_db.get_session() as s:
            assert s.query(Post).filter(Post.id == post_id).count() == 0
            assert s.query(Comment).filter(Comment.post_id == post_id).count() == 0


class TestViewCount:

    def test_view_count_increments(self, inspector_user, manager_user):
        with app_db.get_session() as s:
            post = Post(user_id=inspector_user["id"], category="free",
                        title="pytest view " + UID(), content="x", view_count=0)
            s.add(post); s.flush()
            post_id = int(post.id)

        # 타인(manager) 조회 → +1
        manager_user["client"].get("/board/{}".format(post_id))
        with app_db.get_session() as s:
            assert int(s.query(Post).filter(Post.id == post_id).one().view_count) == 1

        # 본인 조회 → 증가 없음
        inspector_user["client"].get("/board/{}".format(post_id))
        with app_db.get_session() as s:
            assert int(s.query(Post).filter(Post.id == post_id).one().view_count) == 1

        # 타인 재조회 → +1
        manager_user["client"].get("/board/{}".format(post_id))
        with app_db.get_session() as s:
            assert int(s.query(Post).filter(Post.id == post_id).one().view_count) == 2


class TestComment:

    def test_comment_create_and_own_delete(self, inspector_user, manager_user):
        with app_db.get_session() as s:
            post = Post(user_id=inspector_user["id"], category="free",
                        title="pytest comment " + UID(), content="x", view_count=0)
            s.add(post); s.flush()
            post_id = int(post.id)

        # manager 가 댓글 작성
        resp = manager_user["client"].post("/board/{}/comment".format(post_id), data={
            "content": "댓글 by manager",
        })
        assert resp.status_code == 302

        with app_db.get_session() as s:
            cs = s.query(Comment).filter(Comment.post_id == post_id).all()
            assert len(cs) == 1
            comment_id = int(cs[0].id)

        # inspector (자신 글) 가 manager 댓글 삭제 시도 → 403
        resp = inspector_user["client"].post("/comment/{}/delete".format(comment_id))
        assert resp.status_code == 403

        # 본인(manager) 삭제 성공
        resp = manager_user["client"].post("/comment/{}/delete".format(comment_id))
        assert resp.status_code == 302


class TestXSS:

    def test_xss_escaped_in_title_content_comment(self, inspector_user):
        xss = "<script>alert(1)</script>"
        title_with_xss = xss + " " + UID()
        resp = inspector_user["client"].post("/board/new", data={
            "title": title_with_xss, "content": "본문 " + xss, "category": "free",
        }, follow_redirects=False)
        assert resp.status_code == 302
        # 새 post id
        location = resp.headers["Location"]
        post_id = int(location.rstrip("/").split("/")[-1])

        # 상세 조회
        resp = inspector_user["client"].get("/board/{}".format(post_id))
        body = resp.get_data(as_text=True)
        # raw script 태그가 렌더된 HTML 에 없어야
        assert "<script>alert(1)</script>" not in body
        # 이스케이프된 형태 존재
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body

        # 댓글도 이스케이프
        inspector_user["client"].post("/board/{}/comment".format(post_id), data={
            "content": "댓글 " + xss,
        })
        resp = inspector_user["client"].get("/board/{}".format(post_id))
        body = resp.get_data(as_text=True)
        assert "<script>alert(1)</script>" not in body


class TestPagination:

    def test_per_page_whitelist_default_on_invalid(self, inspector_user):
        """per_page=999 (허용 외) → 20 fallback."""
        resp = inspector_user["client"].get("/board?category=all&per_page=999")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # 드롭다운에 20 이 selected
        assert 'option value="20" selected' in body

    def test_pagination_macro_present(self, inspector_user):
        resp = inspector_user["client"].get("/board?category=all")
        body = resp.get_data(as_text=True)
        # 새 매크로 렌더 결과 존재 여부 (요약 라벨)
        assert "전체" in body and "건" in body
