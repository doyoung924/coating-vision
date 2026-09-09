"""
16. Advisor - 규칙 기반 원인 진단 및 점검 항목 제시

목적:
  09/10 SPC 결과, 14 핀홀 검출기, defect_map.json 을 결합하여
  시퀀스별로 3층 리포트를 생성한다.

    [관측] 실제로 측정된 것 - SPC 지표, 알람, 검출 결과
    [해석] 관측으로부터의 추정 - 신뢰도(high/medium/low) 명시 필수
    [참조] defect_map 의 지배 인자와 공정 윈도우 - 출처 명시

우선순위 규칙:
  1. 공정능력 INCAPABLE - 다른 진단보다 먼저. 모니터링이 아닌 공정 조건 변경 필요
  2. 알람 발생 구간 - 해당 시퀀스 지배 결함 유형의 root_causes 연결
  3. 핀홀 검출 - 형태(aspect)로 갭 방향 추정. diagnostic_rule 참조

신뢰도 원칙:
  - 영상만으로 공정 파라미터를 측정할 수 없다. 모든 해석은 추정
  - 용매비는 실측 근거 없음(R1 단일 런 94%) → 문헌 인용으로만
  - 핀홀 형태 임계값은 6개 갭 조건, 단일 런에서 도출 → medium 이하
  - 도메인 갭 명시: 데이터셋은 연료전지 계열(Vulcan/Nafion/PTFE)

LLM 사용 없음. 지식베이스 조회 + 규칙 매칭으로 구성.
"""

import csv
import json
import os
import sys
import numpy as np


SPC_RESULT_PATH = "results_spc.csv"
SPC_ALERT_PATH = "results_spc_alerts.csv"
DEFECT_MAP_PATH = "defect_map.json"
LABELS_PATH = "classification/labels.csv"
IMAGE_DIR = "segmentation/images"
MODEL_PATH = "runs/pinhole_v1/weights/best.pt"

REPORT_TEXT_PATH = "advisor_report.txt"
REPORT_JSON_PATH = "advisor_report.json"

CAPABILITY_GOOD = 0.10
CAPABILITY_MARGINAL = 0.25

PINHOLE_CONF_THRESHOLD = 0.25
PINHOLE_MIN_DETECTIONS_FOR_INFERENCE = 10   # 미만이면 형태 해석 생략
PINHOLE_MIN_DETECTIONS_FOR_MEDIUM = 20      # 이상이면 medium, 사이는 low
PINHOLE_ASPECT_DEVIATION_RATIO = 1.2        # 관측 aspect 가 참조 대비 이 배수 벗어나면 별도 언급

# 지배/혼재 판정
DOMINANT_MAJORITY_RATIO = 1.5    # 1위/2위 비가 이 값 이상이면 "다수", 미만이면 "혼재"
RELATIVE_OUTLIER_RATIO = 1.5     # 갭 평균 대비 이 값 이상이면 "특이"

# 배치 크기 (YOLO 예측)
PINHOLE_BATCH_SIZE = 32

# 시퀀스별 편차 감지 규칙 (라벨률이 갭 참조값 대비 크게 벗어난 경우)
DEVIATION_MIN_SEQUENCE_N = 100   # 100장 미만 시퀀스는 표본 크기 부족으로 판정 불가
DEVIATION_HIGH_RATIO = 2.0       # 참조값의 2배 이상
DEVIATION_LOW_RATIO = 0.5        # 참조값의 1/2 이하
DEVIATION_MIN_REFERENCE = 2.0    # 참조값이 2% 미만이면 분모 노이즈 회피 위해 비교 스킵

# 핀홀 형태 임계값 (defect_map.pinhole.diagnostic_rule)
PINHOLE_ASPECT_HIGH = 1.20     # 초과 시 응집체 걸림 의심
PINHOLE_ROUNDNESS_LOW = 0.72   # 미만 시 기계적 원인
PINHOLE_ROUNDNESS_HIGH = 0.78  # 초과 시 기포/젖음성 원인


# ===============================================================
# 데이터 로딩
# ===============================================================

def load_spc_records(path):
    """CSV 를 시퀀스별로 나누어 리스트로 저장한다."""
    sequences = {}
    csv_file = open(path, "r", encoding="utf-8")
    reader = csv.DictReader(csv_file)
    for row in reader:
        key = (row["run_id"], int(row["coating_gap"]), row["position"])
        if key not in sequences:
            sequences[key] = []
        record = {}
        record["frame_number"] = int(row["frame_number"])
        record["defect_ratio"] = float(row["defect_ratio"])
        record["ewma"] = float(row["ewma"])
        record["ucl"] = float(row["ucl"])
        record["center"] = float(row["center"])
        record["label_crack"] = int(row["label_crack"])
        record["label_delam"] = int(row["label_delam"])
        record["label_pinhole"] = int(row["label_pinhole"])
        sequences[key].append(record)
    csv_file.close()
    return sequences


def load_alerts(path):
    """알람 CSV 를 시퀀스별 리스트로."""
    alerts = {}
    csv_file = open(path, "r", encoding="utf-8")
    reader = csv.DictReader(csv_file)
    for row in reader:
        key = (row["run_id"], int(row["coating_gap"]), row["position"])
        if key not in alerts:
            alerts[key] = []
        alerts[key].append({"rule": row["rule"], "frame_number": int(row["frame_number"])})
    csv_file.close()
    return alerts


def load_defect_map(path):
    file_handle = open(path, "r", encoding="utf-8")
    data = json.load(file_handle)
    file_handle.close()
    return data


