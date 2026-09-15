# 개발 프로세스

전극 코팅 결함 인라인 검사 시스템의 개발 진행 순서.

이 프로젝트는 **기능 단위로 요구사항 명세를 먼저 작성하고 구현하는 방식**으로 진행했다. 각 명세에는 기능 항목·제약 조건·범위 배제·검증 기준을 포함시켰다. **전체 요구사항 분석서 (`docs/requirements.md`)** 는 §3-1 ~ §3-11 10개 단계의 개별 명세를 **통합·정규화**하여 §3-11 단계에서 작성했다. 즉 요구사항 분석서는 개별 명세보다 나중에 작성된 산출물이며, FR/NFR 번호도 이 통합 시점에 부여됐다.

문서 구성:
- §1 요구사항 분석서 → `docs/requirements.md` (통합·정규화 결과)
- §2 ERD → `docs/architecture.md` §3, `docs/schema.md`
- §3 UML → `docs/uml.md` (Use Case · Class × 2 · Sequence × 4)
- §4 개발 → 실험 스크립트 01~56 + Flask 계층 분리 + 기능 단계 §3-1~§3-6
- §5 테스트 → `docs/testing.md`, `tests/`
- §6 운영 → 로깅 · 회복탄력성 · 알람 워크플로 · 배포 절차

---

## 1. 요구사항 분석서

**산출물**: `docs/requirements.md`

먼저 도메인·이해관계자·제약을 정리해 시스템의 목적과 범위를 확정했다. 스마트팩토리 품질관리 시스템으로서 검사 결과가 라인 운영자·매니저·관리자 세 계층에 서로 다른 정보로 제공되어야 하고, 이력·SPC·게시판·관리 기능이 통합되어야 한다는 점을 요구사항으로 문서화했다.

### 1-1. 요구사항 범주

| 범주 | 항목 수 | 내용 |
|---|---|---|
| 기능 요구사항 (FR) | 55 | 인증 · 검사 · 이력 · SPC · 게시판 · 관리 · 운영 |
| 비기능 요구사항 (NFR) | 29 | 성능 · 보안 · 가용성 · 유지보수성 · 이식성 |
| 제약사항 (C) | 10 | Oracle 11g 문법 · thick 모드 · WSL 네트워크 · 라이선스 등 |
| 범위 밖 (Out of Scope) | 15 | 실시간 스트리밍 · 다중 라인 · OAuth · i18n 등 |

### 1-2. 우선순위 부여

- **P0** (시연 필수): 회원가입/로그인, 검사 파이프라인, 이력 저장·조회, SPC 알람 판정, 게시판 CRUD, 역할 위계
- **P1** (통합 지원): CSV 내보내기, 대시보드 요약, 저장소 정리 UI, 로그 회전
- **P2** (편의): CLI 정리 크론

### 1-3. 주요 기능 요구사항

- **FR-13 ~ FR-17 검사 파이프라인**: 이미지 업로드 또는 샘플 3장 → A3 이상 탐지 → YOLO 핀홀 검출 → U-Net 세그 → 형태 분석 → Advisor 3층 리포트 → DB 저장 → SPC 갱신 → 4장 이미지 저장
- **FR-28 ~ FR-35 SPC**: baseline 30점 수집, UCL = CENTER + 3.5σ, EWMA λ=0.1, 알람 판정 시 INSPECTIONS.STATUS='alarm' 전이, `/spc` (매니저 이상), 알람 조치 완료는 report 게시글 존재 확인
- **FR-42 ~ FR-49 관리**: 계정 목록, 역할 변경 (안전장치 3), 활성 토글, 비밀번호 초기화 (랜덤 12자), 마지막 활성 admin 보호, uploads 파일 정리 (고아·보존기간)

### 1-4. 주요 비기능 요구사항

- **NFR-01**: 앱 시작 시간 500 ms 이하 목표 (§2단계 재구조화 직후 257 ms · 현재 389.6 ms, 2026-09-16 재실측)
- **NFR-02**: 검사 처리 warm 1,000 ms/patch 이하 (실측 850~1,000)
- **NFR-07**: 비밀번호는 werkzeug scrypt (알고리즘 접두 포함) — 직접 구현 금지
- **NFR-09**: XSS 방어 — Jinja2 자동 이스케이프 + `nl2br` 필터, `|safe` 미사용
- **NFR-13**: SQL injection 방어 — SQLAlchemy ORM · named binding

