# 설계 — SPC baseline 판정 (FR-56)

작성 시점: 2026-09-16. 대상 요구사항: `docs/requirements.md` FR-56 및 §1.4a 검증 기준. 3단계 (구현) 진입 전 결정 사항 확정.

---

## 1. 판정 기준 선택

### 1-1. 후보

**후보 A — COUNT 기반 (metric 별 실제 저장 개수)**
- `add_spc_point`: `SELECT COUNT(id) WHERE metric = :m` 로 실제 저장 수를 세어, `count < 30` 이면 baseline
- `get_summary`: 이미 COUNT 기반 판정 사용 중 (`spc.py:206-217`)

**후보 B — 삭제 시 남은 점의 SEQ_NO 를 애플리케이션에서 재계산**
- `delete_inspection` 이후 남은 SPC_POINTS 의 SEQ_NO 를 `ROW_NUMBER() OVER (ORDER BY id)` 로 재부여
- baseline 판정은 여전히 `seq_no <= 30` 유지

**후보 C — Oracle DELETE 트리거로 SEQ_NO 자동 재채번**
- `TRIGGER AFTER DELETE ON SPC_POINTS` 로 삭제 후 남은 행의 SEQ_NO 를 UPDATE
- 애플리케이션 코드 변경 없음

**후보 D — soft delete (`IS_DELETED` 컬럼 추가)**
- 실제 DROP 없이 `IS_DELETED=1` 로 표시
- baseline 판정도 `WHERE IS_DELETED = 0` 필터

### 1-2. 선택: **후보 A**

**근거**:
1. **최소 침습**: 스키마 무변경. `add_spc_point` 안 판정 로직 1줄 수정
2. **FR-56 (c) 두 지점 판정 기준 동일** 요구를 가장 직접적으로 만족. `get_summary` 는 이미 COUNT 기반이라 자동 일치
3. gap 대응이 스키마·트리거 변경 없이 자동 성립

**탈락안 사유**:

| 후보 | 탈락 사유 |
|---|---|
| B | 삭제마다 대량 UPDATE 필요. CASCADE 삭제 시 여러 metric 이 동시 재계산되면 트랜잭션 비용·잠금 증가. 재계산 중 새 점 진입 시 동시성 이슈 |
| C | 트리거 로직 복잡화 · 감사·재현성 저하 · CASCADE 삭제 시 트리거 대량 발화. Oracle 11g 트리거 뮤테이팅 테이블 오류 위험 |
| D | 스키마 변경 (컬럼 · 인덱스 · 모든 쿼리 필터 추가). FR-24 CASCADE 취지 (실제 삭제) 와 상충. 저장 공간 누적 |

---

## 2. 변경 지점

### 2-1. `app/services/spc.py:71-78` (`add_spc_point`)

**변경 전**:
```python
# 다음 SEQ_NO
max_seq_scalar = sqlalchemy_session.query(func.max(SpcPoint.seq_no)).filter(
    SpcPoint.metric == metric,
).scalar()
max_seq = int(max_seq_scalar or 0)
seq_no = max_seq + 1

if seq_no <= BASELINE_SIZE:
    # baseline 수집 단계
```

**변경 후**:
```python
# 다음 SEQ_NO (연속 번호 부여 목적 — baseline 판정과 무관)
max_seq_scalar = sqlalchemy_session.query(func.max(SpcPoint.seq_no)).filter(
    SpcPoint.metric == metric,
).scalar()
max_seq = int(max_seq_scalar or 0)
seq_no = max_seq + 1

# baseline 판정은 실제 저장된 점 수 기준 (FR-56)
count_scalar = sqlalchemy_session.query(func.count(SpcPoint.id)).filter(
    SpcPoint.metric == metric,
).scalar()
existing_count = int(count_scalar or 0)

if existing_count < BASELINE_SIZE:
    # baseline 수집 단계
```

### 2-2. `get_summary` — 변경 없음

`spc.py:206-217, 229` 이 이미 COUNT 기반. 그대로 유지.