def load_sequence_image_stems():
    """
    labels.csv 를 파싱하여 시퀀스 키 -> image_stem 리스트 매핑을 만든다.
    """
    import re

    mapping = {}
    csv_file = open(LABELS_PATH, "r", encoding="utf-8")
    reader = csv.DictReader(csv_file)
    for row in reader:
        original = row["original_file_name"]
        name = original
        if name.endswith(".png"):
            name = name[:-4]

        run_id = None
        gap = None
        position = None

        frame_match = re.search(r"_frame_(\d+)", name)
        condition_part = name
        frame_split = name.split("_frame_")
        if len(frame_split) > 1:
            condition_part = frame_split[0]

        run_match = re.match(r"^(R\d+)", condition_part)
        if run_match is not None:
            run_id = run_match.group(1)

        gap_match = re.search(r"(\d+)um", condition_part)
        if gap_match is not None:
            gap = int(gap_match.group(1))

        position_text = condition_part
        if run_id is not None:
            position_text = position_text.replace(run_id, "", 1)
        if gap is not None:
            position_text = position_text.replace(str(gap) + "um", "", 1)
        position_text = position_text.strip("-").strip("_")
        if len(position_text) > 0:
            position = position_text

        if run_id is None or gap is None or position is None:
            continue

        key = (run_id, gap, position)
        stem = row["file_name"]
        if stem.endswith(".jpg"):
            stem = stem[:-4]

        if key not in mapping:
            mapping[key] = []
        mapping[key].append(stem)
    csv_file.close()
    return mapping


# ===============================================================
# 관측 수집
# ===============================================================

def judge_capability(center):
    if center < CAPABILITY_GOOD:
        return "GOOD"
    if center < CAPABILITY_MARGINAL:
        return "MARGINAL"
    return "INCAPABLE"


def summarize_spc(records, alerts_list):
    """
    시퀀스의 SPC 관측을 요약한다.
    """
    summary = {}
    summary["n_observations"] = len(records)
    summary["center"] = records[0]["center"]
    summary["ucl"] = records[0]["ucl"]
    summary["capability"] = judge_capability(summary["center"])

    defect_ratios = []
    for record in records:
        defect_ratios.append(record["defect_ratio"])
    summary["defect_ratio_max"] = float(np.max(defect_ratios))
    summary["defect_ratio_median"] = float(np.median(defect_ratios))
    summary["defect_ratio_mean"] = float(np.mean(defect_ratios))

    # 라벨 기반 결함률 (해당 시퀀스에서 실제로 라벨된 비율)
    crack_count = 0
    delam_count = 0
    pinhole_count = 0
    for record in records:
        crack_count = crack_count + record["label_crack"]
        delam_count = delam_count + record["label_delam"]
        pinhole_count = pinhole_count + record["label_pinhole"]

    total = float(len(records))
    summary["label_crack_rate"] = crack_count / total
    summary["label_delam_rate"] = delam_count / total
    summary["label_pinhole_rate"] = pinhole_count / total

    # 알람 rule 별 카운트
    alert_counts = {}
    for alert in alerts_list:
        rule_name = alert["rule"]
        if rule_name not in alert_counts:
            alert_counts[rule_name] = 0
        alert_counts[rule_name] = alert_counts[rule_name] + 1
    summary["alert_counts"] = alert_counts
    summary["alert_total"] = len(alerts_list)

    return summary


def detect_pinholes(model, stems, image_dir, conf_threshold, batch_size):
    """
    전체 시퀀스 이미지에 YOLO 를 배치로 돌려 검출 결과를 집계한다.
    - 표본 신뢰도 관점: 30장 샘플에서 검출 몇 개 안 나오면 중앙값이 흔들린다.
      전체 시퀀스(수백 장) 를 쓰면 검출 표본이 늘어 통계가 안정된다.
    - CoatingVision 시퀀스는 최대 404장, 배치 32 로 나눠 예측한다.
    """
    aspect_values = []
    area_values = []
    n_detections = 0
    n_scanned = 0

    # 존재하는 파일만 리스트로 만들어 배치 예측
    valid_paths = []
    for stem in stems:
        image_path = os.path.join(image_dir, stem + ".jpg")
        if os.path.exists(image_path) is True:
            valid_paths.append(image_path)

    n_scanned = len(valid_paths)

    start_index = 0
    while start_index < len(valid_paths):
        end_index = start_index + batch_size
        if end_index > len(valid_paths):
            end_index = len(valid_paths)
        batch_paths = valid_paths[start_index:end_index]

        result_list = model.predict(batch_paths, conf=conf_threshold, verbose=False)

        for result in result_list:
            if result.boxes is None:
                continue
            boxes_xywh = result.boxes.xywh.cpu().numpy()
            for row in boxes_xywh:
                width = float(row[2])
                height = float(row[3])
                if width <= 0 or height <= 0:
                    continue

                larger = width
                smaller = height
                if height > width:
                    larger = height
                    smaller = width
                aspect = larger / smaller
                aspect_values.append(aspect)
                area_values.append(width * height)
                n_detections = n_detections + 1

        start_index = end_index

    output = {}
    output["n_images_scanned"] = n_scanned
    output["n_detections"] = n_detections
    if len(aspect_values) > 0:
        output["aspect_median"] = float(np.median(aspect_values))
        output["aspect_p90"] = float(np.percentile(aspect_values, 90))
        output["area_median"] = float(np.median(area_values))
        output["area_max"] = float(np.max(area_values))
        if n_scanned > 0:
            output["detections_per_image"] = n_detections / float(n_scanned)
        else:
            output["detections_per_image"] = 0.0
    else:
        output["aspect_median"] = None
        output["aspect_p90"] = None
        output["area_median"] = None
        output["area_max"] = None
        output["detections_per_image"] = 0.0

    return output


def get_pinhole_reference_by_gap(defect_map, gap):
    """
    defect_map.measured_relations.pinhole_morphology.by_gap 에서 갭별 참조값 조회.
    """
    morphology = defect_map.get("measured_relations", {}).get("pinhole_morphology", {})
    by_gap = morphology.get("by_gap", {})
    gap_key = str(gap)
    if gap_key not in by_gap:
        return None
    return by_gap[gap_key]