### 1-5. 주요 제약사항

- **C-01**: Oracle 11g XE 는 IDENTITY · BOOLEAN · ENUM · LIMIT/OFFSET 미지원 → 대응 방식 스키마 설계 시 명시
- **C-02**: python-oracledb thin 모드 미지원 → thick 모드
- **C-03**: Instant Client shared library 순환 의존 → LD_LIBRARY_PATH execvpe wrapper 로 우회
- **C-07**: CoatingVision 라이선스 CC BY-NC-ND 4.0 → 원본 이미지 재배포 금지

### 1-6. 범위 밖 명시

실시간 스트리밍 카메라 연결 · 다중 라인 동시 모니터링 · 게시판 첨부파일 · 알림 발송 (이메일/SMS/Slack) · WebSocket · i18n · 다크 모드 · OAuth · 검사 이미지 편집 · 관리자 CLI · 알람 이력 별도 페이지 · 이미지 확대 뷰 · REST API 완전 노출 · 대량 삭제 · 통계 대시보드 상세 — 이 15개는 범위 밖으로 문서화하여 스코프 크리프 방지.

---

## 2. ERD

**산출물**: `docs/architecture.md` §3 (mermaid), `docs/schema.md`

요구사항에서 도출된 엔티티를 정리하고 관계를 설계했다.

### 2-1. 관계도

```
USERS(1) ─────< (N)INSPECTIONS                  (FK_INSPECTIONS_USER)
USERS(1) ─────< (N)INSPECTIONS.REVIEWED_BY      (FK_INSPECTIONS_REVIEWED_BY)
USERS(1) ─────< (N)POSTS                        (FK_POSTS_USER)
USERS(1) ─────< (N)COMMENTS                     (FK_COMMENTS_USER)

INSPECTIONS(1) ─────< (N)DETECTIONS   ON DELETE CASCADE
INSPECTIONS(1) ─────< (N)SPC_POINTS   ON DELETE CASCADE
INSPECTIONS(1) ─────< (N)FINDINGS     ON DELETE CASCADE
INSPECTIONS(1) ─────< (N)POSTS.INSPECTION_ID    (nullable)

POSTS(1) ─────< (N)COMMENTS           ON DELETE CASCADE
```

### 2-2. 테이블 (7)

| 테이블 | 주요 컬럼 | 역할 |
|---|---|---|
| USERS | ID, USERNAME(UK), PASSWORD_HASH, EMAIL(UK), ROLE, IS_ACTIVE | 사용자 계정 |
| INSPECTIONS | ID, USER_ID(FK), IMAGE_PATH, A3_RATIO, SEG_CRACK, PINHOLE_COUNT, STATUS, IS_COLD_START, REVIEWED_BY, REVIEWED_AT | 검사 결과 헤더 |
| DETECTIONS | ID, INSPECTION_ID(FK), X1/Y1/X2/Y2, CONF, ASPECT, ROUNDNESS | YOLO 검출 박스 |
| SPC_POINTS | ID, INSPECTION_ID(FK), METRIC, SEQ_NO, VALUE, EWMA, CENTER, UCL, IS_ALARM | SPC 시계열 |
| FINDINGS | ID, INSPECTION_ID(FK), LAYER, CONTENT, CONFIDENCE, SOURCE | Advisor 3층 리포트 |
| POSTS | ID, USER_ID(FK), INSPECTION_ID(FK, nullable), CATEGORY, TITLE, CONTENT, VIEW_COUNT | 게시판 |
| COMMENTS | ID, POST_ID(FK), USER_ID(FK), CONTENT | 댓글 |

### 2-3. 스키마 설계 결정

Oracle 11g 제약 (§1-5 C-01) 을 반영해:

