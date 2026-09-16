# UML 다이어그램 (§3-11)

작성 시점: 2026-09-14. 모든 다이어그램은 **mermaid** 문법. 코드를 읽어 실제 구조와 일치시켰다.

**중복 회피**: **ERD (테이블 관계)** 는 [`docs/architecture.md` §3](architecture.md) 에 존재하고, 여기서는 참조만. UML 은 **Use Case · Class · Sequence** 를 다룬다.

관련: [`docs/requirements.md`](requirements.md) · [`docs/routes.md`](routes.md) · [`docs/schema.md`](schema.md).

---

## 1. Use Case Diagram

Mermaid 는 UML use case 를 정식으로 지원하지 않는다. `flowchart` 로 액터 (왼쪽) 와 유스케이스 (오른쪽) 를 표현. `<<include>>`·`<<extend>>` 는 화살표 라벨.

```mermaid
flowchart LR
    subgraph Actors["액터"]
        Anon(("비인증 사용자"))
        Inspector(("검사자<br/>inspector"))
        Manager(("품질관리자<br/>manager"))
        Admin(("관리자<br/>admin"))
    end

    Inspector -.->|is-a| Anon
    Manager -.->|is-a| Inspector
    Admin -.->|is-a| Manager

    subgraph Auth["인증"]
        UC01[회원가입]
        UC02[로그인]
        UC03[로그아웃]
        UC04[비밀번호 변경]
        UC05[프로필 수정]
    end

    subgraph Inspect["검사"]
        UC10[이미지 업로드·검사 실행]
        UC11[예시 샘플 검사]
        UC12[검사 결과 조회]
    end

    subgraph History["이력"]
        UC20[내 이력 조회]
        UC21[검사 상세 조회]
        UC22[검사 삭제]
        UC23[CSV 내보내기]
        UC24[전체 이력 조회]
    end

    subgraph SPC["공정관리"]
        UC30[SPC 관리도 조회]
        UC31[알람 목록 조회]
        UC32[조치 완료 처리]
    end

    subgraph Board["게시판"]
        UC40[글 작성]
        UC41[글 조회·수정·삭제]
        UC42[리포트 작성 검사 연결]
        UC43[댓글 CRUD]
        UC44[공지사항 작성]
    end

    subgraph Admin_UC["관리"]
        UC50[계정 목록·역할 변경]
        UC51[활성 토글]
        UC52[비밀번호 초기화]
        UC53[저장소 정리]
    end

    Anon --> UC01
    Anon --> UC02

    Inspector --> UC03
    Inspector --> UC04
    Inspector --> UC05
    Inspector --> UC10
    Inspector --> UC11
    Inspector --> UC12
    Inspector --> UC20
    Inspector --> UC21
    Inspector --> UC22
    Inspector --> UC23
    Inspector --> UC40
    Inspector --> UC41
    Inspector --> UC42
    Inspector --> UC43

    Manager --> UC24
    Manager --> UC30
    Manager --> UC31
    Manager --> UC32
    Manager --> UC44

    Admin --> UC50
    Admin --> UC51
    Admin --> UC52
    Admin --> UC53

    UC10 -. include .-> UC02
    UC22 -. include .-> UC02
    UC32 -. include .-> UC42
    UC32 -. extend .-> UC30
```

**설명**:
- 액터는 4계층 (Anon → Inspector → Manager → Admin) 이며 위계 상속으로 상위가 하위 유스케이스를 모두 사용 가능. `@role_required` 데코레이터의 `ROLE_RANK` 위계와 일치.
- 실선 화살표 = 액터가 유스케이스를 직접 수행. 점선 = 상속·include·extend.
- **UC32 (조치 완료 처리)** 는 UC42 (리포트 작성 검사 연결) 를 `include` — `services/inspection_store.py:mark_reviewed` 가 `has_report_for_inspection` 을 필수 조건으로 확인.
- **UC32** 는 UC30 (SPC 관리도 조회) 을 `extend` — 알람 목록에서 곧바로 이동하는 확장 흐름. 필수 아님 (검사 상세 페이지에서 시작도 가능).
- UC44 (공지사항 작성) 는 manager 이상 전용 — UC40 (일반 글 작성) 의 카테고리 정책 분기이며 `services/board.py:create_post` 안에서 role 검사.