### 2-3. baseline 값 조회 (`spc.py:101-105`) — 변경 없음

```python
baseline_rows = sqlalchemy_session.query(SpcPoint.value).filter(
    SpcPoint.metric == metric,
    SpcPoint.seq_no <= BASELINE_SIZE,
).order_by(SpcPoint.seq_no).all()
```

이 쿼리는 **baseline 완성 후 (monitor phase) 에만 도달**. gap 이 있어 `existing_count < 30` 이면 line 78 분기에서 baseline phase 로 리턴하므로 이 조회는 실행되지 않음. 따라서 gap 상태에서 잘못된 baseline 값을 뽑는 문제는 발생하지 않는다.

### 2-4. 인덱스 활용

- `MAX(seq_no) WHERE metric = :m`: `IDX_SPC_METRIC_SEQ ON (METRIC, SEQ_NO)` 의 마지막 값 조회. Index Range Scan (Descending)
- `COUNT(id) WHERE metric = :m`: 동일 인덱스의 metric 파티션 카운트. Index Range Scan
- **두 쿼리 모두 기존 인덱스 활용**. 추가 인덱스 불필요. 성능 영향 미미 (metric 별 수천 점 수준에서 0.1 ms 이내로 추정)

### 2-5. 변경 요약

| 파일 | 변경 |
|---|---|
| `app/services/spc.py` | `add_spc_point` 판정 조건 `seq_no <= BASELINE_SIZE` → `existing_count < BASELINE_SIZE` |
| 스키마 | 없음 |
| 인덱스 | 없음 (기존 활용) |
| 마이그레이션 | 없음 |

---

## 3. SEQ_NO 역할 재정의

### 3-1. baseline 판정에서 빠진 후의 SEQ_NO 용도

1. **연속 번호 부여**: 새 점의 `SEQ_NO = MAX + 1`. metric 별 관측 순서 표현
2. **baseline 값 조회 대상**: `WHERE SEQ_NO <= 30` 으로 최초 30점 기준 CENTER/UCL 산출 (`spc.py:101-105`)
3. **UI x축 라벨**: `templates/spc.html:78, 90`, `static/js/spc.js:50, 129`
4. **감사·추적**: 이 metric 에서 이 검사가 몇 번째 관측이었는지의 식별자. 삭제된 SEQ_NO 는 재활용하지 않음

### 3-2. gap 이 있는 SEQ_NO 를 UI 에 노출하는 것이 맞는가?

**판단: 그대로 노출**

**근거**:
1. **감사 추적 원칙**: SEQ_NO 는 "몇 번째 관측이었는가" 의 이력 식별자. 삭제된 검사에 붙어 있던 값을 재활용하지 않는 것이 감사 · 사후 조사에 유리
2. **알람 상관 검색**: 사용자가 특정 알람 시점을 SEQ_NO 로 검색하거나 로그와 대조할 때 원 값 유지 필수
3. **정보 손실 방지**: gap 자체가 "그 자리에 뭔가 있었고 삭제됐다" 는 정보. 사용자에게 숨기지 않는 편이 정확
4. **Chart.js 표시**: `spc.js:50` `labels.push(String(p.seq_no))` 는 category 축이라 실제로는 등간격 라벨로 노출 (예: SEQ_NO 1·2·4·5 → x축에 `1 2 4 5` 가 붙어서 표시). gap 이 "빈 공간" 으로 벌어지지 않음. 사용자 혼동 없음

**대안 (참고, 채택 안 함)**: `ROW_NUMBER() OVER (ORDER BY seq_no)` 를 x축 라벨로 사용해 gap 제거. **채택 안 함** — UI 라벨과 DB SEQ_NO 가 불일치하면 사용자가 특정 값 검색·감사 시 혼동.

---

## 4. 영향 범위

### 4-1. 정상 상태 (gap 없음) 동작 동일성