- **IDENTITY 없음** → SEQUENCE 7 개 + BEFORE INSERT TRIGGER 7 개로 PK 자동 채움
- **BOOLEAN 없음** → NUMBER(1) + CHECK IN (0,1) — 예: `IS_ACTIVE`, `IS_ALARM`, `IS_COLD_START`
- **ENUM 없음** → VARCHAR2 + CHECK IN (...) — 예: `ROLE`, `STATUS`, `METRIC`, `LAYER`, `CATEGORY`
- **CASCADE 설계**: INSPECTIONS 삭제 시 DETECTIONS · SPC_POINTS · FINDINGS 자동 삭제. POSTS 는 INSPECTION_ID 삭제되어도 유지 (nullable FK) — 검사 삭제 후에도 관련 리포트 게시글이 남도록

### 2-4. 인덱스 (5)

조회 자주 쓰는 컬럼에만 명시. UNIQUE 컬럼은 Oracle 이 자동 인덱스 생성.

| 이름 | 컬럼 | 용도 |
|---|---|---|
| IDX_INSPECTIONS_USER | INSPECTIONS(USER_ID) | 내 이력 조회 |
| IDX_INSPECTIONS_CREATED | INSPECTIONS(CREATED_AT) | 최신순 정렬 |
| IDX_SPC_METRIC_SEQ | SPC_POINTS(METRIC, SEQ_NO) | metric 별 시계열 스캔 |
| IDX_POSTS_CAT_CREATED | POSTS(CATEGORY, CREATED_AT) | 카테고리 탭 최신순 |
| IDX_COMMENTS_POST | COMMENTS(POST_ID) | 상세 페이지 댓글 로드 |

---

## 3. UML

**산출물**: `docs/uml.md` (7 mermaid 블록)

요구사항의 액터·유스케이스, 도메인 모델, 주요 시나리오를 다이어그램으로 확정했다.

### 3-1. Use Case Diagram

**액터 3계층 (역할 위계)**:
- **inspector** (기본 역할): 로그인 · 검사 실행 · 자기 이력 조회 · 게시판 (notice 제외 CRUD) · 댓글
- **manager** (inspector + 추가): 전체 이력 조회 · SPC 관리도 · 알람 조치 완료 처리 · notice 작성
- **admin** (manager + 추가): 타인 글·검사 삭제 · 계정 관리 · 저장소 정리

**주요 유스케이스**:
- UC-01 회원가입 · UC-02 로그인 · UC-03 비밀번호 변경
- UC-10 검사 실행 (이미지 업로드/샘플)
- UC-11 검사 이력 조회 (자기/전체)
- UC-12 검사 이력 CSV 내보내기
- UC-20 SPC 관리도 조회
- UC-21 알람 조치 완료 (report 게시글 필수)
- UC-30 게시판 CRUD (notice 는 manager 이상만)
- UC-40 사용자 관리 (역할 변경, 비밀번호 초기화)
- UC-41 저장소 정리 (고아·보존기간)

### 3-2. Class Diagram

두 개의 관점으로 분리했다.

**3-2-1. 도메인 모델 (ORM 7 클래스)** — 영속 계층
- User · Inspection · Detection · SpcPoint · Finding · Post · Comment
- `relationship` · `foreign_keys=` · cascade 관계 명시
- Inspection 은 User 를 두 번 참조 (owner USER_ID + REVIEWED_BY) → `foreign_keys` 로 모호성 해소 명시

**3-2-2. 애플리케이션 구조 (계층·모듈)** — 논리 계층
```
routes/ (10 blueprint)
   ↓
services/ (auth, inspection_store, spc, board, pipeline, admin, cleanup)
   ↓
ml/ (loader, anomaly, detect, segment, shape, advisor)
   ↓
models.py (ORM)
   ↓
db.py (Engine + Session)
   ↓
config.py (경로 · DSN · WSL IP 자동 감지)
```

**계층 규칙**: 위 계층이 아래를 import. 아래에서 위 참조 금지. 역참조 없음을 grep 으로 검증.

### 3-3. Sequence Diagrams (4 개)

**3-3-1. 로그인 (POST /login)**:
- `authenticate(username, password)` → `check_password_hash` → `LAST_LOGIN_AT` 갱신 → `session.clear()` → 세션에 `user_id`, `role` 저장 → 302 redirect

