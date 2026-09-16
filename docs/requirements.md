# 요구사항 분석서 (§3-11)

작성 시점: 2026-09-14. 만들지 않은 기능은 §4 "범위 밖" 에 명시.

## 작성 경위

이 문서는 §3-1 ~ §3-11 개별 단계의 기능 명세 10건을 **통합·정규화** 하여 §3-11 단계에서 작성했다. 개별 명세는 기능 단위로 먼저 작성되어 구현으로 이어졌으며, 이 분석서는 구현 완료 후 각 단계 산출을 대조·정리한 결과다. FR/NFR 번호는 이 통합 시점에 부여됐다.

구현과의 대조 검증은 이 문서 §5 (검증 요약) 및 pytest 케이스 매핑을 통해 수행했다. 불일치 항목은 `docs/DEVELOPMENT_PROCESS.md` §4-5 "개발 중 정정한 오류" 표에 기록되어 있다.

관련: [`docs/architecture.md`](architecture.md) · [`docs/routes.md`](routes.md) · [`docs/schema.md`](schema.md) · [`docs/testing.md`](testing.md) · [`docs/uml.md`](uml.md) · [`docs/DEVELOPMENT_PROCESS.md`](DEVELOPMENT_PROCESS.md).

\---

## 1\. 기능 요구사항 (FR)

우선순위: **P0** 시연 필수 · **P1** 통합 지원 · **P2** 편의.

### 1.1 인증 · 권한

|ID|요구사항|P|구현 위치|검증|
|-|-|-|-|-|
|FR-01|사용자는 username·비밀번호·(선택)이메일·이름으로 회원가입한다. 기본 역할은 `inspector`|P0|`app/routes/auth.py:register\_page` · `app/services/auth.py:register`|`tests/test\_auth.py::TestRegister`|
|FR-02|회원가입 시 username·email 중복은 400 + flash 로 거부한다|P0|`services/auth.py:\_validate\_registration` · `register`|`test\_auth.py::TestRegister::test\_register\_duplicate\_\*`|
|FR-03|비밀번호는 8자 이상. `password` 와 `password\_confirm` 일치 필수|P0|`services/auth.py:MIN\_PASSWORD\_LENGTH=8`, `routes/auth.py:register\_page`|`test\_auth.py::TestRegister::test\_register\_short\_password`, `test\_register\_password\_mismatch`|
|FR-04|로그인은 werkzeug scrypt 해시 비교. 성공 시 `LAST\_LOGIN\_AT` 갱신, 세션에 `user\_id`·`role` 두 키만 저장|P0|`services/auth.py:authenticate` · `login\_session`|`test\_auth.py::TestLogin::test\_login\_success`|
|FR-05|`IS\_ACTIVE=0` 계정은 로그인 거부|P0|`services/auth.py:authenticate`|`test\_auth.py::TestLogin::test\_login\_inactive`|
|FR-06|로그아웃은 세션 전체를 clear|P0|`services/auth.py:logout\_session` · `routes/auth.py:logout\_page`|`test\_auth.py::TestLogout::test\_logout\_clears\_session`|
|FR-07|이미 로그인된 상태에서 `/login`·`/register` 접근 시 `/` 로 리다이렉트|P1|`routes/auth.py:login\_page`, `register\_page`|수동 (§3-2 검증 완료)|
|FR-08|로그인 후 원래 요청 URL 로 복귀: `next` 파라미터. `\_safe\_next\_url` 로 open-redirect 방지 (`/` 시작 상대 경로만, `//` 거부)|P1|`routes/auth.py:\_safe\_next\_url`|수동|
|FR-09|역할 위계: admin > manager > inspector. `@role\_required('manager')` 는 manager, admin 통과. inspector 403|P0|`app/auth\_utils.py:ROLE\_RANK`, `role\_required`|`tests/test\_permissions.py::test\_access\_matrix` (parametrize 40)|
|FR-10|HTML 라우트 미로그인 시 302 → `/login?next=...`. API 라우트는 401 JSON|P0|`auth\_utils.py:login\_required`, `api\_login\_required`|`test\_permissions.py`|
|FR-11|프로필 (이름·이메일) 수정. 이메일은 다른 사용자와 중복 시 거부|P1|`services/auth.py:update\_profile` · `routes/account.py:account\_page`|`test\_auth.py::TestProfile`|
|FR-12|비밀번호 변경: 현재 비밀번호 검증 필수. 8자 미만·현재와 동일·확인 불일치 거부. 성공 시 세션 유지|P0|`services/auth.py:change\_password` · `routes/account.py:password\_page`|`test\_auth.py::TestPasswordChange`|