# ===============================================================
# 해석 규칙
# ===============================================================

def compute_gap_averages(defect_map):
    """
    defect_map.measured_relations.gap_vs_defect_rate 에서 6개 갭에 걸친 각 클래스 평균.
    상대 특이 판정의 분모로 사용한다.
    가중 평균이 아닌 단순 평균을 쓴다 - 특정 갭 표본이 커서 평균을 지배하는 것을 피하기 위해.
    """
    relations = defect_map["measured_relations"]["gap_vs_defect_rate"]
    crack_values = []
    delam_values = []
    pinhole_values = []
    for gap_key in relations:
        stats = relations[gap_key]
        crack_values.append(stats["crack"])
        delam_values.append(stats["delamination"])
        pinhole_values.append(stats["pinhole"])

    averages = {}
    if len(crack_values) > 0:
        averages["surface_crack"] = float(np.mean(crack_values))
    else:
        averages["surface_crack"] = 0.0
    if len(delam_values) > 0:
        averages["delamination"] = float(np.mean(delam_values))
    else:
        averages["delamination"] = 0.0
    if len(pinhole_values) > 0:
        averages["pinhole"] = float(np.mean(pinhole_values))
    else:
        averages["pinhole"] = 0.0
    return averages


def dominant_and_outliers(sequence_key, defect_map, gap_averages):
    """
    "지배 결함" 대신 두 가지를 병기한다:
      - absolute: 갭 참조값에서 가장 흔한 결함 (최다). 1위/2위 비가 낮으면 '혼재'.
      - outliers: 전체 갭 평균 대비 1.5 배 이상인 결함들 (상대 특이).
    반환 dict:
      {
        'gap_stats': {...},
        'absolute_leader': (key, rate),
        'absolute_second': (key, rate),
        'absolute_ratio': top1/top2,
        'absolute_status': 'majority' | 'mixed',
        'outliers': [(key, rate, average, ratio), ...],  # ratio 내림차순
        'reason_text': '갭 xxx 실측 결함률: ...'
      }
    """
    gap = sequence_key[1]
    relations = defect_map["measured_relations"]["gap_vs_defect_rate"]
    gap_key = str(gap)

    if gap_key not in relations:
        return None

    stats = relations[gap_key]

    class_rates = [
        ("surface_crack", stats["crack"]),
        ("delamination", stats["delamination"]),
        ("pinhole", stats["pinhole"]),
    ]

    # 절대 최다
    sorted_by_rate = sorted(class_rates, key=lambda item: -item[1])
    top1_key = sorted_by_rate[0][0]
    top1_rate = sorted_by_rate[0][1]
    top2_key = sorted_by_rate[1][0]
    top2_rate = sorted_by_rate[1][1]

    if top2_rate > 0.0:
        absolute_ratio = top1_rate / top2_rate
    else:
        absolute_ratio = float("inf")

    if absolute_ratio >= DOMINANT_MAJORITY_RATIO:
        absolute_status = "majority"
    else:
        absolute_status = "mixed"

    # 상대 특이
    outliers = []
    for class_key, rate in class_rates:
        average = gap_averages.get(class_key, 0.0)
        if average <= 0.0:
            continue
        relative = rate / average
        if relative >= RELATIVE_OUTLIER_RATIO:
            outliers.append({
                "class_key": class_key,
                "rate": rate,
                "gap_average": average,
                "relative_ratio": relative,
            })
    outliers.sort(key=lambda item: -item["relative_ratio"])

    reason_text = (
        "갭 " + gap_key + "µm 실측 결함률: 크랙 "
        + format(stats["crack"], ".1f") + "% / 핀홀 "
        + format(stats["pinhole"], ".1f") + "% / 박리 "
        + format(stats["delamination"], ".1f") + "% | 전체 갭 평균: 크랙 "
        + format(gap_averages["surface_crack"], ".1f") + "% / 핀홀 "
        + format(gap_averages["pinhole"], ".1f") + "% / 박리 "
        + format(gap_averages["delamination"], ".1f") + "%"
    )

    return {
        "gap_stats": stats,
        "absolute_leader": (top1_key, top1_rate),
        "absolute_second": (top2_key, top2_rate),
        "absolute_ratio": absolute_ratio,
        "absolute_status": absolute_status,
        "outliers": outliers,
        "reason_text": reason_text,
    }


def class_name_ko(class_key, defect_map):
    class_dict = defect_map["dataset_classes"].get(class_key)
    if class_dict is None:
        return class_key
    return class_dict.get("name_ko", class_key)


