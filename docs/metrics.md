# 정량 수치 실측표 (metrics.md)

프로젝트의 정량 지표를 한 파일에 모은 단일 출처 (single source of truth). 외부 산출물 (보고서·발표) 작성 시 이 파일에서 값을 가져다 쓴다.

## 표기 규약

각 항목의 값 앞에 아래 표기를 붙인다.

- **[실측]** — 이 문서 작성 시점에 직접 측정. 재측정 명령 함께 명시
- **[문서]** — 기존 문서에 기록된 값. 재측정하지 않음. 출처 문서·위치 명시
- **[미측정]** — 값이 없거나 확인 불가

## 측정 환경 (실측 항목 공통)

- **일자**: 2026-09-16 (오전 · 오후 재측정 반영)
- **호스트**: WSL2 Ubuntu on Windows, Linux 6.6.87.2-microsoft-standard-WSL2
- **CPU**: 11th Gen Intel Core i7-1165G7 @ 2.80GHz, 8 cores (torch threads=4)
- **Python**: 3.12.3, torch 2.14.0+cu130, ultralytics 8.4.144, opencv 5.0.0
- **DB**: Oracle 11g XE (원격 호스트, WSL 게이트웨이 IP), Instant Client 19.32 (thick 모드)
- **환경변수**: `ORACLE_CLIENT_LIB=<Oracle IC 경로>` · `LD_LIBRARY_PATH=$ORACLE_CLIENT_LIB` (예: `/opt/oracle/instantclient_19_32`)

### 실행 사전 조건 (재측정 명령 공통)

각 §*재측정 명령* 은 아래를 이미 실행한 셸에서 수행:

```bash
source .venv/bin/activate
export ORACLE_CLIENT_LIB=/opt/oracle/instantclient_19_32   # 실 경로로 대체
export LD_LIBRARY_PATH=$ORACLE_CLIENT_LIB
```

이하 재측정 명령 블록에서는 이 두 export 를 생략하고 실 명령만 표기.

---

## 1. 시스템 규모

### 1-1. 라우트

**[실측] 총 36 rule** — static 제외, 에러 핸들러 제외, `app.url_map.iter_rules()` 기준

- HTML 응답 라우트: **34**
- JSON API 라우트: **2** (`/api/sequence/<seq_id>`, `/api/spc/series`)
- 같은 rule 에 GET+POST 함께 등록된 rule: **8** (하나로 셈)
- 에러 핸들러 (별도 등록, 총계 제외): **4건** (HTTP 403, 404, 500, Exception)

**재측정 명령**:
```bash
python -c "
from app import create_app
app = create_app()
rules = [r for r in app.url_map.iter_rules() if r.endpoint != 'static']
print(len(rules))
"
```

**최종 측정일**: 2026-09-16

**상세 목록**: `docs/routes.md`

### 1-2. 애플리케이션 모듈

**[실측] 총 36 Python 파일** (`app/` 하위)

| 계층 | 파일 수 |
|---|---|
| `app/` 루트 (`__init__.py`, `config.py`, `db.py`, `models.py`, `store.py`, `auth_utils.py`, `logging_config.py`) | 7 |
| `app/routes/` (blueprint 10 + `__init__.py`) | 11 |
| `app/api/` (blueprint 2 + `__init__.py`) | 3 |
| `app/services/` (`auth`, `inspection_store`, `spc`, `board`, `admin`, `cleanup`, `pipeline`, `__init__.py`) | 8 |
| `app/ml/` (`loader`, `anomaly`, `detect`, `segment`, `shape`, `advisor`, `__init__.py`) | 7 |

**재측정 명령**:
```bash
find app -type f -name "*.py" | wc -l
for d in app app/routes app/api app/services app/ml; do
  echo "$d: $(find $d -maxdepth 1 -type f -name '*.py' | wc -l)"
done
```

**최종 측정일**: 2026-09-16

### 1-3. DB 스키마 객체

**[실측]**:

| 객체 | 수 |
|---|---|
| 테이블 | 7 (USERS, INSPECTIONS, DETECTIONS, SPC_POINTS, FINDINGS, POSTS, COMMENTS) |
| 시퀀스 | 7 |
| 트리거 (BEFORE INSERT, PK 자동 채움) | 7 |
| 사용자 명명 인덱스 | 5 |
| 명명 CHECK 제약 | 1 (`CHK_SPC_METRIC`, §3-6 도입 · FR-57) |

**재측정 명령**:
```bash
grep -icE "^\s*CREATE\s+TABLE" sql/schema.sql
grep -icE "^\s*CREATE\s+SEQUENCE" sql/schema.sql
grep -icE "^\s*CREATE.*TRIGGER" sql/schema.sql
grep -icE "^\s*CREATE\s+INDEX" sql/schema.sql
```

**최종 측정일**: 2026-09-16

**상세**: `docs/schema.md`

### 1-4. 코드 줄 수

**[실측]** (`wc -l` 결과, FR-57 관련 5 단계 커밋 후):

| 대상 | 줄 수 |
|---|---|
| `app/` (Python 36 파일) | **5,207** (was 5,109) |
| `tests/` (Python 파일) | **1,502** (was 1,165, +337) |
| `sql/` (`.py` + `.sql`) | **841** (was 704, +137: migrate_3_6.py 등) |