**3-3-2. 검사 실행 (POST /inspect)**:
- 파일 저장 → A3 → YOLO → 세그 → 형태 → Advisor
- **트랜잭션 A**: INSPECTIONS 1 + DETECTIONS N + FINDINGS N 원자적 저장
- **트랜잭션 B**: SPC add_spc_point × 2 (metric='a3', 'seg_crack')
- 알람 시 **트랜잭션 C**: INSPECTIONS.STATUS='alarm'
- **DB 저장 실패 alt**: flash 로 알리고 결과 화면 렌더 유지 (NFR-16 회복탄력성)

**3-3-3. 알람 조치 완료 (POST /history/<id>/review)**:
- `mark_reviewed` → `has_report_for_inspection` 확인 → report 없으면 flash-error 로 거부 → 있으면 REVIEWED_BY/REVIEWED_AT 기록, STATUS='reviewed'

**3-3-4. 권한 검사 (HTML vs API 분기)**:
- HTML 라우트: `@login_required` → 세션 없으면 302 → `/login?next=...`, `@role_required('manager')` → 부족하면 403 HTML
- API 라우트: `@api_login_required` → 401 JSON, 내부 권한 부족 시 403 JSON

---

## 4. 개발

요구사항·ERD·UML 을 코드로 구현했다. 데이터 실험 계층과 Flask 애플리케이션 계층을 병행 개발했다.

### 4-1. 데이터 실험 계층 (실험 스크립트 01 ~ 56)

**목적**: FR-13 ~ FR-17 파이프라인 구성 요소 (A3 · YOLO · U-Net · Advisor) 의 알고리즘·모델·파라미터를 실측으로 결정.

| 분류 | 스크립트 | 산출 |
|---|---|---|
| 데이터 이해 | 01_parse_metadata, 02_patch_extraction_check, 03_pinhole_shape_analysis | 공정 조건 파싱 · 패치 수확량 · 갭별 형태 |
| 데이터 준비 | 04_extract_patches | 27,711 patch (64px, margin 4px, 갭별 균등) |
| 라벨 검증 | 05_verify_delamination | 박리 라벨 실체 확인 |
| 이상 탐지 | 06_benchmark_anomaly, 08_robustness_test | A3/B2/B3 비교 · 강건성 13종 |
| SPC | 09_spc_monitor, 10_spc_tuning, 15_spc_visualize | 관리도 + 파라미터 스윕 |
| 검출 | 11~14, 32~35 | pinhole_v1 → pinhole_frames_seed{0..3} |
| Advisor | 16_advisor | 3층 규칙 기반 리포트 |
| 웹앱 지원 | 17_precompute_detections, 18_prepare_samples | 캐시 · 샘플 |
| 후속 분석 | 19~28, 44~56 | 시간축 평활화 · 애매 patch · 캐스케이드 · 세그·판정 단위·MSA·검출 실패 |

### 4-2. Flask 애플리케이션 계층 — 리팩터링 (§2단계, DB 도입 전)

**목적**: NFR-01 (앱 시작 500 ms 이하) 달성을 위한 계층 분리 + 이후 DB·인증·이력·SPC·게시판·관리 기능을 붙일 구조 확보.

**설계 선행 순서**: §2단계 (애플리케이션 재구조화) → §3-1 (스키마 · 연결 계층) → §3-2~§3-6 (기능 구현). 단일 `app.py` (1,008 줄) 에 DB 계층·인증·이력을 추가하면 이후 분리 비용이 급격히 증가한다는 판단으로, DB 연동 전에 blueprint / services / ml 계층으로 먼저 분리했다 (`app_legacy.py` 는 참고 백업으로 유지). 이 판단의 명시적 근거는 대화 로그에 있었을 것이나 저장소에는 남아 있지 않다. 단, 실제 순서는 커밋 이력·`memory/session_state.md` 로 확인 가능 (§2단계 완료 → §3-1 DB → §3-2~ 기능).

| 단계 | 조치 | 시작 시간 | 대 legacy |
|---|---|---|---|
| legacy | 단일 `app.py` (1,008 줄) | 3,735 ms | 기준 |
| (a) 정적 산출물 lazy | JSON/CSV 최초 사용 시점 로드 | 3,604 ms | −3.5% |
| (b) 모델 lazy | YOLO/U-Net 도 최초 사용 시점 로드 | 223 ms | −94.0% |
| (c) blueprint 도입 | routes/, api/ 로 분리 | 350 ms | −90.6% |
| (d) templates·static 이동 | app/templates, app/static | **257 ms** | **−93.1%** |

