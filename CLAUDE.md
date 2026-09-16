전극 코팅 결함 인라인 검사 시스템
## 개발 환경
===

# \- 가상환경: `source .venv/bin/activate` 후 작업

# \- 의존성: requirements.txt

## 용어 정의 (혼용 금지)

`experiment_log.md` §16-0 에서 확정된 용어이며 앞으로 모든 문서·코드·표기에 그대로 사용한다.

- **patch**: 저자 단위 480×640 이미지. `segmentation/images/image_N.jpg` · `results_spc.csv` 의 한 행 · `results_mask_area.csv` 의 한 행
- **cell** : A3 판정 단위 64×64. patch 당 70 개 (7 rows × 10 cols). `cell_index = row * 10 + col`

"patch" 를 이전 문서에서 64×64 조각의 뜻으로 쓴 경우가 있으나(예: 06 벤치마크의 `test_normal[i]` 배열 원소), 이 문서에서는 그 의미로 사용하지 않는다. 표기 시 반드시 patch 는 480×640, cell 은 64×64 를 뜻한다는 것을 명시한다. 예: "0.11 ms/cell (patch 당 70 cell → 약 8 ms/patch)".

## 프로젝트 목적

이차전지 전극 코팅 공정의 표면 결함을 검출하고, 결함 유형으로부터 원인 공정 인자를 역추적하여 점검 항목을 제시하는 스마트팩토리 품질관리 시스템.

취업 포트폴리오. 타깃은 LG에너지솔루션·삼성SDI·SK온 스마트팩토리/Vision AI 직군, 이차전지 장비사, 제조 AI 기업 도메인 팀.

**차별화 축**: 개발자는 결함을 "검출"하지만 소재 전공자는 결함이 "왜 생겼고 물성에 어떤 영향을 주는지" 설명할 수 있다. 이 프로젝트의 가치는 검출 정확도가 아니라 검출 결과에 도메인 해석을 결합하는 레이어에 있다.

\---

## 현재 상태

```
\[1] 이상 탐지    ████████████████████  완료 - 벤치마크 6종 + 강건성 시험
\[2] 검출 (pinhole) ██████████████████  완료 - 프레임 분할 val 0.978 / test 0.975 (N=38 R7/700). 저자 비교 폐기 (§19-6)
\[2'] 세그 (crack·delam) ████████████  완료 - U-Net+ResNet34, val crack IoU 0.7431 / test_out 0.5455 (§20). delam 참고
\[3] SPC          ████████████████████  완료 - 관리도 + 시각화 (figures/spc_*.png, 15_spc_visualize.py)
\[4] Advisor      ████████████████████  완료 - 3층 규칙 기반, defect_map 조회 (16_advisor.py, advisor_report.{txt,json})
\[5] 대시보드    ████████████████████  완료 - Flask + Jinja2, /stream 재생 + /inspect 파이프라인 + /(개요) + /benchmark (app.py, 17_precompute_detections.py)
README           ████████████████████  완료 - 결과 요약 + 핵심 3개 + 부수 6개 + 재현 방법 + 한계
```

**중요**: `docs/experiment\_log.md` (782줄)에 전 과정이 기록되어 있다. 작업 시작 전 반드시 읽을 것. 이 파일이 프로젝트의 실질적 인계 문서다.

\---

## 핵심 발견 (포트폴리오의 실체)

