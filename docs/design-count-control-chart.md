# 설계 — 계수형 관리도 (FR-57)

작성 시점: 2026-09-16. 대상 요구사항: `docs/requirements.md` FR-57 및 §1.4b 검증 기준. 이월 항목 (§1.4b (III) EWMA 적용 여부) 을 이번 설계에서 결정. 3단계 (마이그레이션) 진입 전 확정.

---

## 1. EWMA 적용 여부 (§1.4b (III) 이월 건 해소)

### 1-1. 산출식 관점: 연속형과 계수형의 차이

- **연속형 EWMA UCL**: `μ + L·σ·√(λ/(2−λ)·(1−(1−λ)^(2t)))` — σ 는 baseline 30점의 표본 표준편차로 독립 추정
- **Poisson EWMA UCL**: `μ + L·√(μ·λ/(2−λ)·(1−(1−λ)^(2t)))` — σ 자리에 `√μ` (Poisson 분산 특성 `Var(X) = E(X) = μ`)

즉 **연속형 산출식의 `σ` 를 `√c̄` 로 대체**하면 근사식이 성립. 두 산출식의 구조가 동일 (λ/(2−λ) 인자, time-varying 항) 하다.

**출처**: Borror·Champ·Rigdon (1998), Montgomery *Introduction to Statistical Quality Control* — Poisson EWMA 관리도. **이 저장소에는 문헌 인용 없음** (`experiment_log §24` 는 Shewhart c 관리도만 다룸).

### 1-2. §24 데이터로 EWMA 효과 실측 (`61_c_ewma_check.py`)

새 추론 없이 `detections_cache.json` 재집계.

| 방식 | UCL (steady-state) | 이탈점 수 | Shewhart 밖 추가 |
|---|---|---|---|
| Shewhart 단독 | 5.129 | **1** | — |
| Shewhart + Poisson EWMA λ=0.1 | 2.317 | 7 | +7 |
| Shewhart + Poisson EWMA λ=0.2 | 2.696 | 5 | +5 |
| Shewhart + Poisson EWMA λ=0.3 | 3.013 | 1 | +1 |

**관찰**:
- EWMA 도입 시 이탈점 크게 증가 (λ=0.1 에서 7배)
- 계수형 지표에는 **"정답 알람" 이 없음** (FR-56·§22 은 마스크 정답 기준이었으나 c 관리도는 별도 지표. 정답 산출 불가) → **추가 검출점이 실제 이상 상황인지 오탐인지 판단 불가**
- 어느 λ 도 정량 근거 부재. 격자 최적화 불가 (정답 없음)

### 1-3. 결정: **(다) 1차 Shewhart 단독, EWMA 는 후속 과제**

**근거**:

1. **Poisson EWMA 산출식은 저장소 밖 지식** — Borror 1998, Montgomery 등 문헌은 알려져 있으나 이 저장소에 인용·검증 없음. 도입 시 산출식 자체가 근거 없이 들어감
2. **정답 없음 → λ 최적화 불가**: 연속형 EWMA λ 는 §23-3 격자·§23-4a LOO 로 정답 (마스크 area_crack) 대비 F1 최적화 가능했으나, 계수형에는 정답이 없어 같은 방식 불가
3. **연속형 EWMA λ 정량 근거도 제한적 (§23-5)**: 유지 근거가 "물리 근거 (드리프트) · 데이터셋 편의 대칭" 수준. 계수형에 동일 방식 적용 시 근거는 더 약함
4. **§보완4 조사 결과 c 관리도 자체 성립** (`experiment_log §24-1`): Shewhart 단독으로 포아송 분포 가정 만족 · 관리한계 명확
5. **후속 과제 분리 이점**: 실배포 후 실제 알람 이력·수동 조치 데이터 확보 후 EWMA 필요성 재판단 가능. 현 시점 도입은 **근거 없는 복잡화**

**대안 (가) Shewhart 단독** 과 **(다) 1차 Shewhart, EWMA 후속** 의 차이**: (가) 는 EWMA 를 명시적으로 배제하나 (다) 는 "필요 시 나중에" 라는 확장 여지를 남긴다. 요구사항 관점에서 두 선택이 실질적으로 같으나, 문서에는 "후속 과제" 로 기록해 재검토 여지를 남긴다.