---

## 2. Class Diagram

### 2.1 도메인 모델 (ORM 7 클래스)

`app/models.py` 실제 클래스 · 컬럼 · relationship. Oracle 컬럼 이름은 대문자, ORM 속성은 소문자.

```mermaid
classDiagram
    class User {
        +Numeric id  PK
        +String(50) username  UK NOT NULL
        +String(255) password_hash  NOT NULL
        +String(120) email  UK
        +String(100) full_name
        +String(20) role
        +Numeric(1) is_active
        +TIMESTAMP created_at
        +TIMESTAMP last_login_at
        +inspections : List[Inspection]
        +posts : List[Post]
    }

    class Inspection {
        +Numeric id  PK
        +Numeric user_id  FK
        +String(255) file_name
        +String(512) image_path
        +String(512) heatmap_path
        +String(512) annotated_path
        +String(512) seg_path
        +String(20) source
        +TIMESTAMP created_at
        +Numeric(10,6) a3_ratio
        +Numeric(5) a3_cells
        +Numeric(10,6) seg_crack
        +Numeric(10,6) seg_delam
        +Numeric(5) pinhole_count
        +Numeric(10,3) ms_a3
        +Numeric(10,3) ms_yolo
        +Numeric(10,3) ms_seg
        +Numeric(10,3) ms_total
        +Numeric(1) is_cold_start
        +String(20) status
        +Numeric reviewed_by  FK
        +TIMESTAMP reviewed_at
        +user : User  (foreign_keys=user_id)
        +reviewer : User  (foreign_keys=reviewed_by)
        +detections : List[Detection]
        +spc_points : List[SpcPoint]
        +findings : List[Finding]
        +posts : List[Post]
    }

    class Detection {
        +Numeric id  PK
        +Numeric inspection_id  FK NOT NULL
        +Numeric(7,2) x1
        +Numeric(7,2) y1
        +Numeric(7,2) x2
        +Numeric(7,2) y2
        +Numeric(5,4) conf
        +Numeric(7,3) aspect
        +Numeric(5,4) roundness
        +inspection : Inspection
    }

    class SpcPoint {
        +Numeric id  PK
        +Numeric inspection_id  FK NOT NULL
        +String(20) metric
        +Numeric(7) seq_no
        +Numeric(10,6) value
        +Numeric(10,6) ewma
        +Numeric(10,6) center
        +Numeric(10,6) ucl
        +Numeric(1) is_alarm
        +inspection : Inspection
    }

    class Finding {
        +Numeric id  PK
        +Numeric inspection_id  FK NOT NULL
        +String(20) layer
        +String(4000) content
        +String(10) confidence
        +String(100) source
        +inspection : Inspection
    }

    class Post {
        +Numeric id  PK
        +Numeric user_id  FK
        +Numeric inspection_id  FK nullable
        +String(20) category
        +String(200) title  NOT NULL
        +String(4000) content
        +Numeric(7) view_count
        +TIMESTAMP created_at
        +TIMESTAMP updated_at
        +user : User
        +inspection : Inspection
        +comments : List[Comment]
    }

    class Comment {
        +Numeric id  PK
        +Numeric post_id  FK NOT NULL
        +Numeric user_id  FK
        +String(1000) content
        +TIMESTAMP created_at
        +post : Post
        +user : User
    }

    User "1" --o "N" Inspection : owns (user_id)
    User "1" --o "N" Inspection : reviews (reviewed_by)
    User "1" --o "N" Post : writes
    User "1" --o "N" Comment : writes
    Inspection "1" *-- "N" Detection : cascade
    Inspection "1" *-- "N" SpcPoint : cascade
    Inspection "1" *-- "N" Finding : cascade
    Inspection "1" --o "N" Post : linked (nullable)
    Post "1" *-- "N" Comment : cascade
```