1. **공정 조건-결함률 정량화**: CoatingVision(Nafion/PTFE·Vulcan 카본, 슬롯다이 코팅) R1 시퀀스에서 코팅 갭 600→1100µm 증가 시 표면 크랙 라벨률 59.2%→98.2%로 단조 증가. 원논문은 이 관계를 서술만 했고 수치는 없었다. 배터리 전극(NMC/PVdF/Al foil)에도 건조 수축에 의한 크랙 형성 물리는 성립하나, 갭-크랙률 곡선의 절대값은 슬러리·집전체 조성이 달라 재측정이 필요하다. 다른 지표 관측(§15-10): patch 단위 마스크 면적비 μ(area_or) 로 재보면 1000µm 까지 대체로 단조 증가, 1100µm 에서 감소·런 간 편차 확대(0.0247 vs 0.0420), 700µm 에서도 R1 0.0208 vs R7/middle 0.0624. 라벨률과 면적비는 서로 다른 지표이며 갭 축을 따라 완전히 동형은 아니다.
2. **핀홀 U자 관계 부분 확인**: CoatingVision 좁은 갭 구간에서 핀홀은 종횡비 1.21, roundness 0.687\~0.739, area\_max 3,685로 일관되게 "길쭉하고 불규칙"한 형태를 보였고, 이는 Nafion 바인더 슬러리 응집체가 슬롯다이 립에 걸리는 메커니즘과 부합한다. 넓은 갭 구간은 실험 범위 밖이라 U자의 우측 팔은 미관측이다. 배터리 전극(NMC 슬러리)에도 응집체 걸림·기포·젖음성의 원인 축은 동일하고, 종횡비·roundness 등 기하학적 형태 판정 규칙은 광학 특성과 무관하게 이전 가능하다. 다만 밝기 기반 검출 임계값은 NMC/Al foil 조합에서 하부 노출 대비가 달라져 재교정이 필요하다.
3. **딥러닝 불필요성 검증**: 밝기 표준편차 하나로 AUROC 0.983 (0.11 ms/cell, patch 당 70 cell → 약 8 ms/patch). 딥러닝은 0.990. AUROC로는 1%p 차이지만 FPR@95TPR은 8.7%→2.6%.
4. **강건성 역전 (가설 반증)**: 센서 노이즈 σ=3에서 딥러닝 오탐률 100%, 표준편차 12.9%. 성능이 좋은 방법이 환경 변동에 더 취약했다. 원인은 정상 분포가 좁아 공분산 역행렬이 커지는 것.
5. **공정능력 판정**: CoatingVision 8개 시퀀스(R1/600\~1100µm 등) 중 5개가 INCAPABLE. 정상 상태 자체가 불량이면 관리도가 무의미하며, 모니터링이 아니라 공정 조건 변경이 필요하다는 진단이다. 이 진단 프레임(INCAPABLE 우선순위 최상) 자체는 배터리 라인 SPC에 그대로 이식 가능하나, 관리 한계와 결함 허용 기준은 NMC/PVdF/Al foil 기준으로 새로 수립해야 한다.
6. **SPC 튜닝**: 알람 318→39건(8배 감소)하면서 lift 5.94→16.51(2.8배 증가). EWMA λ 0.2→0.1이 결정적.
7. **라벨 검증 실패의 진단**: IoU 재현율 0.239 → 원인이 로직이 아니라 검증 설계였음을 밝힘(박스 크기 관례 4배 차이) → 중심거리 기준으로 0.830.
8. **핀홀 검출 (프레임 단위 분할 재학습)**: YOLOv8n · val mAP@0.5 0.978±0.009 · test 0.975±0.023 (R7/700 hold-out, N=38 patch / 19 pinhole 객체). 기존 patch 무작위 분할 값 0.924±0.012 는 val 원본 프레임 82% 가 train 과 공유된 조건 (§18-12) 이라 대체됨. 저자 baseline 0.7956 대비 비교는 저자 분할 방식 미상으로 폐기 (§19-6).
9. **결함별 방법 분리 (실측 완결)**: CoatingVision에서 핀홀은 bbox 검출 (YOLOv8n, val 0.978), 크랙·박리는 시맨틱 세그 (U-Net+ResNet34, crack IoU val 0.7431 / test_out 0.5455). ① 다중 클래스 검출은 충전율 (crack 0.40, delam 0.26)로 기각 (§18-7), 인스턴스도 저자 96px 파편으로 부적합. **핵심 실측**: 세그의 R7/700 도메인 열화 −27% vs A3 통계 지표 −81% (§20-6) — 형태 판별이 산포 지표보다 도메인 변화에 3배 완만. 배터리 전극에도 결함별 형성 물리 (모세관압 크랙 전파, 접착력 vs 내부응력 박리, 기포/젖음성 핀홀) 가 서로 달라 동일 방법 분기 원칙 적용 가능.