**미실시 항목 명시** (한계):
- Poisson EWMA 산출식 문헌 인용 미확보
- λ 최적값 결정 근거 부재
- 후속 재검토 조건: 실배포 후 c 관리도 알람 이력 수집 · 실제 이상 상황에 대한 라벨 확보 시 (i) EWMA 필요성 (ii) λ 값 재판단

---

## 2. 검출기 침묵 감시 (LCL 대체)

### 2-1. §24 데이터에서 c=0 연속 구간 (`61_c_ewma_check.py`)

| 지표 | 값 |
|---|---|
| c=0 연속 최장 | **4 프레임** (R1/1100-1 시퀀스) |
| 연속 길이 분포 | `{1: 25, 2: 12, 3: 4, 4: 4}` |

### 2-2. 포아송 가정 하 기대 (검출기 정상 작동 전제)

`P(c=0) = e^−1.480 = 0.228`. 독립 관측 가정에서 k 연속 확률 = 0.228^k. N=367 프레임에서 시작점 기대 수:

| k | P(0 연속 k) | 기대 시작점 수 (N·P) |
|---|---|---|
| 1 | 0.2277 | 83.6 |
| 2 | 0.0519 | 19.0 |
| 3 | 0.0118 | 4.3 |
| 4 | 0.0027 | **0.99** (실측 4회 관측) |
| 5 | 0.0006 | 0.22 |
| 6 | 0.00014 | 0.05 |
| 7 | 0.000032 | **0.012** ← 20:1 이상 드묾 |

**실측 최장 4 는 기대 (0.99) 대비 4배**로 다소 잦은 편. 다만 시퀀스별 λ 편차 · patch 배가 등으로 완전 독립 아님 → 4 정도는 정상 범위로 볼 수 있음.

**k=7 이상 임계**: N=367 규모에서 20:1 이상 드문 사건. 실제 이상 신호 후보로 유의미.

### 2-3. 이번 범위 포함 여부: **별도 과제로 분리**

**근거**:

1. **c 관리도와 다른 판정 규칙**: 관리도는 단일 점 이탈 · 연속성 알람은 "run rule" (Nelson rules 등). 두 규칙을 하나의 요구사항에 섞으면 복잡화
2. **저장소에 CUSUM · Nelson rules · 검출기 침묵 판정 근거 없음**: c 관리도 자체는 §24 로 확립됐으나 침묵 감시는 별도 검토 필요
3. **§24 실측 최장 4 는 정상 범위** — 현 시점 침묵 감시 도입의 실용적 이득 낮음
4. **FR-57 범위 명확화**: 이번은 c 관리도 도입만. 침묵 감시는 별도 요구사항 (예: FR-58 검출기 침묵 감시 · 연속 관측 규칙) 으로 분리하는 것이 요구사항 응집도 유지에 유리

**한계 항목으로 문서화**: `experiment_log §24` 또는 `docs/PROJECT_SUMMARY.md` §8 에 "c=0 연속 감시 미도입 · k≥7 이 이상 신호 후보" 사실 기록. `docs/testing.md` 수동 시나리오 항목에도 참조 여지.

---

## 3. metric 종류 구분 방식

### 3-1. 후보 구조

**후보 A**: 기존 `EWMA_LAMBDAS` dict 를 확장해 metric 별 (종류·λ·산출식) 정보를 동시 보관

```
METRICS = {
    "a3":        {"kind": "continuous", "lambda": 0.1},
    "seg_crack": {"kind": "continuous", "lambda": 0.2},
    "pinhole_count": {"kind": "count", "lambda": None},  # EWMA 미적용
}
```

**후보 B**: 별도 매핑 두 개 (`CONTINUOUS_METRICS`, `COUNT_METRICS`)

**후보 C**: metric 이름 접미사 규칙 (예: `_count` 접미사면 계수형)

### 3-2. 선택: **후보 A (확장 매핑, 단일 진실 소스)**

**근거**:

1. **FR-31 (EWMA_LAMBDAS 단일 매핑) 원칙 유지**: "새 metric 추가 시 EWMA_LAMBDAS 에 함께 등록" 규칙이 이미 시행 중. 여기에 `kind` 필드만 추가하는 것이 자연스러움
2. **정보 누락 경로 차단**: metric 이름 등록 시 종류·λ 를 동시에 필수로 두면 (dict entry 가 `{"kind": ..., "lambda": ...}` 구조) 필드 누락 시 실행 시 실패
3. **접미사 규칙 (C) 배제**: 이름 규칙은 규약이지 코드 강제가 아님. 오타·규약 위반에 취약
4. **매핑 두 개 (B) 배제**: metric 하나가 두 매핑에 있거나 어느 쪽에도 없는 상태가 가능. FR-31 "VALID_METRICS 와 매핑 키 일치" 원칙과 상충

