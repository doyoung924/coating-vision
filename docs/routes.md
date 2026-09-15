# 라우트 인벤토리 (§3-6, 2026-09-16 재실측 갱신)

작성 시점: 2026-09-14 최초. 2026-09-16 `app.url_map` 실측으로 누락 11개 추가.

## 세는 기준

- **총 36개** — static 제외, 에러 핸들러 제외, `app.url_map.iter_rules()` 기준 (고유 rule 수)
- 같은 rule 에 GET·POST 가 함께 등록된 경우 (예: `/login`, `/inspect`, `/account`) 는 **하나의 rule 로 셈**
- 에러 핸들러 (`app.error_handler_spec`) 4건 (403 · 404 · 500 · Exception) 은 rule 이 아니라 별도. 총계 미포함

**분류**:
- HTML 응답 라우트 34개 / JSON API 라우트 2개
- GET+POST 함께 등록된 rule 8개 (`/inspect`, `/detection`, `/register`, `/login`, `/board/new`, `/board/<id>/edit`, `/account`, `/account/password`)

## 라우트 표 (36개)

| path | methods | blueprint | endpoint | 데코레이터 | DB 접근 |
|---|---|---|---|---|---|
| `/` | GET | main | main.index | login_required | yes |
| `/account` | GET, POST | account | account.account_page | login_required | yes |
| `/account/password` | GET, POST | account | account.password_page | login_required | yes |
| `/admin/storage` | GET | admin | admin.storage_page | role_required('admin') | yes |
| `/admin/storage/cleanup-old` | POST | admin | admin.storage_cleanup_old | role_required('admin') | yes |
| `/admin/storage/cleanup-orphan` | POST | admin | admin.storage_cleanup_orphan | role_required('admin') | yes |
| `/admin/users` | GET | admin | admin.users_list | role_required('admin') | yes |
| `/admin/users/<int:user_id>/active` | POST | admin | admin.users_toggle_active | role_required('admin') | yes |
| `/admin/users/<int:user_id>/reset` | POST | admin | admin.users_reset_password | role_required('admin') | yes |
| `/admin/users/<int:user_id>/role` | POST | admin | admin.users_change_role | role_required('admin') | yes |
| `/api/sequence/<seq_id>` | GET | api | api.api_sequence | api_login_required | no |
| `/api/spc/series` | GET | api_spc | api_spc.spc_series | api_login_required + 내부 manager 체크 | yes |
| `/benchmark` | GET | benchmark | benchmark.benchmark_page | login_required | no |
| `/board` | GET | board | board.board_list | login_required | yes |
| `/board/<int:post_id>` | GET | board | board.board_detail | login_required | yes |
| `/board/<int:post_id>/comment` | POST | board | board.board_comment_new | login_required | yes |
| `/board/<int:post_id>/delete` | POST | board | board.board_delete | login_required | yes |
| `/board/<int:post_id>/edit` | GET, POST | board | board.board_edit | login_required | yes |
| `/board/new` | GET, POST | board | board.board_new | login_required | yes |
| `/comment/<int:comment_id>/delete` | POST | board | board.comment_delete | login_required | yes |
| `/detection` | GET, POST | inspect | inspect.detection_alias | — (리다이렉트만) | no |
| `/figures/<path:name>` | GET | main | main.serve_figure | login_required | no |
| `/frame/<path:name>` | GET | main | main.serve_frame | login_required | no |
| `/history` | GET | history | history.history_list | login_required | yes |
| `/history/<int:inspection_id>` | GET | history | history.history_detail | login_required | yes |
| `/history/<int:inspection_id>/delete` | POST | history | history.history_delete | login_required | yes |
| `/history/<int:inspection_id>/review` | POST | history | history.history_review | login_required | yes |
| `/history/all` | GET | history | history.history_list_all | role_required('manager', 'admin') | yes |
| `/history/all/export` | GET | history | history.history_export_all | role_required('manager', 'admin') | yes |
| `/history/export` | GET | history | history.history_export | login_required | yes |
| `/inspect` | GET, POST | inspect | inspect.inspect_page | login_required | yes |
| `/login` | GET, POST | auth | auth.login_page | — (공개) | yes |
| `/logout` | GET | auth | auth.logout_page | — (공개) | yes |
| `/register` | GET, POST | auth | auth.register_page | — (공개) | yes |
| `/spc` | GET | spc | spc.spc_page | role_required('manager', 'admin') | yes |
| `/stream` | GET | stream | stream.stream_page | login_required | no |