def interpret_pinhole_shape(detection, gap, defect_map):
    """
    검출된 핀홀 형태(aspect) 해석.
    표본 크기에 따라 신뢰도 강등/생략:
      - 검출 < 10: 해석 생략 (observation 만)
      - 10 <= 검출 < 20: 신뢰도 low, '표본 부족' 명시
      - 검출 >= 20: 신뢰도 medium
    참조값(defect_map 갭별 참조 aspect)을 함께 표기. 관측값이 참조값과 크게 다르면 별도 언급.
    """
    inferences = []
    n_detections = detection["n_detections"]
    aspect_med = detection["aspect_median"]

    if aspect_med is None:
        return inferences

    if n_detections < PINHOLE_MIN_DETECTIONS_FOR_INFERENCE:
        # 해석 생략. 관측만 리포트에 남긴다.
        return inferences

    if n_detections < PINHOLE_MIN_DETECTIONS_FOR_MEDIUM:
        confidence = "low"
        sample_note = " (표본 부족, n=" + str(n_detections) + ")"
    else:
        confidence = "medium"
        sample_note = " (n=" + str(n_detections) + ")"

    # 참조값 조회
    reference = get_pinhole_reference_by_gap(defect_map, gap)
    reference_text = ""
    reference_aspect = None
    if reference is not None:
        reference_aspect = reference.get("aspect_median")
        if reference_aspect is not None:
            reference_text = (
                " 참조(갭 " + str(gap) + "µm) aspect="
                + format(reference_aspect, ".2f")
            )

    # 규칙 적용
    if aspect_med > PINHOLE_ASPECT_HIGH:
        message = (
            "검출된 핀홀 종횡비 중앙값이 " + format(aspect_med, ".2f")
            + " 로 " + format(PINHOLE_ASPECT_HIGH, ".2f")
            + " 초과. 응집체 걸림 또는 과소 갭 메커니즘 의심"
            + sample_note + "." + reference_text
        )
        inferences.append({
            "confidence": confidence,
            "message": message,
            "basis": "defect_map.pinhole.diagnostic_rule (임계값은 R1 단일 런 데이터 도출)"
        })
    elif aspect_med < 1.10:
        message = (
            "검출된 핀홀 종횡비 중앙값이 " + format(aspect_med, ".2f")
            + " 로 낮음. 둥근 형태이며 기포/젖음성 원인 성향"
            + sample_note + "." + reference_text
        )
        inferences.append({
            "confidence": confidence,
            "message": message,
            "basis": "defect_map.pinhole.diagnostic_rule (임계값은 R1 단일 런 데이터 도출)"
        })

    # 참조값과 관측값의 큰 차이는 별도 언급 (원인 단정 없이 신호로만)
    if reference_aspect is not None:
        if reference_aspect > 0.0:
            deviation_ratio = aspect_med / reference_aspect
            if deviation_ratio >= PINHOLE_ASPECT_DEVIATION_RATIO:
                message = (
                    "관측 aspect(" + format(aspect_med, ".2f")
                    + ") 가 참조(" + format(reference_aspect, ".2f")
                    + ") 대비 " + format(deviation_ratio, ".2f")
                    + "배로 벗어남. 시퀀스별 검출 표본/모델 특성 차이 가능성"
                    + sample_note + "."
                )
                inferences.append({
                    "confidence": "low",
                    "message": message,
                    "basis": "defect_map.measured_relations.pinhole_morphology.by_gap"
                })
            elif deviation_ratio <= 1.0 / PINHOLE_ASPECT_DEVIATION_RATIO:
                message = (
                    "관측 aspect(" + format(aspect_med, ".2f")
                    + ") 가 참조(" + format(reference_aspect, ".2f")
                    + ") 대비 " + format(deviation_ratio, ".2f")
                    + "배로 낮음. 시퀀스별 검출 표본/모델 특성 차이 가능성"
                    + sample_note + "."
                )
                inferences.append({
                    "confidence": "low",
                    "message": message,
                    "basis": "defect_map.measured_relations.pinhole_morphology.by_gap"
                })

    return inferences


def build_incapable_message(sequence_key, spc_summary, defect_map):
    """INCAPABLE 시퀀스에 대한 최우선 해석 메시지."""
    gap = sequence_key[1]
    center_text = format(spc_summary["center"], ".3f")

    relations = defect_map["measured_relations"]["gap_vs_defect_rate"]
    gap_key = str(gap)
    crack_rate = None
    if gap_key in relations:
        crack_rate = relations[gap_key]["crack"]

    lines = []
    lines.append(
        "공정능력이 붕괴되어 있습니다 (center = " + center_text
        + " >= 임계 " + format(CAPABILITY_MARGINAL, ".2f") + ")."
    )
    lines.append(
        "관리도 기반 이상 감지가 무의미하며, 갭 축소 또는 건조 조건 완화가 선행되어야 합니다."
    )
    if crack_rate is not None:
        lines.append(
            "실측 근거: 갭 " + gap_key + "µm 의 크랙 라벨률 "
            + format(crack_rate, ".1f") + "% (defect_map.measured_relations)."
        )
    return " ".join(lines)


def detect_label_deviations(sequence_key, spc_summary, defect_map):
    """
    시퀀스의 라벨률이 갭 참조값 대비 크게 벗어난 경우를 관측으로 보고한다.

    출력은 "편차가 크다"는 사실까지만. 원인은 추정하지 않는다.
    R7/700 이 대표 사례: 같은 갭인 R1/700 과 라벨률이 크게 다르지만,
    R7 은 132장뿐이고 촬영 위치(middle)도 달라 런/위치/표본크기가
    교란되어 있어 개별 원인(용매비 등)을 분리 귀속할 수 없다.

    반환: interpretation dict 리스트 (신뢰도 low 고정, priority 3)
    """
    deviations = []
    n_observations = spc_summary["n_observations"]
    if n_observations < DEVIATION_MIN_SEQUENCE_N:
        return deviations

    gap = sequence_key[1]
    relations = defect_map["measured_relations"]["gap_vs_defect_rate"]
    gap_key = str(gap)
    if gap_key not in relations:
        return deviations
    reference = relations[gap_key]

    class_map = [
        ("crack", "표면 크랙", spc_summary["label_crack_rate"] * 100.0),
        ("delamination", "박리", spc_summary["label_delam_rate"] * 100.0),
        ("pinhole", "핀홀", spc_summary["label_pinhole_rate"] * 100.0),
    ]

    for ref_key, class_name, observed_percent in class_map:
        reference_percent = reference[ref_key]

        # 참조값이 너무 작으면 분모 노이즈 회피
        if reference_percent < DEVIATION_MIN_REFERENCE:
            continue

        ratio = observed_percent / reference_percent

        is_high = False
        is_low = False
        if ratio >= DEVIATION_HIGH_RATIO:
            is_high = True
        if ratio <= DEVIATION_LOW_RATIO:
            is_low = True

        if is_high is False and is_low is False:
            continue

        if is_high:
            direction_text = "높음"
        else:
            direction_text = "낮음"

        message = (
            class_name + " 라벨률이 갭 " + gap_key + "µm 참조값 대비 "
            + direction_text + " ("
            + format(observed_percent, ".1f") + "% 관측 vs "
            + format(reference_percent, ".1f") + "% 참조, "
            + "비 " + format(ratio, ".2f") + "). "
            + "런/촬영위치/표본크기가 교란되어 있어 이 시퀀스만으로 원인을 "
            + "분리 귀속할 수 없음. 추가 실험 없이는 원인 규명 불가."
        )
        basis = (
            "관측 = 이 시퀀스 라벨률 (n="
            + str(n_observations) + "), 참조 = defect_map.measured_relations."
            + "gap_vs_defect_rate (전체 갭 " + gap_key + "µm)"
        )
        deviations.append({
            "priority": 3,
            "confidence": "low",
            "message": message,
            "basis": basis
        })

    return deviations