### 3-3. VALID_METRICS 파생

`VALID_METRICS = tuple(METRICS.keys())` (기존 `tuple(EWMA_LAMBDAS.keys())` 패턴 유지). 새 metric 추가 시 METRICS 에 (kind, λ) 함께 등록 → VALID_METRICS 자동 반영 → 누락 경로 차단.

### 3-4. metric 종류에 따른 분기

`add_spc_point` 내에서 `METRICS[metric]["kind"]` 로 분기:
- `continuous`: 기존 로직 유지 (baseline 값들로 σ 계산 → σ 기반 UCL, EWMA λ 재귀)
- `count`: baseline 값들로 c̄ 계산 → √c̄ 기반 UCL, EWMA 미적용 (§1-3 결정)

`get_summary` 도 동일 분기. 반환 필드 (`sigma`, `ewma_lambda` 등) 는 종류별로 채워지지 않은 값 처리 규약 필요 (예: 계수형은 `ewma_lambda=None`, 연속형은 `c_bar=None`). 세부는 구현 단계.

---

## 4. 새 metric 정의

### 4-1. metric 이름: **`pinhole_count`**

**명명 규칙**:
- 기존 `a3` (밝기 표준편차, 연속형) · `seg_crack` (세그 크랙 면적비, 연속형) 은 지표 성격만 명시
- 계수형은 성격 명시 필요 → `<대상>_count` 규칙 채택. 향후 `crack_seg_count` 등 확장 여지
- `pinhole` 은 YOLO 검출 대상 명시

### 4-2. 저장값

- **프레임당 YOLO pinhole 박스 수** (`detections_cache.json` 기반, `run_inspection` 시 YOLO 결과의 박스 개수)
- 값 범위: 0 이상 정수 (§24 실측 0~6)

### 4-3. `SPC_POINTS.VALUE` 타입 확인

`sql/schema.sql:127`: `VALUE NUMBER(10,6)` — Oracle NUMBER 타입은 정수 저장 가능 (소수부 0 으로). 스키마 변경 불필요.

- Python 저장 시 `float(0)`, `float(1)` 등으로 캐스팅되어 저장. 후속 조회 시 `int()` 변환 필요할 수 있음 (계수형 표시에서). 세부는 구현 단계

---

## 5. 스키마 변경 범위

### 5-1. `sql/schema.sql:125` CHECK 제약 확장

**변경 전**: `CHECK (METRIC IN ('a3','seg_crack'))`

**변경 후**: `CHECK (METRIC IN ('a3','seg_crack','pinhole_count'))`

### 5-2. 마이그레이션 (`sql/migrate_3_6.py` 신설)

Oracle 은 CHECK 제약 수정 미지원 → DROP 후 ADD.

```
-- 1) 기존 CHECK 제약 이름 확인 (USER_CONSTRAINTS 조회)
-- 2) ALTER TABLE SPC_POINTS DROP CONSTRAINT <이름>;
-- 3) ALTER TABLE SPC_POINTS ADD CONSTRAINT SPC_POINTS_METRIC_CHECK
--    CHECK (METRIC IN ('a3','seg_crack','pinhole_count'));
```

**롤백**: 재실행 시 새 CHECK 를 DROP 하고 기존 값 CHECK 로 복원. `sql/migrate_3_6.py` 는 `--rollback` 플래그로 역동작 지원.

### 5-3. 기존 데이터 영향

`SPC_POINTS` 프로덕션 데이터 **0건** (§22-3-1 실측). 마이그레이션 위험 없음.

### 5-4. `SPC_POINTS.EWMA` 컬럼 처리 (계수형은 미사용)

계수형은 EWMA 값 저장하지 않음 (§1-3 결정). `EWMA` 컬럼은 `NULL` 로 저장. 스키마 이미 `NUMBER(10,6) NULL` 이라 변경 불필요.

---

## 6. 검증 계획

### 6-1. FR-57 검증 기준 (a)~(d) 대응 방식