\---

## 데이터셋: CoatingVision

* 출처: Sampath, V. et al. *Sci Data* (2026). DOI 10.1038/s41597-025-06419-1
* figshare DOI 10.6084/m9.figshare.29260121
* 라이선스 **CC BY-NC-ND 4.0** — 비상업적, 출처 표기 필수, **가공 데이터 재배포 제한**
* 2,227장, 480×640 RGB, 슬롯다이 코팅

### 클래스 분포

|클래스|이미지|비율|마스크 채널|
|-|-|-|-|
|Surface Crack|1,947|87.4%|0|
|Pinhole|519|23.3%|2|
|Delamination|203|9.1%|1|
|Unclassified|-|3.7%|3|

마스크는 RGBA 4채널 PNG, 값 {0, 255}. 채널 매핑은 12에서 검증 완료(class 0 박스 100%가 채널 2 대응).

### 파일명에 인코딩된 공정 조건

`labels.csv`의 `original\_file\_name`:

```
R7-700um-middle\_frame\_489\_patch\_1.png
```

* `R1`/`R7`: 런 번호 (R1이 94%)
* `700um`: **코팅 갭** (600\~1100, 100 간격)
* `frame\_489`: 원본 영상 프레임 번호 → SPC 시계열의 근거
* `patch\_1`: 프레임 내 패치 인덱스

파싱 성공률 100%. 이 정보가 Advisor의 실측 근거와 SPC 시계열을 가능하게 했다.

### 반드시 인지할 제약

1. **정상 이미지 44장(2.0%)뿐**, 그중 25장이 600µm에 집중 → 패치 기반 접근 불가피
2. **R1 단일 런 94%** → 물/IPA 용매비 효과 검증 불가. 용매비를 원인으로 제시할 때는 문헌 인용으로만
3. **패치 단위 데이터** (`patch\_N`) → 핀홀 밀도(단위면적당 개수) 측정 불가
4. **갭 범위가 U자형 좌측만 커버** → 과대 갭 메커니즘 미관측
5. **도메인 갭**: Vulcan 카본/Nafion/PTFE 조성으로 **연료전지 계열**. 배터리 전극(NMC/PVdF/Al foil) 아님

   * 이전 가능: 슬롯다이 코팅·건조의 결함 형성 물리(모세관압 크랙 전파, 접착력vs내부응력 박리, 기포/젖음성 핀홀)
   * 이전 불가: 활물질 광학 특성(카본블랙 vs NMC 반사율), 조명·노출 조건, 절대 결함 허용 기준
   * 구체 사례: delamination 라벨 다수가 크랙 심화 지점에 부여됨. 배터리 전극 관점에서는 크랙에 가까워 보이나 조성이 달라 확답 불가

\---

## 완료된 결과

### \[1] 이상 탐지 (06, 08)

test/normal 3,600 vs eval 결함. train 정상 패치 18,000(갭당 3,000 균등).

|방법|crack|delam|pinhole|통합|통합 FPR@95|속도|
|-|-|-|-|-|-|-|
|A3 밝기 표준편차|0.976|0.995|0.992|0.983|0.087|**0.11 ms/cell** (≈ 8 ms/patch)|
|B2 Mahalanobis|0.984|0.998|0.990|0.990|0.029|1.27 ms|
|B3 PatchCore|0.986|0.996|0.992|0.989|0.027|2.27 ms|

PatchCore가 Mahalanobis를 못 이겼다. 정상 패치가 균일한 단일 모드라 메모리 뱅크가 기여할 여지가 없다.

**강건성 (fixFPR, 임계값 고정)**