`app_legacy.py` 는 참고 백업으로 유지.

**측정 시점**: 257 ms 는 §2단계 재구조화 직후 (2026-09-14 전후) 값. **"3,735 → 257 ms · 93.1% 단축"** 수치는 지연 로드 전환의 효과를 나타내며 이 수치는 유효.

**현재값** (§3-7~§3-10 모듈 추가 후): **389.6 ms** (2026-09-16 3회 median 재실측, 348.5 / 392.5 / 389.6). 상세는 `docs/metrics.md` §3-1.

### 4-3. 구현–요구사항 추적 매트릭스 (§3-1 ~ §3-11)

기능 단위 명세를 순차적으로 구현한 흐름.

| 단계 | 산출 |
|---|---|
| §3-1 DB 도입 | Oracle 11g XE, SQLAlchemy engine, LD_LIBRARY_PATH execvpe wrapper (C-03 대응), 스키마 초기화 (`sql/init_db.py`) |
| §3-2 인증 (FR-01 ~ FR-12) | USERS 테이블, werkzeug scrypt, 세션 (`user_id`, `role`), `@login_required`, `@role_required`, `@api_login_required`, open-redirect 방지 |
| §3-3 검사 이력 (FR-18 ~ FR-24, FR-26) | INSPECTIONS · DETECTIONS · FINDINGS 트랜잭션 저장, 상세 페이지, 삭제 (CASCADE + 파일 삭제) |
| §3-4 SPC (FR-28 ~ FR-34) | SPC_POINTS 테이블, baseline 30점, `add_spc_point`, 알람 판정, `/spc` (매니저 이상), Chart.js 시계열, JSON API |
| §3-5 게시판 + 알람 조치 완료 (FR-35 ~ FR-41) | POSTS · COMMENTS, 4 카테고리, CRUD, 조회수, XSS 방어, 알람 조치 완료 워크플로 (report 게시글 필수) |
| §3-6 문서 갱신 · admin 비번 재해싱 (FR-53) | docs/routes.md · schema.md · architecture.md · README 갱신 |
| §3-7 계정 관리 (FR-42 ~ FR-46) | `/admin/users`, 역할 변경, 활성 토글, 비밀번호 초기화, 안전장치 3 (자기 강등/비활성화/마지막 활성 admin 보호), `/account/password` |
| §3-8 파일 생명주기 + 에러 페이지 + 로깅 (FR-47 ~ FR-52, NFR-15) | `/admin/storage` 파일 정리, path traversal 방어, RotatingFileHandler, 403/404/500 커스텀 페이지 |
| §3-9 CSV 내보내기 + 페이지네이션 (FR-27) | `iter_inspections_for_export` (yield_per, UTF-8 BOM, 10,000행 제한), `_pagination.html` 매크로 (per_page 화이트리스트 20/50/100) |
| §3-10 pytest 도입 | 87 테스트 케이스 (86 pass + 1 skip), 커버리지 69%, `pytest_*` 접두사 격리, 실제 Oracle 사용 |
| §3-11 문서화 | `docs/requirements.md` (FR 55 · NFR 29 · 제약 10 · 범위 밖 15) · `docs/uml.md` (7 mermaid 블록) · `docs/index.md` 신규 |

> **각주**: FR 번호는 §3-11 단계에서 개별 명세를 통합해 요구사항 분석서 (`docs/requirements.md`) 를 작성하는 과정에서 부여했다. 본 표는 구현 결과와 요구사항 항목의 대응을 확인하기 위한 추적 매트릭스이며, 개별 명세가 FR 번호로 작성된 것은 아니다.

### 4-4. 계층 · 규모 최종

| 항목 | 값 |
|---|---|
| 블루프린트 | 10 |
| 라우트 | 36 (static · 에러 핸들러 제외, rule 기준. 2026-09-16 재실측) |
| DB 테이블 | 7 |
| ORM 모델 | 7 |
| 시퀀스 · 트리거 | 각 7 |
| 인덱스 | 5 |
| 서비스 계층 모듈 | pipeline, auth, inspection_store, spc, board, admin, cleanup |
| ML 계층 모듈 | loader, anomaly, detect, segment, shape, advisor |
| 실험 스크립트 | 56 |