| 기준 | 방식 |
|---|---|
| (a) baseline 30점 후 UCL = c̄ + 3√c̄ 저장 | **수동** — FR-56 (a)(b) 와 동일한 사유: pytest_ 접두사 metric 사용 불가 (스키마 CHECK), 프로덕션 metric 오염 원칙. `docs/testing.md` 수동 시나리오 추가 |
| (b) 계수형 알람 판정: Shewhart 만, LCL 미사용 | **자동** — 순수 로직 검증 (metric 종류에 따라 산출식 분기 확인). 판정 함수 단독 테스트 |
| (c) 기존 a3·seg_crack 회귀 없음 | **자동** — 기존 `TestCalculationMatches09` (`test_control_limits_formula`, `test_ewma_formula`) 유지 + a3·seg_crack 관련 케이스 재실행. 값·판정 이전과 동일 확인 |
| (d) 종류 미지정 metric 은 명확히 실패 | **자동** — 기존 `test_unknown_metric_fails_loudly_not_silently` 패턴 확장. METRICS 매핑에 없는 metric · kind 누락 등 실패 케이스 검증 |

### 6-2. 수동 항목 상세 (`docs/testing.md` 추가 예정)

FR-56 gap 시나리오와 유사한 구조:
1. `metric='pinhole_count'` 에 baseline 30점 심기 (프레임 단위 c 값 임의 · 예: 포아송 λ=1.5 랜덤)
2. `get_summary` 호출 → `baseline_complete=True`, `total_points=30`, `center=c̄`, `ucl≈c̄+3√c̄`
3. c=6 (5.13 초과) 새 점 → `is_alarm=True` 확인
4. c=0 새 점 → `is_alarm=False` (LCL 미사용)
5. finally 로 CASCADE 정리 → 실행 전 상태 복구

**한계**: FR-56 시나리오와 마찬가지로 프로덕션 `pinhole_count` metric 오염 우려. `SPC_POINTS` 실측 0건 상태에서만 실행 · 실행 완료 후 총수 0 복원 확인.

### 6-3. 회귀 방지 (자동)

- `test_ewma_formula` 는 이미 `EWMA_LAMBDAS["a3"]` 참조 (§FR-31 커밋). METRICS 구조 확장 후 접근 경로 변경 여부 확인
- `TestMetricLambdaSeparation` 5 케이스는 METRICS 구조로 이동. 매핑 스키마가 dict-of-dict 로 바뀌므로 접근 문법만 조정 (예: `EWMA_LAMBDAS[metric]` → `METRICS[metric]["lambda"]`)

---

## 7. 구현 범위 (다음 단계)

3단계 (마이그레이션) 는 `sql/migrate_3_6.py` 만. 4단계 (구현) 에서 `spc.py` 수정. 5단계 (테스트) 에서 자동·수동 케이스 신설. 이 순서로 커밋 분리.

**수정 대상 요약**:

| 파일 | 변경 |
|---|---|
| `sql/schema.sql:125` | CHECK 목록에 `pinhole_count` 추가 |
| `sql/migrate_3_6.py` | 신규 마이그레이션 (기존 CHECK DROP → 확장 CHECK ADD, rollback 포함) |
| `app/services/spc.py` | `EWMA_LAMBDAS` → `METRICS` dict-of-dict. `add_spc_point`, `get_summary` metric 종류 분기 |
| `docs/requirements.md` FR-57 | 구현 위치 채움 (§1.4b) |
| `docs/schema.md` §SPC_POINTS | CHECK 목록·설명 갱신, 새 metric 추가 절차 갱신 |
| `docs/PROJECT_SUMMARY.md` | 규모 · 8 한계 §c=0 침묵 감시 별도 과제 명시 |
| `tests/test_spc.py` | 자동 케이스 신설 (b·c·d) · METRICS 참조 접근 경로 정정 |
| `docs/testing.md` | 수동 시나리오 (a) 절차 |

**호출부·UI 무변경**: `spc.js`, `spc.html`, `api/spc.py` 는 metric 파라미터로 종류 무관 시계열을 그리므로 계수형 지표도 자동 렌더 가능. UI 변경 없음.

---

## 8. 변경 이력

- 2026-09-16: 초안. EWMA (다) 후속 이월 채택. 침묵 감시 별도 과제로 분리. metric 종류 구분 = METRICS dict-of-dict (후보 A). 새 metric 이름 `pinhole_count`.