def build_absolute_leader_message(dom_info, defect_map, spc_summary):
    """
    절대 최다 결함 + 알람 요약. 1위/2위 격차가 1.5 배 미만이면 '혼재' 로 표기.
    '지배' 라는 강한 표현은 사용하지 않는다.
    """
    top1_key, top1_rate = dom_info["absolute_leader"]
    top2_key, top2_rate = dom_info["absolute_second"]
    top1_name = class_name_ko(top1_key, defect_map)
    top2_name = class_name_ko(top2_key, defect_map)

    alert_total = spc_summary["alert_total"]
    top_rules = []
    sorted_rule_names = sorted(spc_summary["alert_counts"].keys(),
                               key=lambda k: -spc_summary["alert_counts"][k])
    for rule_name in sorted_rule_names:
        top_rules.append(rule_name + " " + str(spc_summary["alert_counts"][rule_name]) + "건")
        if len(top_rules) >= 3:
            break

    if len(top_rules) == 0:
        rules_text = "알람 없음"
    else:
        rules_text = ", ".join(top_rules)

    if dom_info["absolute_status"] == "mixed":
        # 격차 1.5 미만 -> 혼재
        message = (
            "결함 유형 혼재: " + top1_name + " " + format(top1_rate, ".1f")
            + "% 와 " + top2_name + " " + format(top2_rate, ".1f")
            + "% (격차 " + format(dom_info["absolute_ratio"], ".2f")
            + "배로 1.5 미만). 알람 총 " + str(alert_total) + "건 (" + rules_text + ")."
        )
    else:
        # 최다
        message = (
            top1_name + " 이(가) 최다 " + format(top1_rate, ".1f") + "% (2위 "
            + top2_name + " " + format(top2_rate, ".1f")
            + "%, 격차 " + format(dom_info["absolute_ratio"], ".2f")
            + "배). 알람 총 " + str(alert_total) + "건 (" + rules_text + ")."
        )
    return message


def build_relative_outlier_message(outlier, defect_map):
    """
    갭 평균 대비 상대 특이 결함 메시지.
    """
    class_key = outlier["class_key"]
    class_name = class_name_ko(class_key, defect_map)
    rate = outlier["rate"]
    average = outlier["gap_average"]
    ratio = outlier["relative_ratio"]

    message = (
        class_name + " " + format(rate, ".1f")
        + "% 는 전체 갭 평균 " + format(average, ".1f") + "% 대비 "
        + format(ratio, ".2f") + "배로 특이. "
        + "이 갭 조건의 실측 특징으로 볼 수 있음."
    )
    return message


def format_cause_line(cause):
    """단일 root_cause 를 한 줄 텍스트로 렌더링."""
    factor = cause.get("factor", "?")
    direction = cause.get("direction", "")
    confidence = cause.get("confidence", "?")
    source = cause.get("source", "?")

    text = "- " + factor
    if len(direction) > 0:
        text = text + " (" + direction + ")"
    text = text + " [confidence=" + confidence + ", src=" + source + "]"
    return text


def format_root_causes_split(class_dict):
    """
    root_causes 를 operational(운전 조정 가능) 과 design(소재 설계) 로 분리해서 렌더링.

    형식:
      * operational (라인 운전 중 조정 가능)
        - 건조 온도 과다 (...)
        - 코팅 두께 과다 (...)
      * design (즉시 조치 불가 — 배합/소재 설계 검토 필요)
        - 활물질 입자 크기 (...)
        ...

    operational 이 하나도 없으면 다음 문구를 명시:
      "현재 라인 조건에서 즉시 조치 가능한 인자 없음. design 인자 검토 필요."

    반환: [
      { 'category': 'operational', 'header': '...', 'lines': [...] },
      { 'category': 'design', 'header': '...', 'lines': [...] },
      # operational 이 없을 때는 별도 note 블록 추가
    ]
    """
    causes = class_dict.get("root_causes", [])

    def cause_priority(cause):
        confidence = cause.get("confidence", "low")
        order = {"high": 0, "medium": 1, "low": 2}
        return order.get(confidence, 3)

    causes_sorted = sorted(causes, key=cause_priority)

    operational_causes = []
    design_causes = []
    uncategorized = []

    for cause in causes_sorted:
        category = cause.get("category")
        if category == "operational":
            operational_causes.append(cause)
        elif category == "design":
            design_causes.append(cause)
        else:
            uncategorized.append(cause)

    blocks = []

    # operational 블록
    if len(operational_causes) > 0:
        lines = []
        for cause in operational_causes:
            lines.append(format_cause_line(cause))
        blocks.append({
            "category": "operational",
            "header": "operational (라인 운전 중 조정 가능)",
            "lines": lines,
        })
    else:
        blocks.append({
            "category": "operational_empty",
            "header": "operational (라인 운전 중 조정 가능)",
            "lines": ["- 해당 없음. 현재 라인 조건에서 즉시 조치 가능한 인자 없음."],
        })

    # design 블록
    if len(design_causes) > 0:
        lines = []
        for cause in design_causes:
            lines.append(format_cause_line(cause))
        blocks.append({
            "category": "design",
            "header": "design (즉시 조치 불가 — 배합/소재 설계 검토 필요)",
            "lines": lines,
        })

    # 미분류 블록
    if len(uncategorized) > 0:
        lines = []
        for cause in uncategorized:
            lines.append(format_cause_line(cause))
        blocks.append({
            "category": "uncategorized",
            "header": "uncategorized (category 미지정)",
            "lines": lines,
        })

    return blocks


