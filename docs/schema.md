# DB 스키마 (§3-6)

작성 시점: 2026-09-14. Oracle 11g XE 인스턴스에서 `USER_TAB_COLUMNS`,
`USER_CONSTRAINTS`, `USER_SEQUENCES`, `USER_TRIGGERS`, `USER_INDEXES` 실측.

## 테이블 목록 (7)

`USERS`, `INSPECTIONS`, `DETECTIONS`, `SPC_POINTS`, `FINDINGS`, `POSTS`, `COMMENTS`.

## 관계도

```
USERS(1) ─────< (N)INSPECTIONS                  (FK_INSPECTIONS_USER)
USERS(1) ─────< (N)INSPECTIONS.REVIEWED_BY      (FK_INSPECTIONS_REVIEWED_BY)
USERS(1) ─────< (N)POSTS                        (FK_POSTS_USER)
USERS(1) ─────< (N)COMMENTS                     (FK_COMMENTS_USER)

INSPECTIONS(1) ─────< (N)DETECTIONS   ON DELETE CASCADE
INSPECTIONS(1) ─────< (N)SPC_POINTS   ON DELETE CASCADE
INSPECTIONS(1) ─────< (N)FINDINGS     ON DELETE CASCADE
INSPECTIONS(1) ─────< (N)POSTS.INSPECTION_ID    (nullable, 일반 글은 NULL)

POSTS(1) ─────< (N)COMMENTS           ON DELETE CASCADE
```

## USERS

| 컬럼 | 타입 | Null | 기본값 | 설명 |
|---|---|---|---|---|
| ID | NUMBER | N | (SEQ_USERS + TRG_USERS_BI) | PK. |
| USERNAME | VARCHAR2(50) | N | | UNIQUE. 로그인 식별자. |
| PASSWORD_HASH | VARCHAR2(255) | N | | werkzeug scrypt 해시 (알고리즘 접두 포함). |
| EMAIL | VARCHAR2(120) | Y | | UNIQUE. 선택 입력. |
| FULL_NAME | VARCHAR2(100) | Y | | 표시용 성명. |
| ROLE | VARCHAR2(20) | Y | 'inspector' | CHECK IN ('inspector','manager','admin'). 위계 admin > manager > inspector. |
| IS_ACTIVE | NUMBER(1) | Y | 1 | CHECK IN (0,1). 0 이면 로그인 거부. |
| CREATED_AT | TIMESTAMP | Y | SYSTIMESTAMP | 가입 시각. |
| LAST_LOGIN_AT | TIMESTAMP | Y | | 마지막 로그인 시각 (authenticate 성공 시 갱신). |

## INSPECTIONS