### 4-5. 개발 중 발견하고 정정한 오류

개발·검증 과정에서 스스로 찾은 오류.

| 오류 | 계기 | 정정 |
|---|---|---|
| stem 컬럼 부재 조인 오염 | 마스크 vs A3 상관 산출 시 dict 크기 축소 발견 | 상류에 stem 컬럼 추가 |
| 형제 patch 자기상관 lift 5.43× 증폭 | lift 재현 중 unique 프레임 카운트 검증 | 시퀀스 산술평균 · 자기상관 · INCAPABLE 제외 문구 병기, 단독 수치 인용 금지 |
| 무작위 patch 분할 데이터 누수 | 신규 test 38 개 중 19 개가 기존 train 에 있음 발견 | 프레임 단위 stratify 로 재분할 재학습 |
| YOLO 55 ms 산출 스크립트 부재 | 값 추적 실패 | `28_speed_benchmark.py` 재측정 |
| "0.11 ms/patch" 단위 오표기 | 코드 검토에서 원소 크기 확인 | ms/cell 로 정정, 확산된 문서 전체 갱신 |
| EWMA 초기화 버그 | 초기 알람 1,000 건 (alarm fatigue) | 기준 구간 중심선으로 초기화 |
| IoU 매칭 실패 (재현율 0.239) | 자동 라벨 검증 실패 | 원인 진단 후 패딩 2배 + 중심 거리 검증 (재현율 0.830) |
| IoU 매칭 임계 이중 카운트 | FN·FP 짝 발견 | 문턱 병기 규칙 |
| 페이지 크기 기본값 30 (허용 목록 밖) | pytest 실패 | DEFAULT_PAGE_SIZE=20 정정 |
| b_majority 병합 인덱스 입력 | 6조합 전부 F1 0.000 극단값 검증 | raw alert 인덱스로 정정 |

---

## 5. Testing

**산출물**: `docs/testing.md`, `tests/` 아래 6 파일

테스트 코드는 §3-10 단계에서 작성됐다. 이 시점에는 요구사항 분석서 (§3-11) 가 아직 없었고 개별 명세 위에서 케이스를 구성했다. §3-11 요구사항 분석서 작성 시 기존 테스트 케이스를 FR 항목에 대응시켜 커버리지를 확인했다 (docs/requirements.md 각 FR 의 "검증" 컬럼 참조).

### 5-1. 테스트 프레임워크

- pytest + pytest-cov
- 실제 Oracle 11g 인스턴스 사용 (SQLite 대체 아님)
- 근거: Oracle 11g 특유 규약 (SEQUENCE+TRIGGER, NUMBER(1)+CHECK) 을 실제 엔진에서 검증해야 실효성 확보

### 5-2. 테스트 파일 구조

```
tests/
├── conftest.py               # app · client · 사용자 fixture · fake_inspection_result
├── test_auth.py              # 회원가입 · 로그인 · 비밀번호 변경 · 프로필
├── test_permissions.py       # 역할별 접근 매트릭스 (parametrize 40)
├── test_board.py             # 게시판 CRUD · CASCADE · XSS · 페이지네이션
├── test_inspection.py        # 검사 저장 · 상세 · 삭제 · CSV export
├── test_spc.py               # SPC 계산식 · baseline · 알람
└── test_admin.py             # 계정 관리 · 안전장치 · 저장소 정리
```

### 5-3. DB 격리 전략

- **테스트 데이터 UUID 접미사로 생성**: `pytest_inspector_a1b2c3` 형태. 기존 계정과 충돌 없음
- **finalizer 로 자동 정리**: `_delete_test_user(user_id)` 가 소유 검사 · 게시글 · 댓글 · reviewed_by 참조까지 정리
- **기존 데이터 read-only**: 삭제·수정 대상은 `pytest_*` 접두사로 한정

### 5-4. 격리 검증

3회 연속 실행 후 DB 상태:

| 테이블 | 잔여 |
|---|---|
| `pytest_*` 접두사 사용자 | 0 |
| INSPECTIONS | 0 |
| POSTS | 0 |
| COMMENTS | 0 |
| SPC_POINTS | 0 |