def format_check_actions(class_dict):
    actions = class_dict.get("check_actions", [])
    lines = []
    for action in actions:
        lines.append("- " + action)
    return lines


# ===============================================================
# 시퀀스 리포트 조립
# ===============================================================

def analyze_sequence(sequence_key, records, alerts_list, image_stems, model,
                     defect_map, gap_averages):
    """
    한 시퀀스의 3층 리포트를 조립한다.
    """
    spc_summary = summarize_spc(records, alerts_list)

    # 핀홀 검출: 전체 시퀀스 이미지에 대해 실행
    if model is not None and len(image_stems) > 0:
        detection = detect_pinholes(model, image_stems, IMAGE_DIR,
                                    PINHOLE_CONF_THRESHOLD, PINHOLE_BATCH_SIZE)
    else:
        detection = {
            "n_images_scanned": 0, "n_detections": 0,
            "aspect_median": None, "aspect_p90": None,
            "area_median": None, "area_max": None,
            "detections_per_image": 0.0
        }

    # 절대 최다 + 상대 특이 판정
    dom_info = dominant_and_outliers(sequence_key, defect_map, gap_averages)

    # 관심 대상 클래스 (상대 특이가 있으면 그 클래스, 없으면 절대 최다)
    focused_class_key = None
    if dom_info is not None:
        if len(dom_info["outliers"]) > 0:
            focused_class_key = dom_info["outliers"][0]["class_key"]
        else:
            focused_class_key = dom_info["absolute_leader"][0]

    focused_class_dict = None
    if focused_class_key is not None:
        focused_class_dict = defect_map["dataset_classes"].get(focused_class_key)

    # === 해석 조립 ===
    interpretations = []

    # 1. INCAPABLE 우선
    if spc_summary["capability"] == "INCAPABLE":
        message = build_incapable_message(sequence_key, spc_summary, defect_map)
        interpretations.append({
            "priority": 1,
            "confidence": "high",
            "message": message,
            "basis": "defect_map.measured_relations.gap_vs_defect_rate"
        })

    # 2. 절대 최다 / 혼재 (알람과 결합)
    if dom_info is not None and spc_summary["alert_total"] > 0:
        message = build_absolute_leader_message(dom_info, defect_map, spc_summary)
        interpretations.append({
            "priority": 2,
            "confidence": "medium",
            "message": message,
            "basis": dom_info["reason_text"]
        })

    # 2b. 상대 특이 (있으면 우선 해석 대상)
    if dom_info is not None:
        for outlier in dom_info["outliers"]:
            message = build_relative_outlier_message(outlier, defect_map)
            interpretations.append({
                "priority": 2,
                "confidence": "medium",
                "message": message,
                "basis": dom_info["reason_text"]
            })

    # 3. 시퀀스별 라벨률 편차 관측 (원인 단정 금지, low 고정)
    deviation_inferences = detect_label_deviations(sequence_key, spc_summary, defect_map)
    for inference in deviation_inferences:
        interpretations.append(inference)

    # 4. 핀홀 형태 해석 (검출 표본에 따라 신뢰도 강등/생략)
    shape_inferences = interpret_pinhole_shape(detection, sequence_key[1], defect_map)
    for inference in shape_inferences:
        interpretations.append({
            "priority": 4,
            "confidence": inference["confidence"],
            "message": inference["message"],
            "basis": inference["basis"]
        })

    # 5. 갭 값 자체가 MARGINAL 이상이면 도메인 갭 주의 문구
    if spc_summary["capability"] != "GOOD":
        interpretations.append({
            "priority": 5,
            "confidence": "reference",
            "message": (
                "데이터셋 도메인 갭 주의: CoatingVision 은 Vulcan/Nafion/PTFE 조성으로 "
                "연료전지 계열. 결함 형성 물리는 배터리 전극에 공유되나 절대 임계값은 재측정 필요."
            ),
            "basis": "defect_map.domain_gap_note"
        })

    # === 참조 조립 ===
    references = []
    if focused_class_dict is not None:
        class_name = focused_class_dict.get("name_ko", focused_class_key)

        # operational vs design 분리
        cause_blocks = format_root_causes_split(focused_class_dict)
        for block in cause_blocks:
            references.append({
                "topic": class_name + " root_causes | " + block["header"],
                "lines": block["lines"]
            })

        check_text = format_check_actions(focused_class_dict)
        references.append({
            "topic": class_name + " check_actions",
            "lines": check_text
        })

    # 핀홀 갭별 참조 형태 지표
    gap_reference = get_pinhole_reference_by_gap(defect_map, sequence_key[1])
    if gap_reference is not None:
        reference_lines = []
        if "aspect_median" in gap_reference:
            reference_lines.append("- aspect_median: " + format(gap_reference["aspect_median"], ".2f"))
        if "roundness_median" in gap_reference:
            reference_lines.append("- roundness_median: " + format(gap_reference["roundness_median"], ".3f"))
        if "area_median_box" in gap_reference:
            reference_lines.append("- area_median_box: " + format(gap_reference["area_median_box"], ".0f") + "px")
        if "area_max_box" in gap_reference:
            reference_lines.append("- area_max_box: " + format(gap_reference["area_max_box"], ".0f") + "px")
        references.append({
            "topic": "핀홀 갭 " + str(sequence_key[1]) + "µm 참조 형태 (measured_relations)",
            "lines": reference_lines
        })

    # 실측 근거 참조
    gap_key = str(sequence_key[1])
    relations = defect_map["measured_relations"]["gap_vs_defect_rate"]
    if gap_key in relations:
        stats = relations[gap_key]
        references.append({
            "topic": "갭 " + gap_key + "µm 실측 결함률 (CoatingVision 전체)",
            "lines": [
                "- 크랙: " + format(stats["crack"], ".1f") + "%",
                "- 핀홀: " + format(stats["pinhole"], ".1f") + "%",
                "- 박리: " + format(stats["delamination"], ".1f") + "%",
                "- 정상: " + format(stats["clean"], ".1f") + "%",
                "- 표본: " + str(stats["n"]) + "장"
            ]
        })

    return {
        "sequence_key": sequence_key,
        "observation": {
            "spc": spc_summary,
            "detection": detection
        },
        "focused_defect": focused_class_key,
        "dominant_info": dom_info,
        "interpretations": interpretations,
        "references": references
    }