**설명**:
- **7 클래스 · 21 개 관계** (11 FK, 3 CASCADE). ERD 는 `docs/architecture.md` §3, 컬럼 상세는 `docs/schema.md`.
- `User ↔ Inspection` 은 FK 두 개 (`user_id`·`reviewed_by`) 로 관계 두 개. `foreign_keys=` 명시로 SQLAlchemy AmbiguousForeignKeysError 회피 (§3-5 이슈).
- `*--` (composition, CASCADE): 부모 삭제 시 자식도 삭제 (Oracle `ON DELETE CASCADE` FK 로 실제 구현). `--o` (aggregation): FK 는 있으나 부모 삭제 시 자식은 유지 (또는 NULL).
- `Inspection.posts` 는 nullable — 일반 게시글은 검사 연결 없음. report 카테고리만 검사와 연결.

### 2.2 애플리케이션 구조 (계층·모듈)

실제 import 관계. 화살표는 "of module A imports module B" 방향.

```mermaid
classDiagram
    class run_py["run.py<br/>진입점"]
    class create_app["app.__init__<br/>create_app 팩토리"]

    class routes_main["routes.main<br/>/ /figures /frame"]
    class routes_auth["routes.auth<br/>/login /register /logout"]
    class routes_account["routes.account<br/>/account /account/password"]
    class routes_inspect["routes.inspect<br/>/inspect /detection"]
    class routes_history["routes.history<br/>/history /export /review"]
    class routes_board["routes.board<br/>/board*"]
    class routes_spc["routes.spc<br/>/spc"]
    class routes_admin["routes.admin<br/>/admin/users /admin/storage"]
    class routes_stream["routes.stream<br/>/stream"]
    class routes_benchmark["routes.benchmark<br/>/benchmark"]
    class api_sequence["api.sequence<br/>/api/sequence"]
    class api_spc["api.spc<br/>/api/spc/series"]

    class services_pipeline["services.pipeline<br/>run_inspection"]
    class services_auth["services.auth<br/>register authenticate ..."]
    class services_inspection["services.inspection_store<br/>save get delete mark_reviewed"]
    class services_spc["services.spc<br/>add_spc_point compute_control_limits"]
    class services_board["services.board<br/>create_post get_posts ..."]
    class services_admin["services.admin<br/>list_users change_role ..."]
    class services_cleanup["services.cleanup<br/>delete_inspection_files ..."]

    class ml_loader["ml.loader<br/>get_yolo get_seg is_*_loaded"]
    class ml_anomaly["ml.anomaly<br/>run_anomaly_stage"]
    class ml_detect["ml.detect<br/>run_detect_stage"]
    class ml_segment["ml.segment<br/>run_seg_stage"]
    class ml_shape["ml.shape<br/>run_shape_stage"]
    class ml_advisor["ml.advisor<br/>build_advisor"]

    class models["models.py<br/>User Inspection Detection SpcPoint Finding Post Comment"]
    class db["db.py<br/>engine session ensure_ld_library_path"]
    class store["store.py<br/>get_advisor get_kpi ..."]
    class config["config.py<br/>경로 DSN"]
    class auth_utils["auth_utils.py<br/>@login_required @role_required @api_login_required"]
    class logging_conf["logging_config.py<br/>configure_logging"]

    run_py --> create_app
    create_app --> routes_main
    create_app --> routes_auth
    create_app --> routes_account
    create_app --> routes_inspect
    create_app --> routes_history
    create_app --> routes_board
    create_app --> routes_spc
    create_app --> routes_admin
    create_app --> routes_stream
    create_app --> routes_benchmark
    create_app --> api_sequence
    create_app --> api_spc
    create_app --> logging_conf

    routes_auth --> services_auth
    routes_account --> services_auth
    routes_inspect --> services_pipeline
    routes_inspect --> services_inspection
    routes_inspect --> ml_loader
    routes_inspect --> auth_utils
    routes_history --> services_inspection
    routes_history --> services_auth
    routes_history --> auth_utils
    routes_board --> services_board
    routes_board --> services_auth
    routes_board --> auth_utils
    routes_spc --> services_spc
    routes_spc --> auth_utils
    routes_admin --> services_admin
    routes_admin --> services_auth
    routes_admin --> services_cleanup
    routes_admin --> auth_utils
    routes_main --> services_auth
    routes_main --> services_inspection
    routes_main --> store
    routes_main --> auth_utils
    routes_stream --> store
    routes_stream --> auth_utils
    routes_benchmark --> store
    routes_benchmark --> auth_utils
    api_sequence --> store
    api_sequence --> auth_utils
    api_spc --> services_spc
    api_spc --> services_auth
    api_spc --> auth_utils

    services_pipeline --> ml_anomaly
    services_pipeline --> ml_detect
    services_pipeline --> ml_segment
    services_pipeline --> ml_shape
    services_pipeline --> ml_advisor
    services_pipeline --> store

    services_inspection --> models
    services_inspection --> db
    services_inspection --> services_spc
    services_inspection --> services_cleanup
    services_auth --> models
    services_auth --> db
    services_board --> models
    services_board --> db
    services_spc --> models
    services_spc --> db
    services_admin --> models
    services_admin --> db
    services_cleanup --> models
    services_cleanup --> db
    services_cleanup --> config

    ml_detect --> ml_loader
    ml_segment --> ml_loader
    ml_shape --> store
    ml_advisor --> store

    models --> db
    db --> config
    store --> config
    auth_utils --> services_auth
    logging_conf --> config
```