**재측정 명령**:
```bash
find app -type f -name "*.py" | xargs wc -l | tail -1
find tests -type f -name "*.py" | xargs wc -l | tail -1
find sql -type f \( -name "*.py" -o -name "*.sql" \) | xargs wc -l | tail -1
```

**최종 측정일**: 2026-09-16

**증분 원인**: FR-56·FR-31·FR-57 요구·설계·마이그레이션·구현·테스트 5 단계 (커밋 a894831 → 878a5c3), 실측 스크립트 `59_defect_count_dist.py`·`60_c_u_charts.py`·`61_c_ewma_check.py`·`62_gap_vs_defect.py`·`63_line_speed.py` (§24·§25)

---

## 2. 품질

### 2-1. pytest 케이스 수

**[실측] 106 tests collected** (skip 포함)

- passed: **105** (was 86)
- skipped: **1** — `test_status_becomes_alarm_when_spc_triggers`
- **skip 사유**: baseline 미완성 상태에서 SPC 알람 로직이 발동하지 않아 조건부 skip. baseline 30점을 자동으로 채울 수 없는 이유는 **`SPC_POINTS.METRIC CHECK 제약이 프로덕션 metric 만 허용** (`sql/schema.sql:125` `CHK_SPC_METRIC IN ('a3','seg_crack','pinhole_count')`, FR-57 도입). 테스트 전용 metric 을 만들려면 CHECK 를 완화해야 하는데 이는 DB 제약 목적과 배치되므로 (docs/design-spc-baseline.md §5-3 D-1 탈락 사유) 자동화 불가. 대신 프로덕션 metric 으로 baseline 30점 채우면 다른 세션 검사와 SEQ_NO 가 섞여 오염이 우려됨. 알람 시나리오는 `docs/testing.md` "SPC baseline gap 시나리오" 로 수동 검증

**이전 서술 정정**: 종전 "SPC_POINTS 는 metric 별로 SEQ_NO 가 앱 전체 누적" 문구는 §25-1 조사에서 부정확 확인 — SEQ_NO 는 metric 안에서만 누적, metric 간에는 독립 (`spc_service.add_spc_point` 의 `max_seq` 쿼리가 `metric == ...` 필터). 위 CHECK 제약 근거로 정정

**재측정 명령**:
```bash
python -m pytest --collect-only 2>&1 | tail -2
python -m pytest 2>&1 | tail -3
```

**최종 측정일**: 2026-09-16

### 2-2. 커버리지

**[실측] 69%** (statements **2,451**, miss **749**)

**변화**: 총 statements 2,430 → 2,451 (+21, 신규 count metric 로직 등). miss 760 → 749 (−11, 신규 테스트 커버). 백분율은 그대로 69%.

**재측정 명령**:
```bash
python -m pytest --cov=app --cov-report=term 2>&1 | grep -E "^TOTAL"
```

**최종 측정일**: 2026-09-16

**계층별 상세** (`docs/testing.md`):
- routes 평균 65~90%
- services 평균 66~86% (spc.py 는 §5-8 참조로 50% → **83%** 상승, FR-57 테스트 13 추가)
- ml/ 4~49% (모델 로드 회피 원칙)
- auth_utils, config, logging_config, store, models 90~100%

### 2-3. 테스트 실행 시간

**[실측] 37.87 초** (105 passed + 1 skipped, 커버리지 포함)

**변화**: was 39.39 s (86 tests). 테스트 케이스가 19 개 늘었으나 실행 시간은 −1.5 s. FR-57 테스트가 순수 계산·경량 fixture 위주로 총 시간에 큰 부담 없음.

**재측정 명령**:
```bash
python -m pytest --cov=app --cov-report= 2>&1 | tail -3
```

**최종 측정일**: 2026-09-16

---

## 3. 성능

### 3-1. 앱 시작 시간

**[실측] median 389.6 ms** (3회 실측: 348.5 / 392.5 / 389.6 ms)

**측정 기준**: `time.perf_counter()` 로 `from app import create_app` 시작 시점부터 `create_app()` 완료 시점까지. LD_LIBRARY_PATH 사전 설정으로 `execvpe` 재실행 회피 조건.

**재측정 명령**:
```bash
python -c "
import time
t0 = time.perf_counter()
from app import create_app
app = create_app()
print(f'{(time.perf_counter()-t0)*1000:.1f} ms')
"
```

**최종 측정일**: 2026-09-16

**기존 문서 서술과의 관계**: `docs/architecture.md` §8 · `docs/requirements.md` NFR-01 · `docs/DEVELOPMENT_PROCESS.md` §4-2 의 **257 ms** 는 §2단계 재구조화 직후 (2026-09-14 전후) 측정값. "3,735 → 257 ms · 93.1% 단축" 은 지연 로드 전환의 효과를 나타내며 그 값 자체는 유효. **현재값 389.6 ms** 는 §3-7~§3-10 에서 admin/account/storage 모듈 · 관련 계층이 추가된 후의 재측정값. 두 값이 함께 존재 (리팩터링 시점 값 + 현재값).

### 3-2. 추론 단계별 처리 시간

**[문서]** — 재측정 없음. 출처: `experiment_log.md` §17-5, `28_speed_benchmark.py`, `results_speed_benchmark.csv`

측정 조건: N=200 patch, 워밍업 10 제외, seed 42, CPU (11th Gen i7-1165G7, torch threads 4)

| 항목 | median | p90 | std | mean | 단위 |
|---|---|---|---|---|---|
| A3 전체 (gray + 70 cell std) | 10.598 | 12.651 | 1.674 | 10.756 | ms/patch |
| YOLO 순수 추론 (`results.speed['inference']`) | 46.827 | 86.764 | 37.188 | 58.224 | ms/patch |
| YOLO 전후처리 (bundle − infer) | 3.068 | 6.477 | 3.432 | 3.904 | ms/patch |
| 형태 분석 (박스별 aspect + roundness) | 0.001 | 0.247 | 1.092 | 0.139 | ms/patch |
| 세그 추론 (`43_seg_inference.py`) | 450.28 | 487.87 | — | 460.73 | ms/patch |

**측정일**: 2026-09-12 (§17-5)

**GPU 측정치**: **[미측정]** — `logs/train_seed{0..3}.log` 에 epoch 당 총 시간만 있고 patch 당 forward 시간 별도 기록 부재. **§3-3 라인 속도 환산에서 상용 라인 대응 여부 판단 불가의 원인이 이 항목**

### 3-3. 라인 속도 환산 (§25-3·§25-4)

**[문서]** — 산출: `63_line_speed.py`. 프레임 = 3×3 = 9 patch (§15-6).

**프레임당 처리 시간 (median)**:

| 스테이지 | ms/patch | ms/frame |
|---|---|---|
| A3 | 10.598 | **95.4** |
| YOLO (infer+전후처리) | 49.895 | **449.1** |
| U-Net Seg (CPU) | 450.28 | **4,052.5** |
| Full 파이프라인 | — | **4,597.0** |

**논문 파일럿 라인 (0.5 m/min) 감당 여유 배수** (실측 코팅 속도 대비 최대 감당 라인 속도):

| 스테이지 | FOV=10 mm | FOV=25 mm (폭 전체) | FOV=50 mm |
|---|---|---|---|
| A3 | 12.6× ✓ | 31.5× ✓ | 62.9× ✓ |
| YOLO | 2.67× ✓ | 6.68× ✓ | 13.4× ✓ |
| Seg (CPU) | **0.30×** ✗ | **0.74×** ✗ | 1.48× ✓ |
| Full | **0.26×** ✗ | **0.65×** ✗ | 1.31× ✓ |

**상용 라인 부족 배수 (FOV=25 mm 고정)**:

| 라인 | A3 | YOLO | Seg (CPU) | Full |
|---|---|---|---|---|
| 20 m/min | **1.27×** 부족 | 5.99× | 54.0× | 61.3× |
| 50 m/min | 3.18× | 14.97× | 135.1× | 153.2× |
| 80 m/min | 5.09× | 23.95× | 216.1× | 245.2× |

**남은 [가정]**: FOV (10/25/50 시나리오), 프레임 간격 dt (환산에 직접 필요 없음). **FOV 는 절대값을 좌우하나 스테이지 서열 (A3 > YOLO > Seg) 은 어떤 FOV 에서도 뒤바뀌지 않음**. 상용 라인 대응은 GPU 전환·배치 처리 필수. **GPU 세그 감당 여부는 §3-2 [미측정] 이라 저장소만으로 판단 불가**

**측정일**: 2026-09-16

---

## 4. 모델 성능

전부 **[문서]** — 재학습 필요. 출처는 `experiment_log.md` 및 `runs/` 하위 학습 로그.

### 4-1. 핀홀 검출 mAP@0.5

**분할 방식**: 프레임 단위 stratify (§19-1), 대표 방식 (c) 프레임 전체 학습

| split | 값 | 조건 |
|---|---|---|
| val | 0.978 ± 0.009 | N=150 patch, seed 0~3 (§19-2) |
| test | 0.975 ± 0.023 | R7/700 hold-out N=38 patch · 19 pinhole 객체 (§19-3) |

**출처**: `experiment_log.md` §19-2·§19-3, `runs/pinhole_frames_seed{0..3}/results.csv`

**측정일**: 2026-09-13

**주의**: 저자 baseline 0.7956 대비 비교는 **폐기** (§19-6, 저자 분할 방식 미상). 기존 patch 무작위 분할 val 0.924 는 원본 프레임 82% train 공유로 **폐기** (§18-12)

### 4-2. 크랙 세그멘테이션 IoU

**모델**: U-Net + ResNet34 (ImageNet), 3 클래스 (배경/crack/delam)

| split | crack IoU | crack Dice | N |
|---|---|---|---|
| val | 0.7431 ± 0.0021 | 0.8527 ± 0.0014 | 382 patch |
| test_in (R1 홀드아웃) | 0.7719 ± 0.0038 | 0.8712 ± 0.0024 | 118 patch |
| test_out (R7/700 도메인) | 0.5455 ± 0.0210 | 0.7058 ± 0.0174 | 132 patch |

**출처**: `experiment_log.md` §20-2, `runs/semantic/seed{0..3}/test_results.txt`

**측정일**: 2026-09-13

**seed**: 0~3 평균 ± 표준편차

### 4-3. 박리 세그멘테이션 IoU (참고값)

| split | delam IoU | N |
|---|---|---|
| val | 0.6709 ± 0.1388 | 382 |
| test_in | 0.5870 ± 0.0481 | 118 |
| test_out | 0.3799 ± 0.1094 | 132 |

**참고값 처리 사유** (§20-5): (1) seed 편차 큼 (val seed 1 만 0.4629, 다른 3 seed 0.73~0.75) (2) best epoch 선택이 val crack IoU 기준이라 delam 은 우연히 요동 고점에 걸림 (seed 2·3 best − tail median 차 +14.5%p, +28.3%p) (3) tail median 기반 평균 0.5628 로 다시 봐야 함. 자소서·발표 주 지표에서 제외

**출처**: `experiment_log.md` §20-2·§20-5

---

## 5. 검증 결과

전부 **[문서]** — 각 실험 스크립트 산출 CSV 기반.

### 5-1. A3 vs 정답 마스크 면적 Spearman

| 대조 | pooled Spearman | pooled Pearson | N |
|---|---|---|---|
| A3 vs area_or | 0.8652 | 0.6460 | 2,227 patch |
| A3 vs area_crack | 0.8249 | 0.8041 | 2,227 |
| A3 vs area_pinhole | −0.2031 (크랙 교란) | −0.1497 | 2,227 |
| **세그 vs 정답 area_crack** | **0.9825** | 0.9892 | 2,227 |

**출처**: `experiment_log.md` §15-7·§21-2, `results_mask_area.csv`, `results_seg_area.csv`

**측정일**: 2026-09-12 (§15) · 2026-09-13 (§21)

### 5-2. 경계 오탐 비교 (A3 vs YOLO)

**대상**: 3 그룹 (H = 경계 std 상위 1% · N = 정상 기준선 · P = 정답 pinhole)

| 그룹 | N | YOLO 박스 있는 cell 비율 |
|---|---|---|
| H (경계) | 179 | 0.011 (박스 2개, conf 0.29·0.30) |
| N (정상) | 200 | 0.000 |
| P (정답) | 232 | 0.655 (박스 152개, conf median 0.57) |

**해석**: H 에서 A3 는 강하게 반응하나 YOLO 는 P 대비 60배 낮게 반응 → A3 와 YOLO 는 서로 다른 특징을 본다

**출처**: `experiment_log.md` §18-2·§18-3, `30_yolo_boundary_check.py`

**측정일**: 2026-09-13

### 5-3. SPC 경보 미검 건수

**patch 시계열 단위** (min_gap=10, ±5 매칭창) — `experiment_log.md` §22-1

| method | truth | matched_truth (TP) | FP | FN | Recall | Prec | F1 |
|---|---|---|---|---|---|---|---|
| A3 | 105 | 73 | 12 | **32** | 0.695 | 0.859 | 0.768 |
| seg_crack | 105 | 105 | 6 | **0** | 1.000 | 0.946 | 0.972 |

**프레임 단위 대칭 조합** (정답 · 예측 모두 프레임 max 집계, `c_frame_max`) — §22-9

| method | truth | TP | FP | FN | TN | Recall | Prec | F1 |
|---|---|---|---|---|---|---|---|---|
| A3 | 19 | 6 | 7 | 13 | 341 | 0.316 | 0.462 | 0.375 |
| seg_crack | 19 | 12 | 9 | 7 | 339 | 0.632 | 0.571 | 0.600 |

**출처**: `results_alarm_confusion_patch.csv`, `results_frame_prediction_definitions.csv`

**측정일**: 2026-09-15 (§22-1, §22-9), b_majority 정정 2026-09-15 (§22-13)

**인용 규칙**: patch 시계열 값과 프레임 단위 값은 매칭 관례가 달라 나란히 비교 불가. 반드시 단위 병기

### 5-4. 도메인 이전성 하락률 (R7/700)

| 지표 | R1 대표값 | R7/700 | 상대 하락 |
|---|---|---|---|
| A3 vs crack area Spearman | 0.813 (R1 7시퀀스 평균) | 0.153 | **−81.2%** |
| 세그 crack IoU | 0.7431 (val) | 0.5455 (test_out) | **−26.6%** |
| 세그 delam IoU | 0.6709 (val) | 0.3799 (test_out) | −43.4% |

**출처**: `experiment_log.md` §20-6

**측정일**: 2026-09-13

### 5-5. 측정 시스템 재현성 (Gauge R&R marginal)

**[문서]** — 정상 그룹 marginal 재현성 = 1 − fixed_threshold_fpr. N=3,600 정상 cell, 반복 없음, seed 42 단일 실행.

| 교란 | A3 재현성 | B2 재현성 |
|---|---|---|
| none | 0.950 | 0.950 |
| bright_shift +25 | 0.950 | 0.936 |
| bright_shift −15 | 0.950 | 0.923 |
| noise σ=3 | 0.871 | **0.000** |
| noise σ=8 | 0.004 | 0.000 |
| noise σ=15 | 0.000 | 0.000 |
| blur σ=1.5 | 0.976 | 0.954 |

**출처**: `results_measurement_reproducibility.csv`, `experiment_log.md` §22-MSA

**측정일**: 2026-09-15 (재프레이밍), 원 실측은 08_robustness_test.py 실행 시점 (기록 없음)

**산출 불가 항목**: 표본별 판정 일치율, 결함 그룹 재현성, 반복성, 정확한 Gauge R&R %

### 5-6. 검출 실패 케이스

**[문서]** — seed0 best.pt, IoU 0.5 + §13 패딩 매칭

| split | 프레임 | 마스크 성분 | YOLO 박스 | TP | FN | FP | 미검률 | 과검률 |
|---|---|---|---|---|---|---|---|---|
| val | 150 | 89 | 99 | 81 | 8 | 17 | 0.090 | 0.172 |
| test (R7/700) | 38 | 19 | 22 | 19 | 0 | 3 | 0.000 | 0.136 |
| **통합** | **188** | **108** | **121** | **100** | **8** | **20** | **0.074** | **0.165** |

**IoU 문턱 민감도** (§22-DET-9):

| IoU 문턱 | TP | FN | FP | Recall | Precision | F1 |
|---|---|---|---|---|---|---|
| 0.30 | 104 | 4 | 15 | 0.963 | 0.874 | 0.916 |
| 0.50 (현행) | 100 | 8 | 20 | 0.926 | 0.833 | 0.877 |
| 0.70 | 87 | 21 | 34 | 0.806 | 0.719 | 0.760 |

**과검 20건 자동 분류** (§22-DET-11):

| 그룹 | n | conf mean |
|---|---|---|
| (a) 라벨 누락 후보 | 7 | 0.479 |
| (b) 선형·대역 후보 | 3 | 0.529 |
| (c) 기타 | 10 | 0.564 |

**출처**: `results_detection_failures.csv`, `results_iou_threshold_sweep.csv`, `results_fp_classification.csv`

**측정일**: 2026-09-16

### 5-7. 갭 vs 결함 경향 (§25-2)

**[문서]** — 산출: `62_gap_vs_defect.py`. 논문 (Sci Data 2026, DOI 10.1038/s41597-025-06419-1) 서술 "갭↑ → 결함↑" 재현 검증.

**Spearman(gap, X), R1 만 n=7** (R7 은 run·position 교란 배제):

| 축 | Spearman | 판정 |
|---|---|---|
| c̄ (프레임당 YOLO 핀홀 수) | **−0.234** | **미재현** |
| area_crack (patch mean) | **+0.811** | 재현 |
| 결함 프레임 비율 | **+0.918** | 재현 |

**핵심 발견**: **핀홀과 크랙이 서로 다른 경향**. 논문의 통합 서술은 크랙 지배 경향이며 핀홀은 U자 좌측 팔 (좁은 갭 응집체 걸림, CLAUDE.md §핵심 발견 2) 로 갭 축에서 재현되지 않음.

**해석 주의**: R7/700 포함 (n=8) 시 defect_ratio Spearman +0.918 → +0.630 하락. R7 은 run·position 동시 다름이라 gap 축을 흔든다.

**시퀀스별 표**: `experiment_log.md §25-2` 참조.

**측정일**: 2026-09-16 (§25-2)

**산출**: `figures/gap_vs_defect.png` (3 패널 산점도, R1 파란 원, R7 빨간 삼각).

### 5-8. 계수형 관리도 (§24) — 포아송 적합 · c/u/p 판정

**[문서]** — 산출: `59_defect_count_dist.py`, `60_c_u_charts.py`, `61_c_ewma_check.py`. 대상: N=367 프레임 (`detections_cache.json` 재집계).

**포아송 적합** (§24-1):

| 지표 | 값 |
|---|---|
| 평균 c̄ | **1.480** |
| 분산 / 평균 (분산 지수) | **0.904** (≈1 → 포아송 부합, 과분산 없음) |
| χ² 적합도 | 3.19 (dof=4) |
| p-value | **0.529** (H₀ 유지 → 포아송 근사 유효) |

**c 관리도 (통합)** (§24-2):

- c̄ = 1.480, **UCL = c̄ + 3√c̄ = 5.129**
- 통합 UCL 이탈: **1 건** (R1/600 frame=569, c=6)

**시퀀스 층별 c̄ 편차** (§24-5): 0.88 (R7/700) ~ 1.76 (R1/1100). 시퀀스 간 이질성.

**u 관리도 기각 근거** (§24-3):

- ū = 0.203 (= 총 c / 총 patch)
- **Spearman(n_patch, u) = −0.519** — 정규화가 도입한 **인공 음의 상관**. 이 데이터 특유 왜곡
- 실배포 시 n_patch=1 고정 → u = c 로 c 관리도와 동일

**p 관리도 불가 근거** (§24-6): 프레임 결함 비율이 이미 **98.1% (R1 대칭 기준)** 또는 **79.0% (mean 기준)** — 관리도가 성립하는 정상 상태 전제 미충족. 저자 큐레이션 (결함 프레임 위주 선별) 결과라 배터리 실운영 (>99% 정상) 에서는 p 관리도 정합 가능하나 이 데이터로는 검증 불가.

**Poisson EWMA 실측** (§보완4 설계 §1, `61_c_ewma_check.py`):

| 방식 | UCL | 이탈점 수 |
|---|---|---|
| Shewhart 단독 | 5.129 | 1 |
| Shewhart + EWMA λ=0.1 | 2.317 | 7 (+7) |
| Shewhart + EWMA λ=0.2 | 2.696 | 5 (+5) |
| Shewhart + EWMA λ=0.3 | 3.013 | 1 |

계수형 정답 알람 없음 → λ 최적화 근거 부재. Poisson EWMA 산출식은 Borror·Champ·Rigdon (1998), Montgomery SPC 교과서에 있으나 저장소 인용 없음. **FR-57 도입 결정: Shewhart 단독**.

**c=0 연속 감시** (§보완4 설계 §2): 실측 최장 4 프레임 (기대 0.99, 정상 범위). k≥7 연속이 20:1 이상 드묾 (기대 0.012) → 이상 신호 후보이나 이번 범위 밖 (별도 과제).

**측정일**: 2026-09-16 (§24, §보완4 설계)

---

## 6. 요구사항 · 시스템 파라미터

### 6-1. FR / NFR / 제약 / 범위 밖

**[실측]** (오후 FR-56·FR-57 추가 반영):

| 범주 | 개수 | 변화 |
|---|---|---|
| 기능 요구사항 (FR) | **57** | was 55 (+FR-56 SPC baseline gap, +FR-57 계수형 지표) |
| 비기능 요구사항 (NFR) | 29 | — |
| 제약사항 (C) | 10 | — |
| 범위 밖 (Out of Scope) | **16** | was 15 (+1) |

**재측정 명령**:
```bash
grep -c "^|FR-" docs/requirements.md
grep -c "^|NFR-" docs/requirements.md
grep -c "^|C-" docs/requirements.md
awk '/^## 4/,/^## 5/' docs/requirements.md | grep -cE "^\|"   # 헤더 2줄 제외
```

**최종 측정일**: 2026-09-16

**출처**: `docs/requirements.md`

### 6-2. SPC 계수 · λ · metric 종류 (`app/services/spc.py`)

**[문서]** — FR-31·FR-56·FR-57 결정 결과. 근거는 각 § 참조.

| 파라미터 | 값 | 근거 |
|---|---|---|
| BASELINE_SIZE | **30** | §10_spc_tuning 권장값 (실무 부분군 크기 하한) |
| SIGMA_LIMIT (L) | **3.5** | §23-3 격자 재해석 (프레임 대칭 c_frame_max, L×λ 격자, N=367) 에서 세그 관점 최적 L=3.5, A3 관점 평탄 구간 내. 원 채택 근거 (§9-3 lift 16.51) 는 §15-5·§22-3-1 에서 폐기 |
| EWMA λ (a3) | **0.1** | §23-4b LOO 8/8 non-negative, L=3.5 조건 A3 F1 최적 (λ=0.05 로 낮추면 열화) |
| EWMA λ (seg_crack) | **0.2** | §23-4a LOO 8/8 non-negative, L=3.5 조건 세그 F1 0.60 → 0.80 개선. λ=0.1 (§9-3 기존값) 은 이 조건에서 열등 |
| EWMA λ (pinhole_count) | **None** | 계수형 EWMA 미적용. 근거: Poisson EWMA 산출식 저장소 밖 지식 (Borror 1998), 계수형에 정답 알람 없어 λ 최적화 불가. `docs/design-count-control-chart.md §1` 결정 (다) — 1차 Shewhart 단독, EWMA 후속 |
| METRICS 매핑 | `{a3: continuous/0.1, seg_crack: continuous/0.2, pinhole_count: count/None}` | FR-31·FR-57. VALID_METRICS 는 keys 파생. 새 metric 추가 시 kind·λ 함께 등록 |
| 계수형 UCL 산출식 | **CENTER + 3·√CENTER** (Poisson c 관리도) | Shewhart c 관리도 관례. LCL 은 알람 판정 미사용 (FR-57 (IV)) |
| 검출기 침묵 감시 | **미구현** | c=0 연속 k≥7 후보이나 범위 밖 (§보완4 설계 §2) |

**출처**: `app/services/spc.py`, `docs/design-spc-baseline.md`, `docs/design-count-control-chart.md`, `experiment_log.md §23·§24·§보완4`

**재검증 조건**: 다른 데이터셋 (배터리 라인·다른 조성) 에서 세그 λ 최적이 0.2 밖으로 이동 시 재판단. 계수형 EWMA 는 실배포 후 알람 이력 확보 시 재검토

### 6-3. kpi_headline.json 필드 출처

웹앱 대시보드 헤더 카드에 노출되는 4개 헤드라인 지표. **수작업 캡처 파일**로 생성 스크립트 없음 (파일 서두 `note`: "여기에 캡처해 둔다"). 값이 바뀌면 저장소의 `kpi_headline.json` 을 직접 수정하고 커밋해야 한다.

**[문서]** — 값은 실험 결과 스냅샷. 재측정은 각 원 실험 스크립트로.

| 필드 | 값 | 원 실험 | 재측정 근거 파일 |
|---|---|---|---|
| `pinhole_map50` | 0.978 ± 0.009 (val, seed 4회 평균) · test 0.975 ± 0.023 (N=38) | `experiment_log.md` §19-2, §19-3 · 프레임 단위 stratify (§19-1) | `runs/pinhole_frames_seed{0..3}/results.csv` |
| `semantic_crack_iou` | 0.7431 ± 0.0021 (val) · test_in 0.7719 · test_out 0.5455 · CPU 추론 450.28 ms/patch | `experiment_log.md` §20-2 · §21-1 · U-Net + ResNet34 (imagenet) | `runs/semantic/seed{0..3}/test_results.txt` |
| `a3_speed_ms_per_cell` | 0.11 ms/cell (cell = 64×64, patch당 70 cell → 약 7.7 ms/patch) | `06_benchmark_anomaly.py:504` stdout. §17-1 단위 정정 (`ms/patch` → `ms/cell`) 이후 필드명 개명 (§17-2) | `06_benchmark_anomaly.py` 실행 로그 |
| `a3_vs_mask_spearman` | 0.8652 (pooled Spearman, N=2,227, target=area_or) | `experiment_log.md` §15-7 · stem 조인 재실행판 (§15-4) | `results_mask_area.csv`, `22_mask_validation.py` |

**폐기값 병기 (파일 내 `deprecated_value`)**: `pinhole_map50.deprecated_value = 0.924` — patch 무작위 분할 val 값. §18-12 프레임 82% train 공유 확인 후 폐기. 카드 노출 안 함

**INCAPABLE 카운트 미노출** (파일 note): `advisor_report.json` 에서 자동 계산되지만 규격 상한 0.25 가 임의값이라 headline KPI 로 부적합

**필드 개명 이력** (§17-2): `a3_speed_ms_per_patch` → `a3_speed_ms_per_cell` (2026-09-12, 단위 오표기 정정)

---

## 7. 폐기된 수치 (인용 금지)

값 인용 시 반드시 폐기 사실 또는 적용 조건을 함께 표기해야 하는 항목. 재인용 방지 목적.

**중복 서술 없이 요약 + 링크로 처리**. 상세 사유는 `experiment_log.md` 및 `docs/PROJECT_SUMMARY.md` §3 참조.

| 폐기 수치 | 값 | 폐기 사유 요약 | 상세 | 제거 완료 문서 |
|---|---|---|---|---|
| lift 16.51 · "알람 318→39 · 2.8배 증가" | 16.51 | 시퀀스 3개 산술평균 · R1/600 단독 기여 · patch 자기상관 5.43× 증폭 · min_gap 정책 변경 혼재 | `experiment_log.md` §15-5, `PROJECT_SUMMARY.md` §3-1 | README, PROJECT_SUMMARY (경고 박스 유지) |
| 저자 baseline 0.7956 대비 +12.8%p | +12.8%p | 저자 분할 방식 미상 · 라벨 83% 재현 · patch 무작위 조건 | §19-6, §3-2 | README, PROJECT_SUMMARY |
| val mAP@0.5 0.924 ± 0.012 | 0.924 | val 원본 프레임 82%, test 80% 가 train 과 공유 | §18-12, §3-3 | README, PROJECT_SUMMARY |
| YOLO "55 ms/image" | 55 | 산출 스크립트 확인 불가 · 재측정 median 47 / mean 58 / p90 87 | §17-3, §3-4 | app STAGE_INFO, kpi_headline |
| 공정능력 판정 GOOD/MARGINAL/INCAPABLE | — | USL 0.25 임의값 · area_or 도메인 적용 시 뒤집힘 | §9-5, §15-10, §3-5 | 표는 유지, 절대값 인용만 금지 |
| "A3 → YOLO 캐스케이드" 서술 | — | 오프라인 시뮬레이션에서 p10 미검률 11% > 절감률 8.3% | §17-6, §3-6 | 문서 서술을 "병렬 분업" 으로 수정 |
| 22_mask_validation 첫 판 상관 (Spearman 0.7065 등) | 0.7065 | 4-튜플 조인 · stem 컬럼 부재로 형제 patch 값 덮어씀 | §15-2, §3-7 | results_mask_area.csv 재생성 |
| X̄-R 부분군 관리도 | — | 부분군 크기 n 이 결함량과 강한 상관 (Spearman 0.65~0.69) | §15-9, §3-8 | 23_subgroup_spc.py 미작성 |
| "세그가 정답 알람 105건 100% 근접 일치" | 100% | patch 시계열 단위. 프레임 단위 recall 은 세그 0.632 (max) / 0.471 (mean) | §22-2, §22-9, §3-9 | 인용 시 단위 병기 |
| §22-2·§22-4 프레임 F1 0.15~0.21 절대값 | 0.15~0.21 | 정답·예측 집계 비대칭 편향 (a_any). 대칭 조합에서 세그 F1 0.600 (max) | §22-9, §22-10, §3-10 | 대칭 조합으로 대체 |
| "프레임 F1 기준 A3 가 세그보다 우세" | — | a_any 편향. 대칭 조합에서 세그 F1 0.600 > A3 0.375 (max) | §22-9, §3-11 | 표는 유지, 결론만 수정 |
| "미검 32건 → 0건" 단독 인용 | 32/0 | patch 시계열 단위 · 라인 판정 단위 아님. 프레임 대칭 A3 FN 13 (max) · 세그 FN 7 | §22-1, §22-2, §3-12 | 인용 시 단위 병기 |
| "과검률 16.5%" 단독 인용 | 0.165 | IoU 매칭 편의 4건 이중 카운트 · (a)/(b)/(c) 그룹 분류 병기 필요 | §22-DET-9, §22-DET-12, §3-13 | 인용 시 그룹 병기 |
| b_majority F1 0.000 (§22-9 초기값) | 0.000 | `pred_b_majority` 가 min_gap 병합된 인덱스 입력. raw 인덱스로 정정 후 F1 세그 max 0.184, A3 max 0.197 | §22-13, §3-14 | 50_frame_prediction_definitions.py:130-143, 179-193 정정 |
| **"B2 마할라노비스 초당 780패치로 실시간 충분"** | 780 patch/s | (i) §17-1 단위 정정: cell 기준이라 patch 로는 11 patch/s (89 ms/patch), (ii) §25-3 프레임 단위 (9 patch/frame): 상용 라인 20 m/min (FOV=25) 에서 A3 alone 도 1.27× 부족. **patch 단위 · 파일럿 라인 0.5 m/min 조건에서만 성립** | §17-1, §25-3, §25-4 | experiment_log §7-4·§17-5·§21-5 각주 추가 (커밋 1410cc2) |

**공통 원칙**:
- 각 폐기 항목은 결론적으로 원 값이 "왜 인용해선 안 되는 값인지" 를 함께 표기
- 대체 값이 있으면 그 값을 사용 (예: mAP 0.924 → 0.978 프레임 단위, 55 ms → median 47 ms)
- 대체 값이 없거나 정의가 다르면 "인용 금지" 로 유지 (예: 공정능력 판정, X̄-R)
- 적용 조건이 있는 항목은 조건 병기 (예: "780 patch/s" 는 patch 단위 · 파일럿 라인만)

---

## 8. 데이터셋 실험 조건 (§25-0, Sci Data 2026)

**[문서]** — 출처: Sampath, V., Lee, A.S., Miller, S.D. et al. *A Defect Dataset for Electrode Coating Manufacturing*. **Sci Data** (2026). **DOI 10.1038/s41597-025-06419-1**. figshare DOI 10.6084/m9.figshare.29260121.

**라이선스 CC BY-NC-ND 4.0** — 원문 재배포·가공 재배포 제한이라 요약만 인용.

| 항목 | 값 |
|---|---|
| 코터 | FOM Technologies **VectorSC** 벤치탑 슬롯다이 |
| 코팅 속도 | **0.5 m/min (일정)** |
| 기판 | PTFE |
| 코팅 갭 | 600~1100 µm, 100 µm 간격 |
| 코팅 폭 | **25 mm** |
| 코팅 거리 | 75 mm |
| 잉크 | Vulcan 카본 + 물/IPA 혼합용매 + Nafion 바인더 |
| 카메라 | Pixelink PL-D753CU + Navitar 12x 줌, 수직 배치, 최소 초점거리, 양측 LED |
| 프레임 취득 | 영상 녹화 후 프레임 추출, 해시맵으로 중복 제거 |
| 건조 | 공기 중 40 min + 80°C 오븐 4 min |

**여전히 [미측정]** (논문에도 없음): 카메라 시야각 (mm/frame, Navitar 12x 는 배율만), 원 영상 fps (dt). §3-3 라인 속도 환산에서 FOV 는 시나리오 (10/25/50 mm) 로 처리. dt 는 환산에 직접 필요 없음.

**시퀀스 이름 검증** (§25-1): `R1/600 ~ R1/1100` = 코팅 갭 600~1100 µm 일치. **R1** = Run 1 추정 (논문 언급 없음). **R7** = 다른 run, 유일 `middle` 위치 (다른 시퀀스 전체 `top-to-bottom-center`) → run·position 동시 다름, 원인 분리 불가. **R1/1100-1 의 `-1`** = 같은 갭 두 번째 시퀀스 (프레임 54 vs 37, area_crack 0.0241 vs 0.0385).

**상세**: `experiment_log.md §25-0·§25-1`

---

## 9. 값의 출처 · 갱신 원칙

- 이 파일은 **단일 출처**. 다른 문서의 수치와 어긋나면 이 파일이 최신
- 다른 문서의 수치를 이 파일 값으로 자동 갱신하지 않음. 불일치는 §5-6 처럼 병기하거나 문서별로 판단
- 값을 갱신할 때는 **재측정 명령을 그대로 실행**해 확인. 명령이 없는 항목 ([문서]) 은 출처 실험 결과의 값을 유지
- 새 실측이 이전 값과 다르면 **원 값을 삭제하지 말고 병기**. 예: 앱 시작 시간 §2단계 직후 257 ms (리팩터링 효과 근거) vs 실측 (2026-09-16, §3-7~§3-10 모듈 추가 후) 389.6 ms 병기

## 참조

- 자소서 인용 규칙 통합: `docs/PROJECT_SUMMARY.md` §2, §3
- 실험 원본 기록: `experiment_log.md` (§25 반영 시점 3,286+ 줄)
- 라우트 상세: `docs/routes.md`
- 스키마 상세: `docs/schema.md`
- 테스트 상세 · 수동 시나리오: `docs/testing.md`
- 요구사항 (FR-01~57): `docs/requirements.md`
- SPC baseline gap 설계 (FR-56): `docs/design-spc-baseline.md`
- 계수형 관리도 설계 (FR-57): `docs/design-count-control-chart.md`