## 접근 정책 요약

| 정책 | 라우트 |
|---|---|
| 공개 | `/login`, `/register`, `/logout`, `/static/*` |
| 로그인 필요 (HTML) | `/`, `/inspect`, `/history`, `/history/<id>`, `/history/<id>/delete`, `/history/<id>/review`, `/history/export`, `/stream`, `/benchmark`, `/board*`, `/figures/*`, `/frame/*`, `/detection`, `/account`, `/account/password` |
| API 로그인 필요 (401 JSON) | `/api/sequence/<id>` |
| manager 이상 (HTML) | `/history/all`, `/history/all/export`, `/spc` |
| API manager 이상 (401/403 JSON) | `/api/spc/series` |
| admin 전용 (HTML) | `/admin/users`, `/admin/users/<id>/role`, `/admin/users/<id>/active`, `/admin/users/<id>/reset`, `/admin/storage`, `/admin/storage/cleanup-old`, `/admin/storage/cleanup-orphan` |

## 에러 핸들러 (rule 이 아닌 등록)

`app.error_handler_spec` 에 등록. url_map 순회에는 포함되지 않아 총계에서 제외.

| HTTP 코드 | 등록 위치 | 응답 |
|---|---|---|
| 403 | `app/__init__.py:error_403` | HTML 커스텀 페이지 / API 라우트는 JSON |
| 404 | `app/__init__.py:error_404` | HTML 커스텀 페이지 |
| 500 | `app/__init__.py:error_500` | HTML 커스텀 페이지 (스택 트레이스 노출 금지, 로그에만) / API 라우트는 JSON |
| Exception | `app/__init__.py:exception` | 500 응답 |

## 데코레이터 누락 점검

**없음.** DB 접근 라우트 중 인증 데코레이터가 붙지 않은 것은 없다.
공개 라우트 `/login`·`/register`·`/logout` 은 정책상 예외 (인증 자체를 수행하는 라우트).

`/detection` 은 인증 데코레이터 없음이지만 DB 접근 없이 `/inspect` 로 302 리다이렉트만 함. 리다이렉트 대상이 `@login_required` 이므로 익명은 결국 `/login` 으로 재리다이렉트되는 구조. 별도 처리 불필요.

## HTTP 응답 규약

- 미로그인 (HTML 라우트): **302 → `/login?next=<원래 URL>`**
- 미로그인 (API 라우트): **401 JSON** `{"error": "authentication required"}`
- 권한 부족 (HTML manager+ / admin 라우트): **403** (`role_required` 데코레이터가 `abort(403)`)
- 권한 부족 (API manager+ 라우트): **403 JSON** `{"error": "role manager or admin required"}`
- 서비스 계층 거부 (예: report 없이 mark_reviewed 시도): **302 → 원 페이지 + flash-error** (사유를 사용자에게 알리기 위함)
- 폼 검증 실패: **400** + flash-error + 입력값 재렌더

`role_required` 는 `abort(403)` 로 HTML 라우트에서 403 응답. `mark_reviewed`·`create_post` 등 서비스 계층 거부는 사유가 여러 가지 (권한, 상태, 리포트 부재 등) 이므로 flash 로 사유를 알리고 원 페이지로 되돌린다.

## 변경 이력

- **2026-09-14** (§3-6 시점): 최초 작성. 25개 라우트 등재
- **2026-09-16 재실측**: `app.url_map` 순회로 총 36개 확인. 누락 11개 추가 (아래 목록)

### 2026-09-16 추가된 11 라우트

§3-7 계정 관리 · §3-8 파일 정리 · §3-9 CSV 내보내기 단계에서 추가된 라우트가 문서에 미반영된 상태였다.

| 추가 라우트 | 도입 단계 |
|---|---|
| `/account`, `/account/password` | §3-7 (계정 프로필/비밀번호) |
| `/admin/users`, `/admin/users/<id>/role`, `/admin/users/<id>/active`, `/admin/users/<id>/reset` | §3-7 (사용자 관리) |
| `/admin/storage`, `/admin/storage/cleanup-old`, `/admin/storage/cleanup-orphan` | §3-8 (파일 생명주기) |
| `/history/export`, `/history/all/export` | §3-9 (CSV 내보내기) |