**설명**:
- 화살표 방향 = **import 방향**. 위쪽 (라우트) 에서 아래쪽 (config) 을 참조. 역방향 없음 (grep 확인 완료).
- `auth_utils.py` 는 `services.auth` 만 참조하고 routes 를 직접 import 하지 않음 (`url_for` endpoint 이름만 사용).
- `services/*.py` 는 서로 참조 가능 (같은 계층). 예: `inspection_store` 가 `spc` · `cleanup` · `board` 사용. 순환 없음.
- `ml/*.py` 는 서비스·라우트를 참조 안 함. 순수 계산 계층.
- `db.py`·`store.py`·`config.py` 는 최하위 (다른 것을 import 안 함).

---

## 3. Sequence Diagrams

### 3.1 로그인 (POST /login)

```mermaid
sequenceDiagram
    autonumber
    actor U as 사용자
    participant BR as 브라우저
    participant RT as routes.auth<br/>login_page
    participant SVC as services.auth<br/>authenticate
    participant DB as Oracle DB
    participant SESS as Flask Session

    U->>BR: username·password 폼 제출
    BR->>RT: POST /login (form)
    RT->>RT: _safe_next_url(request.args['next'])
    RT->>SVC: authenticate(username, password)
    SVC->>DB: SELECT USERS WHERE USERNAME=?
    DB-->>SVC: User row (또는 None)

    alt user is None
        SVC-->>RT: None
        RT->>RT: log.warning "unknown_user"
        RT-->>BR: 401 + flash-error
    else is_active = 0
        SVC-->>RT: None (log warning "inactive")
        RT-->>BR: 401 + flash-error
    else bad password
        SVC->>SVC: check_password_hash → False
        SVC-->>RT: None (log warning "bad_password")
        RT-->>BR: 401 + flash-error
    else success
        SVC->>DB: UPDATE LAST_LOGIN_AT
        SVC->>SVC: log.info "Login success"
        SVC-->>RT: user_snapshot {id, username, role}
        RT->>SESS: session.clear()<br/>session[user_id, role] = ...
        RT-->>BR: 302 → posted_next or /
    end
```

**설명**:
- 로그인은 `services.auth.authenticate` 하나로 검증 (unknown_user / inactive / bad_password / success). 각 사유별 로그 warning/info. 비밀번호 값은 로그에 남지 않음 (사유 문자열만).
- 세션은 `user_id`·`role` 두 키만 저장. Flask 서명 쿠키.
- `next` 파라미터는 `_safe_next_url` 통과한 경우에만 사용 — open-redirect 방지.

### 3.2 검사 실행 (POST /inspect, DB 저장 실패 alt 포함)