|교란|A3|B2|
|-|-|-|
|none|0.050|0.050|
|bright\_shift ±25|**0.050**|0.064\~0.077|
|noise σ=3|0.129|**1.000**|
|noise σ=8|0.996|1.000|

**수치의 낙관 편향 (반드시 명시)**: 애매한 패치를 배제했고(정상은 margin 4px까지 깨끗한 것만, 결함은 82px/15px 이상만) 조명 조건이 단일하다. 실제 인라인 검사 성능은 이보다 낮다.

### \[2] 검출 (11, 13, 14)

**핀홀 단일 클래스, 프레임 단위 분할 재학습 (seed 0\~3, §19)**

|방식|분할|train/val|val mAP@0.5|val mAP@0.5:0.95|test mAP@0.5 (N=38, R7/700)|
|-|-|-|-|-|-|
|(a) 기존 (폐기)|patch 무작위|610/124|0.924±0.012|0.597±0.032|미보고|
|(b) 대조|프레임 단위|606/123|0.973±0.010|0.560±0.046|0.983±0.017|
|**(c) 프레임 전체 (대표)**|프레임 단위|686/150|**0.978±0.009**|0.600±0.028|**0.975±0.023**|

저자 baseline 0.7956 대비 비교는 §19-6 에서 폐기 (저자 분할 방식 확인 불가).

가중치: 기존 `runs/pinhole\_v1/weights/best.pt` (patch 무작위, 참고용 유지) / 신규 `runs/pinhole\_frames\_seed{0..3}/weights/best.pt` (프레임 단위, 대표)

### \[3] SPC (09, 10)

**권장 설정**: 3.5σ + EWMA λ=0.1 + 알람 병합 10프레임

**공정능력 판정**

|시퀀스|중심선|판정|
|-|-|-|
|R1/600µm|0.042|GOOD|
|R1/800µm|0.112|MARGINAL|
|R1/700µm|0.216|MARGINAL|
|900µm 이상 5개|0.288\~0.428|**INCAPABLE**|

산출물: `results\_spc.csv` (프레임별 결함비율/이동평균/EWMA/관리한계/라벨), `results\_spc\_alerts.csv`

\---

## 다음 작업 (우선순위)

### 1\. Advisor 후속 개선 (여유 시)

- R7/700 특이성 규칙: 같은 갭 내 다른 시퀀스와 라벨률이 크게 다를 때 관측만 보고 (원인 단정 금지). 이번 세션에서 보류 - R7 표본 132장 + 위치 다름(middle vs top-to-bottom-center) 교란으로 원인 분리 불가
- 딥러닝 강건성 회복: 노이즈 제거 전처리(가우시안 블러 등)로 딥러닝 오탐률 회복 여부 검증
- 박리 conf 임계값 낮춘 재평가 (라벨 모호성 vs 데이터 부족 구분)
- 크랙 세그멘테이션 전환

## Advisor 설계 규칙 (16\_advisor.py 유지 기준)

이번 세션에서 다듬은 규칙. 향후 수정 시에도 이 원칙 유지:

1. **지배 결함 판정 금지**. "최다" 로 완화하고 격차(1위/2위 비)를 명시. 격차 1.5배 미만이면 "혼재" 라벨로 바꾼다
2. **절대 최다 + 상대 특이 병기**. 상대 특이 = 갭 참조값이 전체 갭 평균 대비 1.5배 이상. 상대 특이가 있으면 그 결함이 focused\_class 가 되어 참조 블록의 root\_causes 를 그 결함 것으로 채운다
3. **핀홀 형태 해석은 표본 크기에 종속**. 전체 시퀀스 추론(수백 장) 필수, 30장 샘플 금지. 검출 < 10 은 해석 생략, 10~19 는 low + "표본 부족" 명시, 20 이상 medium
4. **root\_causes 는 operational/design 분리 렌더링**. operational 우선, design 은 "즉시 조치 불가 — 배합/소재 설계 검토 필요" 헤더. operational 이 하나도 없으면 "현재 라인 조건에서 즉시 조치 가능한 인자 없음" 명시
5. **라벨률 편차 관측 규칙**. 시퀀스 라벨률이 갭 참조값 대비 2배 이상/절반 이하이면 관측 사실만 보고. 신뢰도 low 고정. 원인 언급 금지, "추가 실험 없이는 원인 규명 불가" 문구 필수. N >= 100 시퀀스만 대상
6. **INCAPABLE 우선순위 최상**. capability = INCAPABLE 이면 다른 진단보다 먼저 high 신뢰도로 보고
7. **요약표에 UCL 병기**. 알람 수만 두면 오해(INCAPABLE 인데 알람 적음)를 부른다. 주석으로 "관리한계가 넓어져 검출력을 상실한 상태" 명시