- gap 없을 때: `MAX(seq_no) = COUNT(id)` → `seq_no = MAX+1 = COUNT+1`
- 판정: `existing_count < 30 ⇔ COUNT+1 <= 30 ⇔ seq_no <= 30` (기존 조건과 동치)
- **정상 상태 결과 동일 보장**

### 4-2. 영향받는 기존 테스트

- `tests/test_spc.py::TestBaseline::test_baseline_returns_null`: 첫 점 저장 케이스. 기존 조건 (`seq_no <= 30`) 과 새 조건 (`existing_count < 30`) 이 동치이므로 **영향 없음**
- `tests/test_spc.py::TestCalculationMatches09::test_control_limits_formula` · `test_ewma_formula`: 순수 계산 테스트. 영향 없음
- `tests/test_spc.py::TestAlarmTransition::test_status_becomes_alarm_when_spc_triggers`: 기존 skip 조건은 유지 (baseline 완성 여부에 의존). FR-56 이 skip 해소를 요구하는 것은 아님

### 4-3. API 응답 영향

- `/api/spc/series` (`api/spc.py`): `get_series` 는 SEQ_NO ORDER BY 로 읽어 그대로 반환. 변경 없음
- `get_summary` 반환값 `total_points` · `baseline_complete`: 원래 COUNT 기반이라 gap 대응 이미 됨. **UI 표시 일관성 정정** — gap 이 있는 상태에서 `add_spc_point` 만 잘못 판정하던 것이 사라져 두 지점이 완전 일치

### 4-4. UI 영향

- Chart.js x축: SEQ_NO 그대로 사용
- baseline 음영: `baseline_size = 30` 과 `baseline_complete` 로 그림 (`spc.js`). `baseline_complete` 는 `get_summary` 반환값이므로 변경 없음
- gap 표시: `3-2` 판단대로 그대로 노출

---

## 5. 검증 계획

### 5-1. FR-56 검증 기준 (a)~(d) 확인 방법

- **(a) 30점 → 10점 삭제 → 새 점 phase='baseline'**: 픽스처가 30개 검사·30개 SPC_POINTS 를 심고 임의 10개 삭제. `add_spc_point("<metric>", value)` 결과의 `phase == "baseline"` 검증
- **(b) get_summary baseline_complete=False, total_points=20**: 위 상태에서 `get_summary("<metric>")` 반환 확인
- **(c) 논리 동치**: 같은 20점 상태에서 `add_spc_point` phase 와 `get_summary.baseline_complete` 가 불일치하지 않음을 assert
- **(d) NFR-17 예외 격리**: baseline 판정 예외가 발생해도 검사 저장 (INSPECTIONS INSERT) 은 롤백되지 않음. `_process_spc_and_status` 의 예외 처리 (`inspection_store.py:138-146`) 로 검증

### 5-2. 픽스처 문제

**시나리오 재현**: 30점 심고 10점 삭제하려면 프로덕션 metric ('a3' 또는 'seg_crack') 을 써야 함.

**제약**:
- `sql/schema.sql:125` `CHECK (METRIC IN ('a3','seg_crack'))` — 테스트 전용 metric 값 불가
- `SPC_POINTS.INSPECTION_ID NOT NULL` FK — 30개 검사도 함께 필요
- 프로덕션 metric='a3' 에 30점 심으면 실 시퀀스 오염. `docs/testing.md` "기존 데이터 오염 금지" 원칙 위반
- CASCADE 삭제로 완전 정리는 가능하나, 픽스처 실행 중 다른 검사가 동시에 들어오면 프로덕션 SEQ_NO 가 pytest 픽스처와 섞임

**결론: 완전 격리된 자동화 픽스처는 현재 스키마에서 불가능**.

### 5-3. 픽스처 대안 · 채택 결정 (2026-09-16 사용자 판단)

| 대안 | 방법 | 판정 |
|---|---|---|
| D-1 | 스키마 CHECK 완화 (`pytest_a3` 등 허용) + spc.py VALID_METRICS 는 그대로 + 테스트 conftest 에서 monkeypatch | **탈락** |
| D-2 | SQLAlchemy 세션 mock 으로 순수 로직 단위 테스트 | **탈락** |
| **D-3** | **통합 테스트를 pytest 밖 수동 시나리오로 문서화** | **채택** |