# ===============================================================
# 출력 포맷팅
# ===============================================================

def format_sequence_report(analysis):
    """한 시퀀스의 사람 읽기용 텍스트 리포트."""
    key = analysis["sequence_key"]
    spc = analysis["observation"]["spc"]
    detection = analysis["observation"]["detection"]

    lines = []
    header = "R" + key[0][1:] + " / " + str(key[1]) + "µm / " + key[2]
    lines.append("=" * 82)
    lines.append("[" + header + "]  판정: " + spc["capability"])
    lines.append("=" * 82)
    lines.append("")

    # 관측
    lines.append("[관측]  실제로 측정된 것")
    lines.append("-" * 82)
    lines.append(
        "  SPC 관측 " + str(spc["n_observations"]) + "건"
        + " | center=" + format(spc["center"], ".3f")
        + " | UCL=" + format(spc["ucl"], ".3f")
        + " | defect_ratio max=" + format(spc["defect_ratio_max"], ".3f")
    )
    lines.append(
        "  라벨 기반 결함률: 크랙 "
        + format(spc["label_crack_rate"] * 100, ".1f") + "% / 핀홀 "
        + format(spc["label_pinhole_rate"] * 100, ".1f") + "% / 박리 "
        + format(spc["label_delam_rate"] * 100, ".1f") + "%"
    )
    if spc["alert_total"] > 0:
        alert_texts = []
        for rule_name in sorted(spc["alert_counts"].keys()):
            alert_texts.append(rule_name + "=" + str(spc["alert_counts"][rule_name]))
        lines.append("  알람 " + str(spc["alert_total"]) + "건 (" + ", ".join(alert_texts) + ")")
    else:
        lines.append("  알람 없음")

    if detection["n_images_scanned"] > 0:
        lines.append(
            "  핀홀 검출 (샘플 " + str(detection["n_images_scanned"]) + "장): "
            + str(detection["n_detections"]) + "개 검출"
            + " | per_image=" + format(detection["detections_per_image"], ".2f")
        )
        if detection["aspect_median"] is not None:
            lines.append(
                "    종횡비 중앙값=" + format(detection["aspect_median"], ".2f")
                + " | 면적 중앙값=" + format(detection["area_median"], ".0f") + "px"
                + " | 면적 최대=" + format(detection["area_max"], ".0f") + "px"
            )
    lines.append("")

    # 해석
    lines.append("[해석]  관측으로부터의 추정 (신뢰도 명시)")
    lines.append("-" * 82)
    if len(analysis["interpretations"]) == 0:
        lines.append("  특이사항 없음")
    else:
        for interpretation in analysis["interpretations"]:
            confidence = interpretation["confidence"]
            message = interpretation["message"]
            basis = interpretation["basis"]
            lines.append("  [confidence=" + confidence + "] " + message)
            lines.append("    근거: " + basis)
            lines.append("")

    # 참조
    lines.append("[참조]  defect_map 지배 인자 및 점검 항목")
    lines.append("-" * 82)
    if len(analysis["references"]) == 0:
        lines.append("  참조 항목 없음")
    else:
        for reference in analysis["references"]:
            lines.append("  * " + reference["topic"])
            for line_text in reference["lines"]:
                lines.append("    " + line_text)
            lines.append("")

    return "\n".join(lines)


def format_global_header(defect_map):
    """전체 리포트 상단 헤더 - 데이터셋/도메인 갭 명시."""
    lines = []
    lines.append("#" * 82)
    lines.append("# 전극 코팅 결함 인라인 검사 - Advisor 리포트")
    lines.append("#" * 82)
    lines.append("")
    lines.append("데이터셋: CoatingVision (Sampath et al. Sci Data 2026)")

    gap_note = defect_map.get("domain_gap_note", {})
    dataset_comp = gap_note.get("dataset_composition", "?")
    target_comp = gap_note.get("target_composition", "?")
    lines.append("데이터 조성: " + dataset_comp)
    lines.append("타깃 조성: " + target_comp)
    lines.append("")
    lines.append("도메인 갭:")
    lines.append("  전이 가능: " + gap_note.get("transferable", "?"))
    lines.append("  전이 불가: " + gap_note.get("not_transferable", "?"))
    lines.append("")
    verify = defect_map.get("verification_status", "?")
    lines.append("지식베이스 검증 상태: " + verify)
    lines.append("")
    return "\n".join(lines)


def format_global_summary(analyses):
    """전체 시퀀스 판정 요약 표."""
    lines = []
    lines.append("=" * 82)
    lines.append("전체 시퀀스 판정 요약")
    lines.append("=" * 82)
    lines.append("")
    header = ("  " + "시퀀스".ljust(38) + "center    UCL       판정          알람")
    lines.append(header)
    lines.append("  " + "-" * 78)
    for analysis in analyses:
        key = analysis["sequence_key"]
        spc = analysis["observation"]["spc"]
        key_text = key[0] + " / " + str(key[1]) + "µm / " + key[2]
        line = ("  " + key_text.ljust(38)
                + format(spc["center"], ".3f").ljust(10)
                + format(spc["ucl"], ".3f").ljust(10)
                + spc["capability"].ljust(14)
                + str(spc["alert_total"]))
        lines.append(line)
    lines.append("")
    lines.append(
        "  주석: 알람 수가 적은 것은 공정 안정을 뜻하지 않는다."
    )
    lines.append(
        "        INCAPABLE 조건은 관리한계가 넓어져(R1/900 UCL=0.997)"
    )
    lines.append(
        "        검출력을 상실한 상태다."
    )
    lines.append("")
    return "\n".join(lines)