| 컬럼 | 타입 | Null | 기본값 | 설명 |
|---|---|---|---|---|
| ID | NUMBER | N | (SEQ_INSPECTIONS + TRG) | PK. |
| USER_ID | NUMBER | Y | | FK → USERS. 소유자. |
| FILE_NAME | VARCHAR2(255) | Y | | 업로드 파일명 (원본). |
| IMAGE_PATH | VARCHAR2(512) | Y | | `app/static/uploads/` 아래 basename. |
| HEATMAP_PATH | VARCHAR2(512) | Y | | A3 히트맵 이미지 basename. |
| ANNOTATED_PATH | VARCHAR2(512) | Y | | YOLO 오버레이 basename. |
| SEG_PATH | VARCHAR2(512) | Y | | 세그 오버레이 basename. |
| SOURCE | VARCHAR2(20) | Y | | CHECK IN ('upload','sample'). |
| CREATED_AT | TIMESTAMP | Y | SYSTIMESTAMP | 검사 시각. |
| A3_RATIO | NUMBER(10,6) | Y | | A3 이상 patch 비율 (0.0~1.0). |
| A3_CELLS | NUMBER(5) | Y | | 이상 cell 개수 (70 중). |
| SEG_CRACK | NUMBER(10,6) | Y | | 세그 crack 픽셀 면적비. |
| SEG_DELAM | NUMBER(10,6) | Y | | 세그 delam 픽셀 면적비 (§20-5 참고값). |
| PINHOLE_COUNT | NUMBER(5) | Y | | YOLO 검출 개수. |
| MS_A3 | NUMBER(10,3) | Y | | A3 처리 시간 (ms/patch). |
| MS_YOLO | NUMBER(10,3) | Y | | YOLO 처리 시간 (콜드 스타트 시 로드 시간 포함). |
| MS_SEG | NUMBER(10,3) | Y | | 세그 처리 시간 (콜드 스타트 시 로드 시간 포함). |
| MS_TOTAL | NUMBER(10,3) | Y | | 스테이지 합계. |
| IS_COLD_START | NUMBER(1) | Y | 0 | CHECK IN (0,1). 이번 요청에서 모델을 로드했으면 1. `get_dashboard_summary` 는 IS_COLD_START=1 을 평균 crack 통계에서 제외. |
| STATUS | VARCHAR2(20) | Y | 'normal' | CHECK IN ('normal','alarm','reviewed'). SPC 알람 시 'alarm', 조치 완료 시 'reviewed'. |
| REVIEWED_BY | NUMBER | Y | | FK → USERS. 조치 완료 처리한 사용자. |
| REVIEWED_AT | TIMESTAMP | Y | | 조치 완료 시각. |

## DETECTIONS

| 컬럼 | 타입 | Null | 설명 |
|---|---|---|---|
| ID | NUMBER | N | PK. |
| INSPECTION_ID | NUMBER | N | FK → INSPECTIONS **ON DELETE CASCADE**. |
| X1, Y1, X2, Y2 | NUMBER(7,2) | Y | 박스 좌표. |
| CONF | NUMBER(5,4) | Y | YOLO 신뢰도. |
| ASPECT | NUMBER(7,3) | Y | 종횡비 (max(w,h)/min(w,h)). |
| ROUNDNESS | NUMBER(5,4) | Y | `4π·area/perimeter²` (shape stage). |

## SPC_POINTS

| 컬럼 | 타입 | Null | 기본값 | 설명 |
|---|---|---|---|---|
| ID | NUMBER | N | (SEQ_SPC_POINTS + TRG) | PK. |
| INSPECTION_ID | NUMBER | N | | FK → INSPECTIONS **ON DELETE CASCADE**. |
| METRIC | VARCHAR2(20) | Y | | CHECK IN ('a3','seg_crack'). |
| SEQ_NO | NUMBER(7) | Y | | metric 내 순번. 인덱스 (METRIC, SEQ_NO). |
| VALUE | NUMBER(10,6) | Y | | 원값. |
| EWMA | NUMBER(10,6) | Y | | metric 별 λ 로 계산한 EWMA (a3=0.1, seg_crack=0.2. FR-31). baseline 30점까지는 NULL. |
| CENTER | NUMBER(10,6) | Y | | baseline 평균. baseline 30점까지는 NULL. |
| UCL | NUMBER(10,6) | Y | | CENTER + 3.5σ. baseline 30점까지는 NULL. |
| IS_ALARM | NUMBER(1) | Y | 0 | CHECK IN (0,1). value 또는 EWMA 가 UCL 초과 시 1. |

### 새 SPC metric 추가 시 필수 절차

METRIC 컬럼은 **앱 계층 (`app/services/spc.py:EWMA_LAMBDAS`)** 과 **DB CHECK 제약** 두 곳에서 이중 관리된다. 새 metric 추가 시 **두 곳을 모두 수정**해야 하며 한쪽만 고치면 앱 검증은 통과하나 DB INSERT 가 `ORA-02290` (check constraint violated) 로 실패한다.

**수정 대상**:

1. **`app/services/spc.py:EWMA_LAMBDAS`** — 새 metric 과 λ 값을 dict 에 추가. `VALID_METRICS` 는 `EWMA_LAMBDAS.keys()` 에서 파생되므로 자동 반영
2. **`sql/schema.sql:125`** `SPC_POINTS.METRIC CHECK (METRIC IN ('a3','seg_crack'))` — 새 metric 값을 CHECK 목록에 추가
3. **기존 DB 마이그레이션 (`sql/migrate_3_X.py` 신규)** — 이미 DB 를 초기화한 환경에서는 스키마 파일 수정만으로는 반영되지 않음. `ALTER TABLE SPC_POINTS DROP CONSTRAINT <이름>` 후 `ALTER TABLE ... ADD CONSTRAINT ... CHECK (METRIC IN (...))` 실행

**배포 순서**: DB 마이그레이션을 먼저 실행 (스키마가 새 metric 을 허용하도록) → 그 다음 앱 배포 (새 EWMA_LAMBDAS 반영). 반대 순서로 하면 새 앱이 부팅해서 새 metric 값을 저장하려 할 때 DB CHECK 위반.

**FR-31 요구사항 참조**: `docs/requirements.md` FR-31 — 새 metric 추가 시 λ 값을 함께 지정해야 하며, 지정되지 않은 metric 은 SPC 처리에서 명확히 실패.

**이중 관리 강제 검토 결과**: 앱 기동 시 `USER_CONSTRAINTS` 조회로 CHECK 정의를 파싱해 `EWMA_LAMBDAS.keys()` 와 대조하는 방식이 가능하나 **채택하지 않음** — (1) Oracle CHECK 표현식 문자열 파싱 필요 (정규식 취약) (2) 매 부팅마다 DB 접속 → NFR-01 (500 ms) 위반 가능성 (3) 이 프로젝트의 다른 이중 관리 지점 (`ROLE`, `STATUS`, `CATEGORY`, `LAYER`) 도 동일 구조라 SPC 만 강제하는 것이 비대칭 (4) 배포 순서로 관리하는 것이 실무 관례. **문서화로 대체**.

## FINDINGS

| 컬럼 | 타입 | Null | 설명 |
|---|---|---|---|
| ID | NUMBER | N | PK. |
| INSPECTION_ID | NUMBER | N | FK → INSPECTIONS **ON DELETE CASCADE**. |
| LAYER | VARCHAR2(20) | Y | CHECK IN ('observation','interpretation','reference'). |
| CONTENT | VARCHAR2(4000) | Y | 텍스트. 4000자 초과 시 자르고 stdout 로그. |
| CONFIDENCE | VARCHAR2(10) | Y | interpretation 시 confidence (high/medium/low/reference). |
| SOURCE | VARCHAR2(100) | Y | reference 시 topic. |

**Advisor → FINDINGS 매핑 규칙**:
- `observation` dict → key 별 별도 행 (content = "{key}: {value}")
- `interpretations` list → 각 항목이 별도 행 (content = message)
- `references` list → 각 topic 이 별도 행 (source = topic, content = lines join)

## POSTS

| 컬럼 | 타입 | Null | 기본값 | 설명 |
|---|---|---|---|---|
| ID | NUMBER | N | (SEQ_POSTS + TRG) | PK. |
| USER_ID | NUMBER | Y | | FK → USERS. 작성자. |
| INSPECTION_ID | NUMBER | Y | | FK → INSPECTIONS. 검사 연결 (report 카테고리 등). |
| CATEGORY | VARCHAR2(20) | Y | 'free' | CHECK IN ('notice','report','qna','free'). notice = manager 이상만 작성. |
| TITLE | VARCHAR2(200) | N | | 필수. |
| CONTENT | VARCHAR2(4000) | Y | | 4000자 이하. 서비스에서 검증. |
| VIEW_COUNT | NUMBER(7) | Y | 0 | 본인 글 조회 시 미증가. |
| CREATED_AT | TIMESTAMP | Y | SYSTIMESTAMP | 작성 시각. |
| UPDATED_AT | TIMESTAMP | Y | | 수정 시각 (update_post 에서만 세팅). |

