# 정량 수치 실측표 (metrics.md)

프로젝트의 정량 지표를 한 파일에 모은 단일 출처 (single source of truth). 외부 산출물 (보고서·발표) 작성 시 이 파일에서 값을 가져다 쓴다.

## 표기 규약

각 항목의 값 앞에 아래 표기를 붙인다.

- **[실측]** — 이 문서 작성 시점에 직접 측정. 재측정 명령 함께 명시
- **[문서]** — 기존 문서에 기록된 값. 재측정하지 않음. 출처 문서·위치 명시
- **[미측정]** — 값이 없거나 확인 불가

## 측정 환경 (실측 항목 공통)

- **일자**: 2026-09-16
- **호스트**: WSL2 Ubuntu on Windows, Linux 6.6.87.2-microsoft-standard-WSL2
- **CPU**: 11th Gen Intel Core i7-1165G7 @ 2.80GHz, 8 cores (torch threads=4)
- **Python**: 3.12.3, torch 2.14.0+cu130, ultralytics 8.4.144, opencv 5.0.0
- **DB**: Oracle 11g XE (원격 호스트, WSL 게이트웨이 IP), Instant Client 19.32 (thick 모드)
- **환경변수**: `LD_LIBRARY_PATH=/home/doyoung/oracle/instantclient_19_32`

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
source .venv/bin/activate
export LD_LIBRARY_PATH=/home/doyoung/oracle/instantclient_19_32
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

**[실측]** (`wc -l` 결과):

| 대상 | 줄 수 |
|---|---|
| `app/` (Python 36 파일) | 5,109 |
| `tests/` (Python 파일) | 1,165 |
| `sql/` (`.py` + `.sql`) | 704 |

**재측정 명령**:
```bash
find app -type f -name "*.py" | xargs wc -l | tail -1
find tests -type f -name "*.py" | xargs wc -l | tail -1
find sql -type f \( -name "*.py" -o -name "*.sql" \) | xargs wc -l | tail -1
```

**최종 측정일**: 2026-09-16

---

## 2. 품질

### 2-1. pytest 케이스 수

**[실측] 87 tests collected** (skip 포함)

- passed: **86**
- skipped: **1** — `test_status_becomes_alarm_when_spc_triggers`
- **skip 사유**: baseline 미완성 상태에서는 SPC 알람 로직이 발동하지 않음. baseline 30점 채우기가 테스트 격리 원칙에 어긋남 (SPC_POINTS 는 metric 별로 SEQ_NO 가 앱 전체 누적). 알람 시나리오 자체는 §3-4 수동 검증에서 실측 완료. 상세는 `docs/testing.md` 참조

**재측정 명령**:
```bash
source .venv/bin/activate
export LD_LIBRARY_PATH=/home/doyoung/oracle/instantclient_19_32
python -m pytest --collect-only 2>&1 | tail -3
python -m pytest 2>&1 | tail -3
```

**최종 측정일**: 2026-09-16

### 2-2. 커버리지

**[실측] 69%** (statements 2,430, miss 760)

**재측정 명령**:
```bash
python -m pytest --cov=app --cov-report=term 2>&1 | grep -E "^TOTAL"
```

**최종 측정일**: 2026-09-16

**계층별 상세** (`docs/testing.md`):
- routes 평균 65~90%
- services 평균 66~86%
- ml/ 4~49% (모델 로드 회피 원칙)
- auth_utils, config, logging_config, store, models 90~100%

### 2-3. 테스트 실행 시간

**[실측] 39.39초** (86 passed + 1 skipped, 커버리지 포함)

**재측정 명령**:
```bash
python -m pytest --cov=app --cov-report= 2>&1 | tail -3
```

**최종 측정일**: 2026-09-16

**비고**: `docs/testing.md` 는 3회 평균 ~38초로 표기. 이번 재측정은 39.39초 단회 (커버리지 계측 오버헤드 포함). 정도 차이는 ±1~2초 범위

---

## 3. 성능

### 3-1. 앱 시작 시간

**[실측] median 389.6 ms** (3회 실측: 348.5 / 392.5 / 389.6 ms)

**측정 기준**: `time.perf_counter()` 로 `from app import create_app` 시작 시점부터 `create_app()` 완료 시점까지. LD_LIBRARY_PATH 사전 설정으로 `execvpe` 재실행 회피 조건.

**재측정 명령**:
```bash
source .venv/bin/activate
export LD_LIBRARY_PATH=/home/doyoung/oracle/instantclient_19_32
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

**GPU 측정치**: [미측정] — `logs/train_seed{0..3}.log` 에 epoch 당 총 시간만 있고 patch 당 forward 시간 별도 기록 부재

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

---

## 6. 요구사항

### 6-1. FR / NFR / 제약 / 범위 밖

**[실측]**:

| 범주 | 개수 |
|---|---|
| 기능 요구사항 (FR) | 55 |
| 비기능 요구사항 (NFR) | 29 |
| 제약사항 (C) | 10 |
| 범위 밖 (Out of Scope) | 15 |

**재측정 명령**:
```bash
grep -c "^|FR-" docs/requirements.md
grep -c "^|NFR-" docs/requirements.md
grep -c "^|C-" docs/requirements.md
```

**최종 측정일**: 2026-09-16

**출처**: `docs/requirements.md`

---

## 7. 폐기된 수치 (인용 금지)

값 인용 시 반드시 폐기 사실을 함께 표기해야 하는 항목. 재인용 방지 목적.

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

**공통 원칙**:
- 각 폐기 항목은 결론적으로 원 값이 "왜 인용해선 안 되는 값인지" 를 함께 표기
- 대체 값이 있으면 그 값을 사용 (예: mAP 0.924 → 0.978 프레임 단위, 55 ms → median 47 ms)
- 대체 값이 없거나 정의가 다르면 "인용 금지" 로 유지 (예: 공정능력 판정, X̄-R)

---

## 8. 값의 출처 · 갱신 원칙

- 이 파일은 **단일 출처**. 다른 문서의 수치와 어긋나면 이 파일이 최신
- 다른 문서의 수치를 이 파일 값으로 자동 갱신하지 않음. 불일치는 §5-6 처럼 병기하거나 문서별로 판단
- 값을 갱신할 때는 **재측정 명령을 그대로 실행**해 확인. 명령이 없는 항목 ([문서]) 은 출처 실험 결과의 값을 유지
- 새 실측이 이전 값과 다르면 **원 값을 삭제하지 말고 병기**. 예: 앱 시작 시간 §2단계 직후 257 ms (리팩터링 효과 근거) vs 실측 (2026-09-16, §3-7~§3-10 모듈 추가 후) 389.6 ms 병기

## 참조

- 자소서 인용 규칙 통합: `docs/PROJECT_SUMMARY.md` §2, §3
- 실험 원본 기록: `experiment_log.md` (2,948 줄)
- 라우트 상세: `docs/routes.md`
- 스키마 상세: `docs/schema.md`
- 테스트 상세: `docs/testing.md`
- 요구사항: `docs/requirements.md`