```mermaid
sequenceDiagram
    autonumber
    actor U as 검사자
    participant BR as 브라우저
    participant RT as routes.inspect<br/>inspect_page
    participant LD as ml.loader
    participant PL as services.pipeline<br/>run_inspection
    participant ML as ml.{anomaly,detect,segment,shape,advisor}
    participant IS as services.inspection_store
    participant SPC as services.spc
    participant DB as Oracle DB
    participant FS as static/uploads/

    U->>BR: 이미지 파일 폼 제출
    BR->>RT: POST /inspect (multipart)
    RT->>RT: @login_required 통과
    RT->>LD: is_yolo_loaded() / is_seg_loaded() (콜드 여부 캡처)
    RT->>PL: run_inspection(upload)

    PL->>FS: upload.save(orig_path)
    PL->>ML: ml.anomaly.run_anomaly_stage(image, threshold)
    PL->>ML: ml.anomaly.save_heatmap(...)
    PL->>ML: ml.detect.run_detect_stage(image)
    PL->>ML: ml.segment.run_seg_stage(image, seg_path)
    PL->>ML: ml.shape.run_shape_stage(detections)
    PL->>ML: ml.advisor.build_advisor(...)
    PL-->>RT: result payload (dict)

    RT->>LD: is_yolo_loaded() / is_seg_loaded() 재확인 (콜드 판정)

    RT->>IS: save_inspection(result, user_id, ...)

    alt DB 정상
        IS->>DB: BEGIN Tx-A
        IS->>DB: INSERT INSPECTIONS
        IS->>DB: INSERT DETECTIONS × N
        IS->>DB: INSERT FINDINGS × N
        IS->>DB: COMMIT Tx-A
        IS-->>IS: new_id 확보
        IS->>SPC: add_spc_point('a3', ratio)  (Tx-B)
        IS->>SPC: add_spc_point('seg_crack', ratio)  (Tx-B)
        SPC->>DB: INSERT SPC_POINTS (baseline 또는 monitor)
        SPC-->>IS: is_alarm?
        opt alarm 발생
            IS->>DB: UPDATE INSPECTIONS SET STATUS='alarm'  (Tx-C)
        end
        IS-->>RT: new_id
        RT->>RT: log.info "Inspection saved id=..."
        RT-->>BR: 200 HTML (결과 4장 + inspection_id + "이력에서 보기")
    else DB 실패
        IS-->>RT: raise Exception
        RT->>RT: log.error "Inspection save failed"
        RT-->>BR: 200 HTML (결과 렌더 유지 + flash-error "DB 저장 실패")
    end
```

**설명**:
- 파이프라인은 **5 스테이지 순차 실행**. 파일 저장 · 이미지 처리는 request context 안에서. 모델은 lazy 로드 (첫 요청 시).
- **트랜잭션 3개 분리**: Tx-A (INSPECTIONS + DETECTIONS + FINDINGS), Tx-B (SPC × 2), Tx-C (STATUS 갱신). Tx-A 실패 → 전체 롤백 + flash. Tx-B/C 실패는 로그만 (검사 저장은 유지).
- **alt "DB 실패"**: `save_inspection` 예외 → 결과 payload 는 이미 파일로 저장돼 있으므로 화면 렌더는 유지. `inspection_id` 는 payload 에 세팅 안 됨 → "이력에서 보기" 버튼 숨겨짐.
- 콜드 스타트 감지는 파이프라인 전후 `is_yolo_loaded()` 비교로 판정, `IS_COLD_START=1` 저장 시 대시보드 평균 crack 통계에서 제외.

### 3.3 알람 발생 → 조치 완료 전이

```mermaid
sequenceDiagram
    autonumber
    actor MGR as 매니저
    participant BR as 브라우저
    participant HR as routes.history<br/>history_review
    participant IS as services.inspection_store<br/>mark_reviewed
    participant BS as services.board<br/>has_report_for_inspection
    participant DB as Oracle DB

    Note over IS,DB: [사전] 검사 저장 시 SPC add_spc_point 가 UCL 초과 감지 →<br/>INSPECTIONS.STATUS='alarm' 자동 전이 (§3.2 alt "alarm 발생")

    MGR->>BR: /history/<id> 방문
    BR->>HR: GET /history/<id>
    HR-->>BR: 상세 페이지 (status=alarm → "조치 완료 처리" 버튼 표시)

    MGR->>BR: "조치 완료 처리" 클릭
    BR->>HR: POST /history/<id>/review

    HR->>IS: mark_reviewed(inspection_id, viewer_user_id, viewer_role)

    alt role 이 manager/admin 아님
        IS-->>HR: (False, "매니저 이상만...")
        HR->>BR: 302 + flash-error
    else role 통과
        IS->>BS: has_report_for_inspection(inspection_id)
        BS->>DB: SELECT COUNT(*) FROM POSTS WHERE INSPECTION_ID=? AND CATEGORY='report'
        DB-->>BS: count

        alt count == 0
            BS-->>IS: False
            IS-->>HR: (False, "이 검사에 연결된 리포트가 없습니다...")
            HR->>BR: 302 + flash-error (STATUS 유지)
        else count >= 1
            BS-->>IS: True
            IS->>DB: SELECT INSPECTIONS WHERE ID=?
            IS->>IS: status == 'alarm' 확인
            IS->>DB: UPDATE INSPECTIONS<br/>SET status='reviewed', reviewed_by=?, reviewed_at=SYSTIMESTAMP
            IS-->>HR: (True, None)
            HR->>BR: 302 + flash-success "reviewed 상태로 전이"
        end
    end
```