### 1.2 검사 · 파이프라인

|ID|요구사항|P|구현 위치|검증|
|-|-|-|-|-|
|FR-13|로그인 사용자는 이미지 업로드 또는 미리 준비된 샘플 3장 (핀홀·크랙·정상) 으로 검사 실행|P0|`routes/inspect.py:inspect\_page` · `SampleUpload`|수동 (§3-3 스모크)|
|FR-14|파이프라인 5 단계: (1) A3 밝기 표준편차 이상 탐지 → 히트맵 (2) YOLO 핀홀 검출 → 오버레이 (3) U-Net 세그 crack/delam → 오버레이 (4) 형태 분석 (aspect·roundness) (5) Advisor 3층 리포트|P0|`services/pipeline.py:run\_inspection` → `ml/{anomaly,detect,segment,shape,advisor}.py`|수동 값 대조 (§2단계 legacy 대비 crack 3.01%, delam 0.00%)|
|FR-15|YOLO·세그 모델은 lazy 로드 (첫 요청 시). 로드 실패는 에러 슬롯 보관, 재시도 안 함|P0|`ml/loader.py:get\_yolo`, `get\_seg`, `is\_\*\_loaded`|수동 (§2단계 (b) 실측 시작 시간 3735→223 ms)|
|FR-16|콜드 스타트 감지: 이번 요청에서 모델 로드 시 `IS\_COLD\_START=1`. 대시보드 평균 crack 통계에서 제외|P0|`routes/inspect.py:inspect\_page` (loader 상태 캡처) · `services/inspection\_store.py:get\_dashboard\_summary`|수동 (§3-4 실측 MS\_YOLO 2317→116 ms, 20×)|
|FR-17|검사 결과는 `app/static/uploads/` 에 4 파일 저장: 원본 · YOLO 오버레이 · A3 히트맵 · 세그 오버레이|P0|`services/pipeline.py:run\_inspection` (payload.file\_names)|수동|

### 1.3 이력