기존 사용자 (admin/bob/manager1) 는 그대로 유지, role/is_active 변경 없음.

### 5-5. 결과

| 지표 | 값 |
|---|---|
| 케이스 수 | 87 (86 pass + 1 skip) |
| 실행 시간 (3회 평균) | ~38 s |
| 전체 커버리지 | 69% (statements 2,430 중 miss 760) |
| routes 평균 | 65~90% |
| services 평균 | 66~86% |
| ml/ (모델 로드 회피) | 4~49% |
| auth_utils, config, logging_config, store, models | 90~100% |

### 5-6. 모델 로드 회피 원칙

- `save_inspection` 은 결과 dict 만 받으므로 모델 로드 없이 `fake_inspection_result` fixture 를 인자로 전달
- `/inspect` POST 를 통과하는 테스트는 없음 (파이프라인 시간 오래 걸림)
- test_client 는 `pytest_*` 사용자로 로그인 후 `/history/*`, `/board/*` 라우트를 실측

### 5-7. skipped 1 건 사유

`test_status_becomes_alarm_when_spc_triggers` — baseline 미완성 상태에서는 SPC 알람 로직이 발동하지 않으므로 `pytest.skip()`. baseline 30점 채우기는 테스트 격리 원칙에 어긋나므로 스킵 유지. 알람 시나리오 자체는 §3-4 수동 검증에서 완료.

### 5-8. 테스트 원칙

- **기존 데이터 오염 금지**: 모든 삭제는 `pytest_*` 접두사 사용자 소유로 한정
- **모델 로드 없음**: `fake_inspection_result` fixture 로 대체
- **통과시키려고 기능 코드 변경 금지**: DEFAULT_PAGE_SIZE 버그처럼 실제 결함은 정정, 그 외 코드 변경 없음

---

## 6. 운영 (유지보수)

배포 후 시스템이 안정적으로 동작하도록 운영·유지보수 항목을 설계·구현했다.

### 6-1. 로깅

- **RotatingFileHandler**: `logs/app.log`, 10 MB × 5 백업 (NFR-26 디스크 무한 증가 방지)
- **LOG_LEVEL**: `.env` (기본 INFO)
- **로그 항목**: 로그인 성공/실패 (사유), 검사 실행, DB 오류, 파일 정리 결과, 500 스택
- **보안 (NFR-52)**: 비밀번호·해시·세션 토큰 로그 금지. `authenticate` 는 사유 (`reason=bad_password`) 만 기록. grep 매치 0 검증

### 6-2. 에러 페이지 (NFR-15)

- HTML: 커스텀 403/404/500 템플릿
- API: JSON 응답 (`{"error":"internal server error"}`)
- 500 응답에 스택 트레이스 노출 금지 (로그에만)

### 6-3. 회복탄력성 (NFR-16 ~ NFR-20)

- **DB 장애 시 검사 기능 유지**: `save_inspection` 실패 시 flash 로 알리고 결과 화면 렌더 유지
- **SPC 처리 실패 격리**: `_process_spc_and_status` 는 별도 트랜잭션. 실패해도 검사 저장 유지, 로그 error 만
- **모델 로드 실패 격리**: `ml/loader.py` 의 실패는 에러 슬롯에 보관, 재시도 안 함
- **DB 연결 재확인**: 커넥션 풀 `pool_size=5, max_overflow=5, pool_pre_ping=True`
- **WSL 호스트 IP 재부팅 대응**: 매 호출 시 `ip route show` 로 재감지 (lazy)

### 6-4. 알람 조치 완료 워크플로 (FR-35)

1. `/inspect` 검사 결과가 UCL 초과 시 SPC 서비스가 IS_ALARM=1 로 저장하고 INSPECTIONS.STATUS='alarm' 전이
2. `/spc` 알람 목록에 표시 (매니저·관리자만)
3. `/history/<id>` 상세에서 "이 검사로 리포트 작성" 버튼 → `/board/new?inspection_id=<id>&category=report`
4. 매니저·관리자가 "조치 완료 처리" 클릭 → `mark_reviewed` 서비스가 report 존재 확인 → STATUS='reviewed' + REVIEWED_BY / REVIEWED_AT 기록
5. report 없이 시도하면 flash-error 로 거부