\---

## 개발 환경

* WSL2(Ubuntu) on Windows, VSCode + WSL extension
* 로컬 GPU는 MX450(2GB)으로 **학습 불가**. 추론과 사전학습 특징 추출만 가능
* 학습은 RunPod RTX 3090 사용
* 설치됨: ultralytics, torch(CPU), opencv-python, numpy, PIL

**RunPod 사용 시 주의**

* `data.yaml`의 `path:`가 로컬 절대경로로 저장되므로 압축 해제 직후 sed 수정 필수
* tar 업로드가 중간에 끊길 수 있음. md5로 대조할 것 (labels는 온전한데 images만 빠지면 tar 절단)
* 재업로드 시 `labels/\*.cache` 삭제 필수. 안 지우면 이전 이미지 수만 읽음

\---

## 코드 스타일 원칙

**반드시 지킬 것**

* 리스트 컴프리헨션, 삼항연산자, 중첩 람다 금지. 명시적 for/if 사용
* 한 줄에 여러 동작 압축 금지
* 변수명 축약 금지 (`img` 대신 `image`)
* 파일 일부 수정보다 **전체 재작성** 선호
* 새 개념 도입 시 코드 이전에 평문으로 로직 먼저 설명

**설계 순서**: 평문 로직 → 플랫 코드 → 함수 분리 → 필요 시 클래스화

\---

## 작업 원칙

* **한 세션에 한 레이어.** 여러 개를 동시에 만들면 통합되지 않는 코드가 나온다
* **검증이 실패하면 파라미터부터 조정하지 말고 원인을 진단하라.** 11→12→13이 그 사례다. IoU 재현율 0.239에서 임계값을 낮췄다면 근본 원인(박스 크기 관례 차이)을 놓치고 잘못된 데이터를 만들었을 것이다
* **전기화학 내용은 반드시 사용자 검수.** LLM이 채운 도메인 내용은 그럴듯하지만 틀릴 수 있고, 여기가 틀리면 프로젝트 전체가 무너진다
* **실패를 기록한다.** `docs/experiment\_log.md`에 시도-실패-개선을 남긴다. 이 기록이 포트폴리오의 절반이다
* **성능 수치는 항상 baseline과 비교해서 제시한다.** 절대값만으로는 의미가 없다
* **한계를 숨기지 않는다.** 낙관 편향, 도메인 갭, 표본 크기를 명시하는 것이 신뢰도를 높인다

\---

## 디렉토리 구조