|ID|요구사항|P|구현 위치|검증|
|-|-|-|-|-|
|FR-18|검사 결과를 하나의 트랜잭션으로 INSPECTIONS 1행 + DETECTIONS N행 + FINDINGS N행 저장. 실패 시 롤백. DB 실패 시 화면 렌더는 유지, flash 로 알림|P0|`services/inspection\_store.py:save\_inspection`|`test\_inspection.py::TestSaveInspection`|
|FR-19|Advisor 3층 → FINDINGS 매핑: `observation` dict 는 key별 행, `interpretations` list 는 항목별 행, `references` list 는 topic 별 행 (source=topic)|P0|`services/inspection\_store.py:\_flatten\_advisor`|수동 (§3-3 저장 후 SQL 조회)|
|FR-20|DETECTIONS.ROUNDNESS 는 shape.per\_detection 을 index 로 매핑|P0|`services/inspection\_store.py:\_roundness\_map` · `save\_inspection`|수동 (§3-4 검사 #7 conf=0.515, roundness=0.699)|
|FR-21|FINDINGS.CONTENT 4000자 초과 시 자르고 stdout·로그 warning|P1|`services/inspection\_store.py:\_truncate\_content`|로그 문구 확인|
|FR-22|내 이력 목록: 최신순, 페이지당 20 (또는 50/100 화이트리스트). 기간·상태 필터|P0|`routes/history.py:history\_list` · `services/inspection\_store.py:get\_user\_inspections`|수동 (§3-9 페이지 137건 시나리오)|
|FR-23|상세 페이지: 저장된 4 이미지 + DETECTIONS 표 + Advisor 3층. 소유자 또는 manager 이상만 조회|P0|`routes/history.py:history\_detail` · `services/inspection\_store.py:get\_inspection\_detail`|`test\_inspection.py::TestPermissions`|
|FR-24|삭제는 소유자 또는 admin. 삭제 시 DETECTIONS·SPC\_POINTS·FINDINGS CASCADE + 4 이미지 파일도 삭제. 파일 실패해도 DB 삭제는 유지|P0|`routes/history.py:history\_delete` · `services/inspection\_store.py:delete\_inspection` · `services/cleanup.py:delete\_inspection\_files`|`test\_inspection.py::TestCascadeDelete`|
|FR-25|manager 이상은 `/history/all` 로 전체 사용자 이력 조회 (검사자 컬럼 + username 필터)|P0|`routes/history.py:history\_list\_all` · `services/inspection\_store.py:get\_all\_inspections`|`test\_permissions.py`, 수동|
|FR-26|대시보드 `/` 상단에 로그인 사용자 요약: 총 검사·최근 7일·평균 crack (콜드 제외)·최근 5건|P1|`routes/main.py:index` · `services/inspection\_store.py:get\_dashboard\_summary`|수동|
|FR-27|CSV 내보내기: `/history/export`, `/history/all/export`. UTF-8 BOM, 스트리밍, 17 컬럼, 10,000행 제한, `X-Row-Truncated` 헤더|P1|`routes/history.py:history\_export{,\_all}` · `services/inspection\_store.py:iter\_inspections\_for\_export`|`test\_inspection.py::TestCSVExport`|

### 1.4 공정관리 (SPC)

|ID|요구사항|P|구현 위치|검증|
|-|-|-|-|-|
|FR-28|SPC 는 전체 공통 (사용자별 분리 X). 검사 저장 시 `a3`·`seg\_crack` 두 metric 에 SPC 점 추가|P0|`services/inspection\_store.py:\_process\_spc\_and\_status` · `services/spc.py:add\_spc\_point`|수동 (§3-4 검사 #10 시나리오)|
|FR-29|baseline 30점까지는 CENTER/UCL/EWMA NULL 로 저장 (수집 단계). 31점부터 관리한계 계산|P0|`services/spc.py:BASELINE\_SIZE=30`, `add\_spc\_point` (phase='baseline'/'monitor')|`test\_spc.py::TestBaseline`|
|FR-30|관리한계: UCL = CENTER + 3.5σ. 원 채택 근거 (§9-3 lift) 폐기 (§3-1) 후 §23-3 재해석에서 세그 c\_frame\_max F1 관점 최적 L=3.5 확인. **사후 정량 근거 유효**|P0|`services/spc.py:SIGMA\_LIMIT=3.5`, `compute\_control\_limits`|`test\_spc.py::TestCalculationMatches09::test\_control\_limits\_formula`|
|FR-31|EWMA λ=0.1. 재귀식 `λ·value + (1-λ)·prev\_ewma`. 초기값 CENTER. 원 채택 근거 (§9-3) 폐기 후 §23-3 재해석에서 A3·세그 어느 관점에서도 F1 최적 아님 (세그 최적 λ=0.2, A3 최적 λ=0.05). 표본 소량으로 변경 보류. **정량 근거 없음 상태**|P0|`services/spc.py:EWMA\_LAMBDA=0.1`, `\_next\_ewma`|`test\_spc.py::TestCalculationMatches09::test\_ewma\_formula`|
|FR-32|알람 판정: 원값 또는 EWMA 가 UCL 초과 시 `IS\_ALARM=1` + INSPECTIONS.STATUS='alarm' 전이|P0|`services/spc.py:add\_spc\_point` · `services/inspection\_store.py:\_process\_spc\_and\_status`|수동 (§3-4 image\_1.jpg A3=0.5143 > UCL 0.4193 → STATUS=alarm)|
|FR-33|`/spc` (manager 이상): 누적 관리도 Chart.js 렌더. metric 탭 (a3/seg\_crack), baseline 음영, 알람 붉은 점, 최근 알람 목록|P0|`routes/spc.py:spc\_page` · `app/static/js/spc.js` · `templates/spc.html`|수동|
|FR-34|`/api/spc/series` (manager 이상): metric·limit 파라미터, JSON 반환 (summary + points)|P1|`api/spc.py:spc\_series`|`test\_permissions.py`|
|FR-35|알람 조치 완료 전이 (STATUS='alarm' → 'reviewed'): manager 이상 + 해당 검사에 연결된 report 카테고리 글이 최소 1건 있어야 함. 없으면 flash 로 거부. 성공 시 `REVIEWED\_BY`·`REVIEWED\_AT` 기록|P0|`services/inspection\_store.py:mark\_reviewed` · `routes/history.py:history\_review` · `services/board.py:has\_report\_for\_inspection`|수동 (§3-6 HTTP 재검증: bob 403 · admin no-report 거부 · admin+report 성공)|
|FR-56|SPC baseline 완성 판정은 metric 별 실제 저장된 SPC 점 수 기준. `SEQ_NO` gap 상태에서도 일관. `add_spc_point` 와 `get_summary` 두 지점 판정 기준 동일. FR-29 확장 (상세는 §1.4a)|P0|`services/spc.py:add\_spc\_point`, `get\_summary` (2단계 설계 후 결정)|`tests/test\_spc.py` gap 시나리오 (§1.4a 검증 기준 (a)~(d))|

#### §1.4a FR-56 상세

**배경**: 검사 이력 삭제 (FR-24 CASCADE) 로 `SPC_POINTS` 가 삭제되면 `SEQ_NO` 에 gap 이 생긴다. FR-29·FR-30·FR-31·FR-32 (baseline · 관리한계 · EWMA · 알람 판정) 는 baseline 완성 판정을 전제로 하므로 `add_spc_point` 와 `get_summary` 두 지점이 서로 다른 값을 반환하면 판정 오류로 이어질 수 있다.

**검증 기준** (인수 조건):
- (a) 특정 metric 에 30점 저장 → 중간 10점 삭제 → 20점 남은 상태에서 새 점 추가 시 `phase='baseline'` 으로 저장
- (b) 위 상태에서 `get_summary` 는 `baseline_complete = False`, `total_points = 20` 반환
- (c) 위 상태에서 `add_spc_point` 의 phase 판정과 `get_summary` 의 `baseline_complete` 는 논리적 동치 (한쪽이 baseline 이면 다른 쪽은 미완성)
- (d) NFR-17 (SPC 처리 실패 격리) 원칙에 따라 판정 로직 예외는 검사 저장 자체를 롤백하지 않음


### 1.5 게시판

|ID|요구사항|P|구현 위치|검증|
|-|-|-|-|-|
|FR-36|카테고리 4개: `notice`·`report`·`qna`·`free`. notice 는 manager 이상만 작성. 목록에서 notice 상단 고정|P0|`services/board.py:VALID\_CATEGORIES`, `create\_post`, `get\_posts` (case 정렬)|`test\_board.py::TestPostCRUD::test\_notice\_by\_\*`|
|FR-37|글 CRUD (create/read/update/delete). 수정·삭제는 작성자 또는 admin 만|P0|`services/board.py:{create,get,update,delete}\_post`|`test\_board.py::TestPostCRUD`|
|FR-38|조회수 증가: 다른 사용자 조회 시 +1. 본인 조회는 증가 안 함|P1|`services/board.py:get\_post` (increment=True, user\_id 비교)|`test\_board.py::TestViewCount`|
|FR-39|댓글 CRUD. 삭제는 작성자 또는 admin. 글 삭제 시 댓글 CASCADE|P0|`services/board.py:create\_comment`, `delete\_comment` · Oracle FK CASCADE|`test\_board.py::TestComment`, `TestPostCRUD::test\_delete\_cascade\_comments`|
|FR-40|검사 연결 (report 카테고리): `/history/<id>` 상세에 "이 검사로 리포트 작성" 링크 → `/board/new?inspection\_id=<id>\&category=report`. 폼에 검사 요약 표시 (수정 불가). 상세에도 연결 검사 링크 표시|P0|`templates/history/detail.html`, `templates/board/{form,detail}.html` · `routes/board.py:board\_new`|수동|
|FR-41|XSS 방어: Jinja2 자동 이스케이프 + 커스텀 `nl2br` 필터 (escape 후 개행만 `<br>`). `\|safe` 미사용|P0|`app/\_\_init\_\_.py:\_nl2br\_filter` (markupsafe.escape + Markup)|`test\_board.py::TestXSS`|

### 1.6 관리 (admin 전용)

|ID|요구사항|P|구현 위치|검증|
|-|-|-|-|-|
|FR-42|`/admin/users`: 계정 목록 (검사 건수 서브쿼리 조인). username 부분 일치·역할 필터. 페이지당 20/50/100|P0|`routes/admin.py:users\_list` · `services/admin.py:list\_users`|수동|
|FR-43|역할 변경: inspector ↔ manager ↔ admin. 안전장치 1 (자기 강등 금지)|P0|`services/admin.py:change\_role`|`test\_admin.py::TestRoleChange::test\_self\_downgrade\_blocked`|
|FR-44|활성 토글. 안전장치 2 (자기 비활성화 금지)|P0|`services/admin.py:toggle\_active`|`test\_admin.py::TestToggleActive::test\_self\_deactivate\_blocked`|
|FR-45|안전장치 3: 마지막 활성 admin 을 강등·비활성화하려 하면 `AdminError`. 시스템 admin 0명 되어 잠기지 않게 방어|P0|`services/admin.py:\_ensure\_not\_last\_admin`|`test\_admin.py::TestLastAdminSafety`|
|FR-46|비밀번호 초기화: 랜덤 12자 (secrets.token\_urlsafe(9)) 생성, 해시 저장, flash 로 1회 표시. 서버 로그에 남기지 않음|P0|`services/admin.py:reset\_password` · `routes/admin.py:users\_reset\_password`|`test\_admin.py::TestPasswordReset`|
|FR-47|`/admin/storage`: uploads 파일 수·용량·고아 수 표시. 고아 정리·보존 기간 정리 버튼|P1|`routes/admin.py:storage\_{page,cleanup\_orphan,cleanup\_old}` · `services/cleanup.py`|수동 (§3-8 시나리오)|
|FR-48|파일 정리 안전: 삭제 대상은 `UPLOADS\_DIR.resolve()` 안. `../` 등 traversal 시도는 refuse + 로그 warning. `.gitkeep` 보호|P0|`services/cleanup.py:\_safe\_delete`, `PROTECTED\_NAMES`|`test\_admin.py::TestCleanupPathTraversal`|
|FR-49|CLI 정리: `python sql/cleanup.py --orphan --old N --status`. 크론용|P2|`sql/cleanup.py`|수동|

### 1.7 운영 (에러 · 로깅)

|ID|요구사항|P|구현 위치|검증|
|-|-|-|-|-|
|FR-50|에러 페이지 403/404/500. HTML 은 커스텀 템플릿, `/api/\*` 는 JSON 응답. 500 응답에 스택 트레이스 노출 금지 (로그에만 기록)|P0|`app/\_\_init\_\_.py:error\_403/404/500/exception` · `templates/errors/\*.html`|수동 (§3-8 traceback 노출=False 확인)|
|FR-51|로깅: `logs/app.log` RotatingFileHandler (10MB × 5) + stdout. LOG\_LEVEL `.env` (기본 INFO)|P1|`app/logging\_config.py:configure\_logging`|로그 파일 확인|
|FR-52|로그 항목: 로그인 성공/실패 (사유), 검사 실행, DB 오류, 파일 정리 결과, 500 스택. **비밀번호·해시·세션 토큰 로그 금지**|P0|`services/auth.py:authenticate`, `routes/inspect.py:inspect\_page`, `services/cleanup.py:cleanup\_\*`, `app/\_\_init\_\_.py:error\_500`|수동 (grep 비밀번호 매치 0)|
|FR-53|시연 계정 seed: `sql/init\_db.py` 재실행 시 admin 계정 PASSWORD\_HASH 를 `.env` ADMIN\_PASSWORD 로 재해싱, ROLE='admin', IS\_ACTIVE=1 강제|P1|`sql/init\_db.py:ensure\_admin`|수동|
|FR-54|WSL 호스트 IP 자동 감지: `.env` 의 `ORACLE\_HOST` 비어 있으면 `ip route show \| grep default` 로 감지. 재부팅 시 자동 반영|P1|`app/config.py:\_detect\_wsl\_host\_ip`, `get\_oracle\_host`|수동|
|FR-55|Oracle Instant Client thick 모드 초기화: 진입점 상단에서 `LD\_LIBRARY\_PATH` 확인 후 미설정 시 `os.execvpe` 로 자기 자신 재실행 (shared library 순환 의존 우회)|P0|`app/db.py:ensure\_ld\_library\_path` · `run.py`·`sql/\*.py` 상단|수동 (앱 부팅 시 실측)|

\---

## 2\. 비기능 요구사항 (NFR)

### 2.1 성능

|ID|요구사항|목표·실측|
|-|-|-|
|NFR-01|앱 시작 시간|**257 ms** (§2단계 재구조화 직후 측정, legacy 3,735 ms → 93.1% 단축). **현재값 389.6 ms** (2026-09-16 3회 median 재실측, §3-7~§3-10 모듈 추가 반영). lazy load. LD\_LIBRARY\_PATH 미리 설정 시 300 ms, execvpe 재실행 시 430 ms. 상세: `docs/metrics.md` §3-1|
|NFR-02|검사 처리 시간 (warm)|실측 **850\~1,000 ms/patch** (CPU): A3 \~8 ms + YOLO \~50 ms + 세그 \~450 ms + shape \~1 ms + Advisor 0 ms + save·SPC 오버헤드|
|NFR-03|검사 처리 시간 (cold, 첫 요청)|실측 **\~5,000 ms**. 로드 시간 (YOLO \~0.6초 + 세그 \~2.4초) 포함. 이 검사는 `IS\_COLD\_START=1` 로 통계 제외|
|NFR-04|검사 이력 조회|페이지당 20건 조회 <100 ms (Oracle 원격 왕복 포함)|
|NFR-05|CSV 스트리밍|`stream\_with\_context` + `yield\_per(200)`. 전체 메모리 로드 안 함. 최대 10,000행|
|NFR-06|pytest 실행 시간|87 케이스 **\~38초** (86 pass + 1 skip)|

### 2.2 보안

|ID|요구사항|방식|
|-|-|-|
|NFR-07|비밀번호 저장|werkzeug `generate\_password\_hash` (scrypt, 알고리즘 접두 포함). `check\_password\_hash` 로 검증. 직접 구현 없음|
|NFR-08|세션 보호|Flask 서명 쿠키. `SECRET\_KEY` `.env`. 세션 키는 `user\_id`·`role` 두 개만. 해시·비밀번호 미저장|
|NFR-09|XSS 방어|Jinja2 자동 이스케이프 + 커스텀 `nl2br` (`markupsafe.escape` 후 개행만 `<br>`). `\|safe` 사용 없음. 실측 `<script>alert(1)</script>` 이 `\&lt;script\&gt;alert(1)\&lt;/script\&gt;` 로 렌더|
|NFR-10|Open-redirect 방지|`\_safe\_next\_url` 이 `/` 시작 상대 경로만 허용, `//` 시작 거부|
|NFR-11|권한 이중 검사|데코레이터 + 서비스 계층 (mark\_reviewed·change\_role·delete\_post 등 서비스에서 role/owner 재검증). 템플릿 UI 분기만으로 판단하지 않음|
|NFR-12|Path traversal 방어|`\_safe\_delete` 가 `UPLOADS\_DIR.resolve()` 기준 `relative\_to()` 검사. 밖 경로 거부 + 로그 warning|
|NFR-13|SQL injection 방어|SQLAlchemy ORM · named binding. 원시 SQL 없음|
|NFR-14|로그 마스킹|비밀번호·해시·세션 토큰 로그 미출력. `authenticate` 는 사유(`reason=bad\_password`) 만 남기고 값 미포함. grep 매치 0|
|NFR-15|500 응답 정보 노출 방지|커스텀 500 페이지 (HTML), `{"error":"internal server error"}` (API). 스택은 로그 파일에만|

### 2.3 가용성 · 회복탄력성

|ID|요구사항|방식|
|-|-|-|
|NFR-16|DB 장애 시 검사 기능 유지|`save\_inspection` 실패 시 flash 로 알리고 결과 화면 렌더 유지 (`routes/inspect.py`). 실측 monkey-patch 시나리오 (§3-3)|
|NFR-17|SPC 처리 실패 격리|`\_process\_spc\_and\_status` 는 별도 트랜잭션. 실패해도 검사 저장은 유지, 로그 error 만|
|NFR-18|모델 로드 실패 격리|`ml/loader.py` 의 실패는 에러 슬롯에 보관, 재시도 안 함. 라우트가 `get\_yolo() is None` 확인 후 안내|
|NFR-19|DB 연결 재확인|커넥션 풀 `pool\_size=5, max\_overflow=5, pool\_pre\_ping=True`|
|NFR-20|WSL 호스트 IP 재부팅 대응|매 호출 시 `ip route show` 로 재감지 (lazy)|

### 2.4 유지보수성

|ID|요구사항|방식|
|-|-|-|
|NFR-21|계층 분리|`routes → services → models → db → config` 단방향 (역참조 없음, grep 확인)|
|NFR-22|Lazy 로드|모듈 import 시 파일·모델·DB 접근 없음. 최초 사용 시 로드 + 캐시|
|NFR-23|자동화 테스트 커버리지|실측 **69%** (전체 2,430 statements 중 760 miss). pytest 87 케이스|
|NFR-24|격리 가능한 테스트|pytest\_ 접두사 사용자 + finalizer. 3회 연속 실행 후 잔여 0. 기존 admin/bob/manager1 무결성 유지|
|NFR-25|문서화|`docs/{architecture, schema, routes, testing, requirements, uml}.md`. Oracle 카탈로그 실측 기반|
|NFR-26|로그 회전|RotatingFileHandler (10 MB × 5 백업). 디스크 무한 증가 방지|

### 2.5 이식성

|ID|요구사항|방식|
|-|-|-|
|NFR-27|Oracle 11g 이식|IDENTITY 대신 SEQUENCE+TRIGGER (7개), BOOLEAN 대신 NUMBER(1)+CHECK, ENUM 대신 VARCHAR2+CHECK, LIMIT/OFFSET 은 SQLAlchemy 가 ROWNUM 서브쿼리 자동 변환|
|NFR-28|Instant Client thick 모드|`oracledb.init\_oracle\_client(lib\_dir=...)`. `LD\_LIBRARY\_PATH` execvpe wrapper|
|NFR-29|환경 변수 격리|`.env` (gitignored) · `.env.example` (커밋). ORACLE\_\*, ADMIN\_PASSWORD, SECRET\_KEY, UPLOAD\_RETENTION\_DAYS, LOG\_LEVEL|

\---

## 3\. 제약사항

|ID|제약|대응|
|-|-|-|
|C-01|Oracle 11g XE 는 IDENTITY 컬럼·BOOLEAN·ENUM·LIMIT/OFFSET 미지원|각각 SEQUENCE+TRIGGER, NUMBER(1)+CHECK, VARCHAR2+CHECK, ROWNUM 서브쿼리 (`docs/schema.md` §Oracle 11g 제약과 대응)|
|C-02|Oracle 11g XE 는 python-oracledb thin 모드 미지원 → thick 모드 필수|Instant Client 19.32 + `LD\_LIBRARY\_PATH` execvpe wrapper (`app/db.py:ensure\_ld\_library\_path`)|
|C-03|Instant Client shared library 순환 의존 (libnnz19 ↔ libclntshcore.so.19.1 ↔ libclntsh.so.19.1). rpath 부재로 ctypes 선로딩만으로 해결 불가|진입점에서 `LD\_LIBRARY\_PATH` 세팅 후 `os.execvpe` 로 재실행. 재실행 오버헤드 \~130 ms|
|C-04|WSL–Windows 네트워크 구성. WSL 게이트웨이 IP 가 재부팅 시 변경|`.env` ORACLE\_HOST 비우면 `ip route show` 로 매 호출마다 자동 감지 (lazy)|
|C-05|세그멘테이션 CPU 추론 \~450 ms/patch (GPU 미측정, RunPod 3090 학습만 사용)|실시간이 아닌 요청당 1 회 실행. 스트림 재생은 캐시된 YOLO 만 사용|
|C-06|SPC 관리한계는 baseline 30점 완성 후에만 계산. 이전에는 알람 판정 불가 (수집 단계)|baseline 진행률을 `/spc` 페이지에 표시. 실제 라인 운영 시 최초 30개 정상 검사가 필요|
|C-07|CoatingVision 데이터셋 라이선스 CC BY-NC-ND 4.0. 재배포·가공 재배포 제한|`.gitignore` 에 `classification/`·`detection/`·`segmentation/`·`app/static/samples/\*.jpg` 등록. 저장소 커밋 대상 아님|
|C-08|서버 시간대는 UTC (SYSTIMESTAMP 기본, `datetime.utcnow()` 사용). Excel 표시 시 UTC 명시 필요|CSV 컬럼 "일시" 는 `%Y-%m-%d %H:%M:%S` UTC. 시연 시 안내|
|C-09|Flask 개발 서버 (`werkzeug`) 사용. 프로덕션 WSGI (gunicorn 등) 미채택|시연 목적으로 개발 서버 유지. 프로덕션 배포 시 별도 구성 필요|
|C-10|인스턴스 세그멘테이션·시맨틱 세그 delam 은 안정성 한계 (§20-5)|delam 은 참고값. 대시보드·리포트에 "§20-5 참고값" 명시|

\---

## 4\. 범위 밖 (Out of Scope)

이 프로젝트가 **구현하지 않기로 한** 항목과 그 이유. 요청 시 별도 스코프.

|항목|이유|
|-|-|
|실시간 스트리밍 검사 (프로덕션 라인 카메라 연결)|시연 목적 시스템. `/stream` 은 저장된 프레임 재생 시뮬레이션만|
|다중 라인 동시 모니터링|SPC 는 단일 metric 시계열 (a3, seg\_crack). 라인 구분 없음|
|게시판 첨부파일 업로드 (게시글에 이미지 첨부)|XSS·저장소 관리 복잡도. 검사 이미지 4장은 별도 INSPECTIONS 컬럼|
|알림 발송 (이메일·SMS·Slack)|인프라 종속성. 알람은 `/spc` 화면 + `IS\_ALARM` 플래그만|
|다중 이미지 배치 검사 (파일 여러 개 한 번에)|단일 파일 업로드로 유지. 배치는 오프라인 `17\_precompute\_detections.py` 담당|
|WebSocket 실시간 갱신|`/stream` 은 JSON 폴링 아닌 클라이언트 자동 재생. 서버 push 없음|
|국제화 (i18n)|한국어 고정 (템플릿 · flash 메시지)|
|다크 모드 · 반응형 모바일|1920x1080 캡처 상정 (`style.css` 주석). 모바일 미대응|
|OAuth · SSO|자체 세션 · 비밀번호. 외부 IdP 연동 없음|
|검사 이미지 편집 · 주석 도구|상세 페이지는 조회만. 마킹 추가 없음|
|관리자용 CLI 도구 (사용자 승격 등)|admin 이 웹 UI (`/admin/users`) 로 처리. `sql/init\_db.py` 는 admin seed 만|
|알람 이력 페이지 (별도 화면)|`/spc` 알람 목록 + `/history/all?status=alarm` 필터로 대체|
|이력 이미지 클릭 확대 뷰|상세 페이지에 원본 크기로 표시. 라이트박스 없음|
|REST API 완전 노출 (외부 시스템 연동)|`/api/sequence/\*`·`/api/spc/series` 만 존재. CRUD API 없음|
|대량 검사 삭제 (bulk delete)|상세 페이지에서 개별 삭제만. 관리자용 저장소 정리 (`/admin/storage`) 는 파일 단위|
|통계 대시보드 상세 (기간별 그래프 등)|로그인 사용자 요약 카드 (총·최근 7일·평균 crack·최근 5건) 만. 상세 통계는 CSV 내보내기로 대체|

\---

## 5\. 검증 요약

|검증 방법|대상|
|-|-|
|pytest 자동화 (87 케이스)|FR-01\~06, FR-09\~12, FR-18\~24, FR-27\~35, FR-37\~46, FR-48. `tests/` 아래 6 파일|
|수동 스모크 (curl + 세션)|라우트 36개 (2026-09-16 재실측), 4 세션 매트릭스, 파이프라인 값 대조 (crack 3.01%, delam 0.00%)|
|로그 확인 (grep)|NFR-14 비밀번호 미노출|
|시나리오 재현|§3-3 DB 장애, §3-4 알람 발생, §3-5 리포트 부재 시 거부, §3-8 파일 정리, §3-9 페이지네이션 137건|
|Oracle 카탈로그 조회|스키마 (`docs/schema.md`) · CASCADE 실측|