### 6-5. 관리자 저장소 정리 (FR-47 ~ FR-49)

- **웹 UI** (`/admin/storage`): uploads 파일 수·용량·고아 수 표시. 고아 정리·보존 기간 정리 버튼
- **파일 정리 안전 (FR-48)**: 삭제 대상은 `UPLOADS_DIR.resolve()` 안. `../` 등 traversal 시도는 거부 + 로그 warning. `.gitkeep` 보호
- **CLI 정리 (FR-49)**: `python sql/cleanup.py --orphan --old N --status` — 크론용

### 6-6. 콜드 스타트 감지 (FR-16)

- 이번 요청에서 모델 로드 시 `IS_COLD_START=1`
- `get_dashboard_summary` 의 평균 crack 통계에서 제외
- 실측: 첫 검사 `MS_YOLO=2317 ms` → 두번째 `MS_YOLO=116 ms` (20×)
- 콜드 스타트 분리로 통계 지표 왜곡 방지

### 6-7. 초기화 · 배포 절차

```bash
# 1) .env 준비 (커밋 금지, .gitignore 등록됨)
cp .env.example .env
# .env 에 ORACLE_PASSWORD, ADMIN_PASSWORD, SECRET_KEY 를 실 값으로 채운다

# 2) DB 초기화 (테이블 7 + 시퀀스 7 + 트리거 7 + 인덱스 5 + admin 계정 seed)
python sql/init_db.py

# 3) YOLO 사전 추론 + 예시 샘플 (최초 1회)
python 17_precompute_detections.py    # 2,227장 YOLO 캐싱 (~4분 CPU)
python 18_prepare_samples.py          # /inspect 예시 버튼용 3장

# 4) 웹앱 시작
python run.py                         # http://localhost:5000
```

- `run.py` 최상단이 `LD_LIBRARY_PATH` 를 세팅 후 자기 자신을 재실행 (C-03 Instant Client 순환 의존 우회). ~130 ms 오버헤드
- systemd 등에서 `Environment=LD_LIBRARY_PATH=...` 를 지정하면 재실행 회피 가능

### 6-8. 시연 계정 초기화 (FR-53)

- `sql/init_db.py` 재실행 시 admin 계정 PASSWORD_HASH 를 `.env` ADMIN_PASSWORD 로 재해싱, ROLE='admin', IS_ACTIVE=1 강제
- 배포 시 반드시 ADMIN_PASSWORD 변경

### 6-9. 개발 서버 한계 (제약 C-09)

- `werkzeug` 개발 서버 사용, 프로덕션 WSGI (gunicorn 등) 미채택
- 시연 목적으로 개발 서버 유지. 프로덕션 배포 시 별도 구성 필요

### 6-10. 지속적 개선

배포 후 품질 관점의 재검증·정정을 지속했다.

- 판정 단위 재집계 (§22): patch 시계열 → 원본 프레임 단위로 재정의
- 정답·예측 집계 대칭성 확보 (§22-9): 6조합 산출 후 대칭 조합 채택
- b_majority 코드 오류 정정 (§22-13): 병합 인덱스 → raw 인덱스
- 측정 시스템 재현성 재프레이밍 (§22-MSA): 강건성 실험을 Gauge R&R 관점으로 재정리
- 검출 실패 케이스 분석 (§22-DET): IoU 문턱 민감도 + 과검 3그룹 분류

**미착수 항목** (`docs/PROJECT_SUMMARY.md` §8-11): USL 스윕, p·u 관리도, 라인 속도 환산, 대칭 조합 L 스윕, 딥러닝 강건성 회복, 박리 conf 재평가.

---

## 참고 문서

- 요구사항 상세: `docs/requirements.md`
- 아키텍처 상세: `docs/architecture.md`
- DB 스키마 상세: `docs/schema.md`
- UML 상세: `docs/uml.md`
- 라우트 인벤토리: `docs/routes.md`
- 테스트 상세: `docs/testing.md`
- 자소서 참고 자료: `docs/PROJECT_SUMMARY.md`
- 실험 시간순 기록: `experiment_log.md`