**D-1 탈락 사유**: 테스트 편의를 위해 프로덕션 CHECK 제약을 완화하는 것은 DB 제약을 두는 목적과 배치된다. 스키마가 앱을 우회한 경로 (직접 SQL, 다른 도구) 로 들어온 잘못된 metric 을 걸러내는 것이 CHECK 제약의 존재 이유인데, 이를 완화하면 그 방어선이 무너진다. `spc.py:VALID_METRICS` 는 애플리케이션 계층 방어선이고, CHECK 는 DB 계층 방어선으로 서로 다른 목적을 가짐.

**D-2 탈락 사유**: `docs/testing.md` §5 원칙 "실제 Oracle 을 사용 (SQLite 대체 아님)" 에 예외를 만든다. mock 도입 시 Oracle 11g 특유 규약 (SEQUENCE + TRIGGER, CHECK, CASCADE) 을 우회하게 되어 회귀 방지 효과가 낮아진다.

**D-3 채택 근거**: 프로덕션 스키마·앱·테스트 격리 원칙 모두 유지. 검증 기준 (a)(b) 만 자동화 밖으로 밀어냄. 회귀 방지는 (c) 자동 테스트로 대체.

### 5-4. FR-56 검증 기준별 방식 재분류

**FR-57 신설 없음** — 테스트 격리는 요구사항이 아니라 `docs/testing.md` 의 테스트 정책 영역.

| 기준 | 방식 | 근거 |
|---|---|---|
| (a) 30점 → 10점 삭제 → 새 점 phase='baseline' | **수동 시나리오** (`docs/testing.md` 에 절차 추가 예정) | gap 재현이 프로덕션 metric 필요, 자동화 불가 (§5-3) |
| (b) get_summary baseline_complete=False, total_points=20 | **수동 시나리오** (동상) | (a) 와 동일 시나리오 안에서 확인 |
| (c) `add_spc_point` phase 판정과 `get_summary.baseline_complete` 의 논리 동치 | **자동 테스트** (`tests/test_spc.py` 신규 케이스, 4단계) | gap 없는 상태에서도 동치성은 유지되어야 함. 정상 상태 (예: 5점 · 30점 · 31점) 에서 두 지점 반환값이 서로 모순되지 않음을 assert. gap 재현 없이 검증 가능 |
| (d) NFR-17 예외 격리 (판정 로직 예외가 검사 저장을 롤백하지 않음) | **기존 NFR-17 검증 방식과 동일** | `_process_spc_and_status` (`inspection_store.py:118-126, 130-136`) 의 `try/except` 로직으로 이미 격리. 별도 자동 테스트 신설 안 함 (NFR-17 도 자동 테스트 없이 코드 리뷰 + 서술로만 유지되고 있음, 확인 완료: `tests/` 안 NFR-17 매치 0건) |

### 5-5. 3단계 (구현) 범위 확정

3단계 (구현) 는 **프로덕션 코드 (`app/services/spc.py`) 만 수정**. 스키마·마이그레이션 없음. 4단계 (테스트) 에서 (c) 자동 케이스만 신규.

- 수정 대상: `app/services/spc.py:71-78` 판정 로직 1군데
- 스키마 변경: 없음
- 마이그레이션: 없음
- 4단계 신규 테스트: (c) 논리 동치 자동 케이스 1건 + `docs/testing.md` 에 (a)(b) 수동 시나리오 절 추가

---

## 6. 변경 이력

- 2026-09-16: 초안 (COUNT 기반 후보 A 채택 · 픽스처 D-1 잠정 권장)
- 2026-09-16: §5 갱신 — 픽스처 D-3 (수동 시나리오) 채택. D-1·D-2 탈락. FR-57 신설 없음. (c) 는 자동 · (d) 는 기존 NFR-17 검증 방식 준용