## COMMENTS

| 컬럼 | 타입 | Null | 기본값 | 설명 |
|---|---|---|---|---|
| ID | NUMBER | N | (SEQ_COMMENTS + TRG) | PK. |
| POST_ID | NUMBER | N | | FK → POSTS **ON DELETE CASCADE**. |
| USER_ID | NUMBER | Y | | FK → USERS. |
| CONTENT | VARCHAR2(1000) | Y | | 1000자 이하. |
| CREATED_AT | TIMESTAMP | Y | SYSTIMESTAMP | |

## 시퀀스 (7)

Oracle 11g 는 IDENTITY 컬럼이 없으므로 각 테이블마다 SEQUENCE + BEFORE INSERT TRIGGER 로 PK 자동 채움.

| 이름 | START | INCREMENT |
|---|---|---|
| SEQ_USERS, SEQ_INSPECTIONS, SEQ_DETECTIONS, SEQ_SPC_POINTS, SEQ_FINDINGS, SEQ_POSTS, SEQ_COMMENTS | 1 | 1 |

## 트리거 (7)

각 테이블에 대해 `BEFORE INSERT ... FOR EACH ROW`:

| 이름 | 대상 테이블 |
|---|---|
| TRG_USERS_BI, TRG_INSPECTIONS_BI, TRG_DETECTIONS_BI, TRG_SPC_POINTS_BI, TRG_FINDINGS_BI, TRG_POSTS_BI, TRG_COMMENTS_BI | 각 테이블 |

동작:
```plsql
IF :NEW.ID IS NULL THEN
  SELECT SEQ_XXX.NEXTVAL INTO :NEW.ID FROM DUAL;
END IF;
```

명시적으로 ID 를 넣지 않으면 시퀀스에서 채워짐. 이미 값이 있으면 유지 (마이그레이션 등에 유용).

## 사용자 명명 인덱스 (5)

FK 컬럼과 조회 자주 쓰는 컬럼에만 명시. UNIQUE 컬럼 (USERNAME, EMAIL) 은 Oracle 이 자동 인덱스 생성 (SYS_ 접두).

| 이름 | 컬럼 | 용도 |
|---|---|---|
| IDX_INSPECTIONS_USER | INSPECTIONS(USER_ID) | 내 이력 조회 |
| IDX_INSPECTIONS_CREATED | INSPECTIONS(CREATED_AT) | 최신순 정렬 |
| IDX_SPC_METRIC_SEQ | SPC_POINTS(METRIC, SEQ_NO) | metric 별 시계열 스캔 |
| IDX_POSTS_CAT_CREATED | POSTS(CATEGORY, CREATED_AT) | 카테고리 탭 최신순 |
| IDX_COMMENTS_POST | COMMENTS(POST_ID) | 상세 페이지 댓글 로드 |

## Oracle 11g 제약과 대응

| 제약 | 대응 |
|---|---|
| IDENTITY 컬럼 없음 | SEQUENCE + BEFORE INSERT TRIGGER |
| BOOLEAN 없음 | NUMBER(1) + CHECK IN (0,1) |
| LIMIT/OFFSET 없음 (12c 부터 지원) | SQLAlchemy 가 자동으로 ROWNUM 서브쿼리 생성 |
| ENUM 없음 | VARCHAR2 + CHECK IN (...) |
| Instant Client shared library 순환 의존 | `LD_LIBRARY_PATH` 를 `os.execvpe` 로 사전 세팅 (진입점 상단 `ensure_ld_library_path()`) |
| thick 모드 필요 (11g XE) | `oracledb.init_oracle_client(lib_dir=...)` 를 앱당 1회 |
