"""역할별 접근 매트릭스 (§3-10)."""
import pytest


# (path, role, expected_status). role="anon" 은 익명.
# 302 = login redirect, 401 = API JSON, 403 = 권한 부족, 200 = 통과
CASES = [
    # 공개
    ("/login",              "anon",       200),
    ("/login",              "inspector",  302),  # 로그인 상태에서 /login → /
    ("/register",           "anon",       200),
    ("/static/css/style.css", "anon",     200),

    # 일반 로그인 요구
    ("/",                   "anon",       302),
    ("/",                   "inspector",  200),
    ("/",                   "manager",    200),
    ("/",                   "admin",      200),
    ("/inspect",            "anon",       302),
    ("/inspect",            "inspector",  200),
    ("/history",            "anon",       302),
    ("/history",            "inspector",  200),
    ("/stream",             "inspector",  200),
    ("/benchmark",          "inspector",  200),
    ("/board",              "inspector",  200),
    ("/account",            "inspector",  200),

    # manager 이상
    ("/history/all",        "anon",       302),
    ("/history/all",        "inspector",  403),
    ("/history/all",        "manager",    200),
    ("/history/all",        "admin",      200),
    ("/spc",                "inspector",  403),
    ("/spc",                "manager",    200),
    ("/spc",                "admin",      200),

    # admin 전용
    ("/admin/users",        "anon",       302),
    ("/admin/users",        "inspector",  403),
    ("/admin/users",        "manager",    403),
    ("/admin/users",        "admin",      200),
    ("/admin/storage",      "inspector",  403),
    ("/admin/storage",      "manager",    403),
    ("/admin/storage",      "admin",      200),

    # API — 익명은 401, 미로그인 → 401 JSON
    ("/api/sequence/R1_600_top-to-bottom-center", "anon",     401),
    ("/api/sequence/R1_600_top-to-bottom-center", "inspector",200),
    ("/api/spc/series?metric=a3", "anon",     401),
    ("/api/spc/series?metric=a3", "inspector",403),
    ("/api/spc/series?metric=a3", "manager",  200),
    ("/api/spc/series?metric=a3", "admin",    200),

    # CSV export
    ("/history/export",     "anon",       302),
    ("/history/export",     "inspector",  200),
    ("/history/all/export", "inspector",  403),
    ("/history/all/export", "manager",    200),
]


@pytest.mark.parametrize("path,role,expected", CASES)
def test_access_matrix(path, role, expected, anon_client, inspector_user, manager_user, admin_user):
    if role == "anon":
        client = anon_client
    elif role == "inspector":
        client = inspector_user["client"]
    elif role == "manager":
        client = manager_user["client"]
    elif role == "admin":
        client = admin_user["client"]
    else:
        pytest.fail("unknown role: " + role)

    resp = client.get(path, follow_redirects=False)
    assert resp.status_code == expected, (
        "path={} role={} expected={} got={}".format(path, role, expected, resp.status_code)
    )