def serialize_analysis(analysis):
    """JSON 저장용으로 시퀀스 키를 리스트로 변환."""
    key = analysis["sequence_key"]
    # dominant_info 에 float('inf') 가 있을 수 있어 JSON 직렬화 방어
    dom_info = analysis.get("dominant_info")
    if dom_info is not None:
        cleaned = {}
        for name, value in dom_info.items():
            if name == "absolute_ratio":
                if value == float("inf"):
                    cleaned[name] = None
                else:
                    cleaned[name] = value
            else:
                cleaned[name] = value
        dom_info = cleaned

    serializable = {
        "run_id": key[0],
        "coating_gap": key[1],
        "position": key[2],
        "observation": analysis["observation"],
        "focused_defect": analysis["focused_defect"],
        "dominant_info": dom_info,
        "interpretations": analysis["interpretations"],
        "references": analysis["references"]
    }
    return serializable


# ===============================================================
# 실행
# ===============================================================

def load_model_if_available():
    """
    YOLO 모델을 로드한다. ultralytics 가 없거나 가중치가 없으면 None.
    """
    if os.path.exists(MODEL_PATH) is False:
        print("경고: 핀홀 모델 가중치를 찾지 못했습니다 (" + MODEL_PATH + ").")
        print("      SPC + defect_map 기반 리포트만 생성합니다.")
        return None
    try:
        from ultralytics import YOLO
    except ImportError:
        print("경고: ultralytics 미설치. 핀홀 검출 관측 없이 진행합니다.")
        return None
    model = YOLO(MODEL_PATH)
    return model


def main():
    if os.path.exists(SPC_RESULT_PATH) is False:
        print("results_spc.csv 를 찾을 수 없습니다.")
        return
    if os.path.exists(DEFECT_MAP_PATH) is False:
        print("defect_map.json 을 찾을 수 없습니다.")
        return
    if os.path.exists(LABELS_PATH) is False:
        print("labels.csv 를 찾을 수 없습니다.")
        return

    print("데이터 로딩 중...")
    sequences = load_spc_records(SPC_RESULT_PATH)
    alerts_by_sequence = load_alerts(SPC_ALERT_PATH)
    defect_map = load_defect_map(DEFECT_MAP_PATH)
    stems_by_sequence = load_sequence_image_stems()

    # 전체 갭 평균 - 상대 특이 판정의 분모
    gap_averages = compute_gap_averages(defect_map)
    print("갭 평균 결함률 (전체 6개 갭): 크랙 "
          + format(gap_averages["surface_crack"], ".1f") + "% / 핀홀 "
          + format(gap_averages["pinhole"], ".1f") + "% / 박리 "
          + format(gap_averages["delamination"], ".1f") + "%")

    print("핀홀 모델 로드 중...")
    model = load_model_if_available()
    if model is not None:
        print("  모델: " + MODEL_PATH)
        print("  전체 시퀀스 이미지 추론, batch=" + str(PINHOLE_BATCH_SIZE)
              + ", conf=" + str(PINHOLE_CONF_THRESHOLD))

    # 시퀀스 정렬 (갭 오름차순)
    sequence_keys = list(sequences.keys())

    def sort_key(item):
        return (item[1], item[0], item[2])

    sequence_keys.sort(key=sort_key)

    analyses = []
    for key in sequence_keys:
        run_id = key[0]
        gap = key[1]
        position = key[2]
        print()
        print("분석: " + run_id + " / " + str(gap) + "µm / " + position)

        records = sequences[key]
        if key in alerts_by_sequence:
            alerts_list = alerts_by_sequence[key]
        else:
            alerts_list = []

        if key in stems_by_sequence:
            stems = stems_by_sequence[key]
        else:
            stems = []

        import time
        start_time = time.time()
        analysis = analyze_sequence(key, records, alerts_list, stems, model,
                                    defect_map, gap_averages)
        elapsed = time.time() - start_time
        analyses.append(analysis)

        spc = analysis["observation"]["spc"]
        detection = analysis["observation"]["detection"]
        print(
            "  판정: " + spc["capability"]
            + " | 알람: " + str(spc["alert_total"])
            + " | 핀홀 검출: " + str(detection["n_detections"])
            + " (전체 " + str(detection["n_images_scanned"]) + "장, "
            + format(elapsed, ".1f") + "s)"
        )
        sys.stdout.flush()

    # 텍스트 리포트
    print()
    print("리포트 작성 중...")
    text_parts = []
    text_parts.append(format_global_header(defect_map))
    text_parts.append(format_global_summary(analyses))
    for analysis in analyses:
        text_parts.append(format_sequence_report(analysis))
        text_parts.append("")

    output_file = open(REPORT_TEXT_PATH, "w", encoding="utf-8")
    output_file.write("\n".join(text_parts))
    output_file.close()
    print("  저장: " + REPORT_TEXT_PATH)

    # JSON 리포트
    json_output = {
        "schema_version": "0.1",
        "domain_gap_note": defect_map.get("domain_gap_note"),
        "verification_status": defect_map.get("verification_status"),
        "sequences": []
    }
    for analysis in analyses:
        json_output["sequences"].append(serialize_analysis(analysis))

    output_file = open(REPORT_JSON_PATH, "w", encoding="utf-8")
    json.dump(json_output, output_file, ensure_ascii=False, indent=2)
    output_file.close()
    print("  저장: " + REPORT_JSON_PATH)

    print()
    print("완료.")


main()