**설명**:
- **선행 조건**: SPC 알람은 검사 저장 시 자동 판정 (§3.2 alt). 이 시퀀스는 조치 완료 흐름만.
- **거부 사유 세 층**: (1) role 부족, (2) report 부재, (3) STATUS 이미 reviewed 이거나 미존재. 모두 `mark_reviewed` 서비스 안에서 판정하고 사유를 반환 → 라우트가 flash-error 로 전달.
- `abort(403)` 대신 `redirect + flash` — 사유가 여러 가지라 사용자에게 알리기 위함 (§3-6 설계 노트).
- STATUS 유지: 거부 시 DB 변경 없음. `reviewed_by`·`reviewed_at` 은 성공 시에만 기록.

### 3.4 권한 검사 (HTML vs API 분기)

```mermaid
sequenceDiagram
    autonumber
    actor U as 사용자
    participant BR as 브라우저·클라이언트
    participant DEC as auth_utils.py<br/>데코레이터
    participant SVC as services.auth<br/>get_current_user
    participant DB as Oracle DB
    participant RT as 라우트 함수

    U->>BR: HTTP 요청 (쿠키 포함 또는 미포함)
    BR->>DEC: 요청이 라우트에 도달하기 전 데코레이터 실행

    DEC->>SVC: get_current_user()
    SVC->>SVC: session['user_id'] 조회
    alt session 없음
        SVC-->>DEC: None
    else session 있음
        SVC->>DB: SELECT USERS WHERE ID=?
        DB-->>SVC: user row
        alt is_active = 0 or 사용자 미존재
            SVC-->>DEC: None
        else 정상
            SVC-->>DEC: user_snapshot {id, username, role}
        end
    end

    alt current_user is None
        alt @login_required (HTML 라우트)
            DEC-->>BR: 302 → /login?next=<request.full_path>
        else @api_login_required (API 라우트)
            DEC-->>BR: 401 JSON<br/>{"error":"authentication required"}
        end
    else current_user 있음
        alt @role_required('manager','admin')
            DEC->>DEC: ROLE_RANK[user.role] >= min_rank ?
            alt 부족
                DEC-->>BR: abort(403) → 403 HTML 페이지
                Note over BR: /api/* 요청이면 errorhandler 가<br/>403 JSON {"error":"forbidden"} 반환
            else 통과
                DEC->>RT: view_function(*args, **kwargs)
                RT-->>BR: 200 (또는 302 등)
            end
        else @api_login_required + 내부 role 체크 (예: api/spc)
            DEC->>RT: view_function
            RT->>RT: role in ('manager','admin') ?
            alt 부족
                RT-->>BR: 403 JSON {"error":"role manager or admin required"}
            else 통과
                RT-->>BR: 200 JSON
            end
        end
    end
```

**설명**:
- **HTML vs API 분기 3 층**: (1) 데코레이터 종류로 미로그인 응답 분기 (302 vs 401 JSON), (2) `role_required` 는 HTML 라우트에서 `abort(403)` → `errorhandler` 가 요청 경로 (`/api/*` 여부) 로 최종 형식 결정, (3) `api_spc.spc_series` 는 내부에서 role 체크해 403 JSON 반환.
- `get_current_user` 는 매 요청마다 DB 를 조회 (세션 캐시 아님). 이유: 역할 변경·비활성화가 즉시 반영되어야 함 (§3-7 admin 이 bob 을 manager 로 승격 시 bob 의 다음 요청부터 /spc 200).
- `errorhandler` 는 `app/__init__.py` 안에서 등록. `_is_api_request()` = `request.path.startswith("/api/")`.