```
coating\_vision/
├── CLAUDE.md                    # 이 파일
├── classification/labels.csv    # 원본 (공정 조건 파싱 소스)
├── segmentation/{images,masks}/ # 원본
├── detection/{images,labels}/   # 원본 (저자 핀홀 라벨 581개)
├── data/
│   ├── patches/                 # 04 산출: 27,711 패치
│   ├── detection\_auto/          # 13 산출: 3클래스 YOLO 데이터셋
│   └── pinhole\_balanced/        # 14 산출: 핀홀 단일 (610/124/140)
├── defect\_map.json              # 결함-원인-물성 매핑 (root/, 검수 필요, root\_causes 에 category=operational/design)
├── docs/experiment\_log.md       # 전 과정 기록 (782줄)
├── experiments/                 # 01\~14 스크립트
├── 15\_spc\_visualize.py         # SPC 관리도 시각화
├── 16\_advisor.py               # 3층 규칙 기반 Advisor
├── advisor\_report.txt          # 사람 읽기용 리포트
├── advisor\_report.json         # 구조화 리포트 (대시보드 재사용)
├── figures/                     # SPC 관리도 PNG 8장 + 요약 격자
├── runs/                        # YOLO 학습 결과 (pinhole\_v1/weights/best.pt)
├── results\_\*.csv                # 벤치마크/강건성/SPC 결과
├── run.py                       # Flask 대시보드 진입점 (`python run.py`)
├── app/                         # Flask 애플리케이션 패키지 (§2단계 리팩터링 완료)
│   ├── __init__.py              # create_app() 팩토리 + blueprint 5 등록
│   ├── config.py                # 경로 상수·Flask 설정
│   ├── store.py                 # JSON·CSV lazy 로드 + KPI 조립
│   ├── ml/                      # 모델 lazy 로드·스테이지 추론 (loader/anomaly/detect/segment/shape/advisor)
│   ├── services/pipeline.py     # /inspect 파이프라인 조립 (run_inspection)
│   ├── routes/                  # HTML blueprint (main/stream/inspect/benchmark)
│   ├── api/sequence.py          # JSON blueprint (/api/sequence/<seq_id>)
│   ├── templates/               # Jinja2 (base/index/stream/inspect/benchmark)
│   └── static/                  # css/style.css, js/stream.js, samples/, uploads/(업로드 임시, gitignored)
├── app_legacy.py                # (a) 단계 이전 원본 백업 (참고용, 삭제 금지)
├── 17\_precompute\_detections.py  # 전체 프레임 YOLO 사전 추론 + patch_threshold 산정
├── kpi\_headline.json            # 대시보드 헤드라인 두 값 (aggregate mAP, A3 속도)
├── detections\_cache.json        # 17 산출: {image_N.jpg → [YOLO boxes]}
└── stream\_data/                 # 17 산출: index.json + <seq_id>.json (재생용)
```

### 스크립트 목록

|파일|역할|
|-|-|
|01\_parse\_metadata.py|파일명에서 공정 조건 파싱, 갭-결함률 집계|
|02\_patch\_extraction\_check.py|정상 패치 수확량 검증|
|03\_pinhole\_shape\_analysis.py|갭별 핀홀 형태(면적/roundness/종횡비)|
|04\_extract\_patches.py|패치 추출 (누수 방지 이미지 단위 분할)|
|05\_verify\_delamination.py|박리 라벨 검증 (밝기/초점 지표 + 시각화)|
|06\_benchmark\_anomaly.py|이상 탐지 벤치마크 6종|
|08\_robustness\_test.py|교란 13종 강건성 시험|
|09\_spc\_monitor.py|SPC 관리도 및 알람|
|10\_spc\_tuning.py|파라미터 스윕 + 공정능력 판정|
|11\~13\_mask\_to\_detection|마스크→YOLO 라벨 변환 (11 실패, 12 진단, 13 수정)|
|14\_pinhole\_dataset.py|핀홀 단일 클래스 데이터셋|
|15\_spc\_visualize.py|SPC 관리도 시각화 (시퀀스별 8장 + 요약 격자)|
|16\_advisor.py|3층 규칙 기반 Advisor (SPC + 핀홀 검출 + defect\_map)|
|17\_precompute\_detections.py|전체 2,227장 YOLO 캐싱 + A3 patch_threshold 산정. `/stream` 지연 제거용|
|18\_prepare\_samples.py|`/inspect` 예시 버튼용 샘플 3장 (핀홀/크랙/정상) 을 static/samples/ 로 복사|
|app.py|Flask 대시보드 (`/stream` 인라인 재생 / `/inspect` 파이프라인 / `/` 개요 / `/benchmark` 표)|



