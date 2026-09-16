"""
09. SPC 레이어 - 공정 관리도 및 이상 감지

목적:
  [1] 이상 탐지는 "이 패치가 이상하다"까지만 말한다.
  실제 품질관리는 개별 결함이 아니라 **결함 발생률의 추이**를 본다.
  "지금 이 프레임에 결함이 있다"가 아니라
  "최근 구간에서 크랙 검출률이 유의하게 증가했다"가 현장에서 필요한 출력이다.

설계상 중요한 결정 세 가지:

1) 시계열의 단위
   패치가 아니라 **프레임**이 시계열의 단위다.
   한 프레임(480x640)에서 여러 패치가 나오므로, 패치 이상점수를 프레임 단위로 집계한다.
   집계는 평균이 아니라 **이상 패치 비율**을 쓴다.
   평균은 정상 패치가 많으면 희석되지만, 비율은 결함의 존재를 직접 반영한다.

2) 불균등 시계열
   02 분석에서 확인했듯 프레임 번호에 결번이 많다.
   (예: R1|600 은 프레임 0~659 범위에 306장뿐 - 저자의 해시맵 중복 제거 때문)
   따라서 "최근 N개 샘플" 이동창을 쓰되, x축은 프레임 번호로 표시한다.
   등간격을 가정하는 시간 기반 창은 쓸 수 없다.

3) 관리한계의 설정
   정상 공정 구간에서 관리한계를 정하고, 이후 구간에 적용한다.
   이 데이터에는 "정상 생산 구간"이 따로 없으므로,
   **결함률이 가장 낮은 조건(600um)의 초반부**를 기준 구간으로 삼는다.

감지 규칙:
  - 3시그마 이탈 (Shewhart)
  - EWMA (지수가중이동평균) - 작은 지속적 변화에 민감
  - Western Electric 규칙 일부
      규칙2: 연속 9점이 중심선 한쪽
      규칙3: 연속 6점이 단조 증가 또는 감소

사용법:
  python3 09_spc_monitor.py
  python3 09_spc_monitor.py --method deep     # 마할라노비스 사용 (torch 필요)
"""

import csv
import os
import sys
import glob
import numpy as np
from PIL import Image


PATCH_ROOT = "data/patches"
MANIFEST_PATH = "data/patches/manifest.csv"
LABELS_PATH = "classification/labels.csv"
IMAGE_DIR = "segmentation/images"
MASK_DIR = "segmentation/masks"

RESULT_PATH = "results_spc.csv"
ALERT_PATH = "results_spc_alerts.csv"

PATCH_SIZE = 64
STRIDE = 64          # SPC 에서는 비중첩으로 훑는다 (프레임당 70패치)

WINDOW_SIZE = 15     # 이동창 크기 (프레임 수)
EWMA_LAMBDA = 0.2    # EWMA 가중치
SIGMA_LIMIT = 3.0


# ===============================================================
# 메타데이터
# ===============================================================

def parse_original_filename(original_name):
    import re
    result = {}
    result["run_id"] = None
    result["coating_gap"] = None
    result["position"] = None
    result["frame_number"] = None

    name = original_name
    if name.endswith(".png"):
        name = name[:-4]

    frame_match = re.search(r"_frame_(\d+)", name)
    if frame_match is not None:
        result["frame_number"] = int(frame_match.group(1))

    condition_part = name
    frame_split = name.split("_frame_")
    if len(frame_split) > 1:
        condition_part = frame_split[0]

    run_match = re.match(r"^(R\d+)", condition_part)
    if run_match is not None:
        result["run_id"] = run_match.group(1)

    gap_match = re.search(r"(\d+)um", condition_part)
    if gap_match is not None:
        result["coating_gap"] = int(gap_match.group(1))

    position_text = condition_part
    if result["run_id"] is not None:
        position_text = position_text.replace(result["run_id"], "", 1)
    if result["coating_gap"] is not None:
        position_text = position_text.replace(str(result["coating_gap"]) + "um", "", 1)
    position_text = position_text.strip("-").strip("_")
    if len(position_text) > 0:
        result["position"] = position_text

    return result


def load_frame_metadata(csv_path):
    """image_N -> 런/갭/위치/프레임번호 및 라벨"""
    metadata = {}
    csv_file = open(csv_path, "r", encoding="utf-8")
    reader = csv.DictReader(csv_file)
    for row in reader:
        parsed = parse_original_filename(row["original_file_name"])
        parsed["surface_crack"] = int(row["Surface_Crack"])
        parsed["delamination"] = int(row["Delamination"])
        parsed["pinhole"] = int(row["Pinhole"])

        stem = row["file_name"]
        if stem.endswith(".jpg"):
            stem = stem[:-4]
        metadata[stem] = parsed
    csv_file.close()
    return metadata


def group_into_sequences(metadata):
    """
    런 x 갭 x 위치 로 묶어 하나의 연속 촬영 시퀀스를 만든다.
    각 시퀀스 안에서 프레임 번호로 정렬한다.
    """
    sequences = {}
    for stem in metadata:
        record = metadata[stem]
        if record["frame_number"] is None:
            continue
        key = (record["run_id"], record["coating_gap"], record["position"])
        if key not in sequences:
            sequences[key] = []
        sequences[key].append((record["frame_number"], stem))

    for key in sequences:
        sequences[key].sort()

    return sequences


# ===============================================================
# 프레임 단위 이상 점수
# ===============================================================

def score_patches_std(image_array):
    """
    한 프레임을 격자로 훑어 패치별 밝기 표준편차를 계산한다.
    06 벤치마크에서 A3(표준편차)가 AUROC 0.983 으로 가장 단순하면서 효과적이었다.
    """
    gray = np.mean(image_array.astype(np.float64), axis=2)
    height = gray.shape[0]
    width = gray.shape[1]

    scores = []
    y = 0
    while y + PATCH_SIZE <= height:
        x = 0
        while x + PATCH_SIZE <= width:
            patch = gray[y:y + PATCH_SIZE, x:x + PATCH_SIZE]
            scores.append(float(np.std(patch)))
            x = x + STRIDE
        y = y + STRIDE

    return np.array(scores)


def aggregate_frame(patch_scores, patch_threshold):
    """
    패치 점수를 프레임 단위 지표로 집계한다.

    이상 패치 비율을 주 지표로 쓴다.
    평균 점수는 정상 패치가 많으면 희석되지만,
    비율은 "프레임의 몇 %가 이상한가"를 직접 나타낸다.
    """
    result = {}
    result["n_patches"] = len(patch_scores)
    if len(patch_scores) == 0:
        result["defect_ratio"] = 0.0
        result["mean_score"] = 0.0
        result["max_score"] = 0.0
        return result

    anomalous = int(np.sum(patch_scores >= patch_threshold))
    result["defect_ratio"] = anomalous / float(len(patch_scores))
    result["mean_score"] = float(np.mean(patch_scores))
    result["max_score"] = float(np.max(patch_scores))
    return result


# ===============================================================
# 관리도
# ===============================================================

def compute_control_limits(baseline_values, sigma_limit):
    """
    기준 구간에서 중심선과 관리한계를 계산한다.
    비율 데이터이므로 하한은 0 미만으로 내려가지 않게 자른다.
    """
    array = np.array(baseline_values, dtype=float)
    center = float(np.mean(array))
    deviation = float(np.std(array))

    upper = center + sigma_limit * deviation
    lower = center - sigma_limit * deviation
    if lower < 0.0:
        lower = 0.0

    result = {}
    result["center"] = center
    result["sigma"] = deviation
    result["upper"] = upper
    result["lower"] = lower
    return result


def compute_moving_average(values, window_size):
    """이동평균. 앞부분은 가능한 만큼만 사용한다."""
    output = []
    index = 0
    while index < len(values):
        start = index - window_size + 1
        if start < 0:
            start = 0
        window = values[start:index + 1]
        output.append(float(np.mean(window)))
        index = index + 1
    return output


def compute_ewma(values, lambda_value, initial_value):
    """
    지수가중이동평균.
    Shewhart 관리도는 큰 급변에 강하지만 작은 지속적 변화를 놓친다.
    EWMA 는 과거를 지수적으로 누적하므로 완만한 드리프트를 잡는다.

    초기값은 기준 구간의 중심선을 쓴다.
    첫 관측값으로 초기화하면 초기 관리한계가 매우 좁은 구간에서
    그 값 자체가 이탈로 판정되어 거짓 알람이 발생한다.
    """
    output = []
    previous = float(initial_value)
    index = 0
    while index < len(values):
        current = lambda_value * float(values[index]) + (1.0 - lambda_value) * previous
        output.append(current)
        previous = current
        index = index + 1
    return output


def compute_ewma_limits(center, sigma, lambda_value, sigma_limit, length):
    """EWMA 의 관리한계는 시점마다 다르다 (초기에 좁고 점차 넓어짐)."""
    upper_list = []
    lower_list = []

    index = 0
    while index < length:
        step = index + 1
        factor = lambda_value / (2.0 - lambda_value)
        factor = factor * (1.0 - (1.0 - lambda_value) ** (2 * step))
        width = sigma_limit * sigma * np.sqrt(factor)

        upper_list.append(center + width)
        lower_value = center - width
        if lower_value < 0.0:
            lower_value = 0.0
        lower_list.append(lower_value)
        index = index + 1

    return upper_list, lower_list


# ===============================================================
# 감지 규칙
# ===============================================================

def detect_sigma_violation(values, limits):
    """규칙1: 관리한계 이탈"""
    alerts = []
    index = 0
    while index < len(values):
        if values[index] > limits["upper"]:
            alerts.append((index, "rule1_upper", values[index]))
        elif values[index] < limits["lower"]:
            alerts.append((index, "rule1_lower", values[index]))
        index = index + 1
    return alerts


def detect_run_of_nine(values, center):
    """규칙2: 연속 9점이 중심선 한쪽에 위치 - 평균 이동을 시사"""
    alerts = []
    run_length = 0
    run_side = 0

    index = 0
    while index < len(values):
        side = 0
        if values[index] > center:
            side = 1
        elif values[index] < center:
            side = -1

        if side != 0 and side == run_side:
            run_length = run_length + 1
        else:
            run_side = side
            run_length = 1

        if run_length >= 9:
            alerts.append((index, "rule2_run9", values[index]))
            run_length = 0
            run_side = 0

        index = index + 1
    return alerts


def detect_trend_of_six(values):
    """규칙3: 연속 6점 단조 증가/감소 - 공정 드리프트를 시사"""
    alerts = []
    increasing = 0
    decreasing = 0

    index = 1
    while index < len(values):
        if values[index] > values[index - 1]:
            increasing = increasing + 1
            decreasing = 0
        elif values[index] < values[index - 1]:
            decreasing = decreasing + 1
            increasing = 0
        else:
            increasing = 0
            decreasing = 0

        if increasing >= 6:
            alerts.append((index, "rule3_trend_up", values[index]))
            increasing = 0
        if decreasing >= 6:
            alerts.append((index, "rule3_trend_down", values[index]))
            decreasing = 0

        index = index + 1
    return alerts


def detect_ewma_violation(ewma_values, upper_list, lower_list):
    alerts = []
    index = 0
    while index < len(ewma_values):
        if ewma_values[index] > upper_list[index]:
            alerts.append((index, "ewma_upper", ewma_values[index]))
        elif ewma_values[index] < lower_list[index]:
            alerts.append((index, "ewma_lower", ewma_values[index]))
        index = index + 1
    return alerts


# ===============================================================
# 실행
# ===============================================================

def determine_patch_threshold(metadata, image_dir):
    """
    패치 이상 판정 임계값을 정한다.
    결함 라벨이 전혀 없는 프레임들의 패치 점수 분포에서 95 퍼센타일을 쓴다.
    """
    clean_stems = []
    for stem in metadata:
        record = metadata[stem]
        total = record["surface_crack"] + record["delamination"] + record["pinhole"]
        if total == 0:
            clean_stems.append(stem)

    clean_stems.sort()

    all_scores = []
    count = 0
    for stem in clean_stems:
        path = os.path.join(image_dir, stem + ".jpg")
        if os.path.exists(path) is False:
            continue
        image_array = np.array(Image.open(path).convert("RGB"))
        scores = score_patches_std(image_array)
        index = 0
        while index < len(scores):
            all_scores.append(scores[index])
            index = index + 1
        count = count + 1

    if len(all_scores) == 0:
        return 3.0, 0

    threshold = float(np.percentile(np.array(all_scores), 95))
    return threshold, count


def process_sequence(sequence_key, frame_list, metadata, image_dir, patch_threshold):
    """한 시퀀스의 프레임별 지표를 계산한다."""
    records = []

    for frame_number, stem in frame_list:
        path = os.path.join(image_dir, stem + ".jpg")
        if os.path.exists(path) is False:
            continue

        image_array = np.array(Image.open(path).convert("RGB"))
        patch_scores = score_patches_std(image_array)
        aggregated = aggregate_frame(patch_scores, patch_threshold)

        record = {}
        record["frame_number"] = frame_number
        record["stem"] = stem
        record["defect_ratio"] = aggregated["defect_ratio"]
        record["mean_score"] = aggregated["mean_score"]
        record["max_score"] = aggregated["max_score"]
        record["label_crack"] = metadata[stem]["surface_crack"]
        record["label_delam"] = metadata[stem]["delamination"]
        record["label_pinhole"] = metadata[stem]["pinhole"]
        records.append(record)

    return records


def analyze_sequence(sequence_key, records, baseline_fraction):
    """
    한 시퀀스에 관리도를 적용하고 알람을 뽑는다.
    앞 baseline_fraction 비율을 기준 구간으로 사용한다.
    """
    values = []
    for record in records:
        values.append(record["defect_ratio"])

    if len(values) < 30:
        return None

    baseline_count = int(len(values) * baseline_fraction)
    if baseline_count < 20:
        baseline_count = 20
    baseline_values = values[:baseline_count]

    limits = compute_control_limits(baseline_values, SIGMA_LIMIT)

    moving_average = compute_moving_average(values, WINDOW_SIZE)
    ewma_values = compute_ewma(values, EWMA_LAMBDA, limits["center"])
    ewma_upper, ewma_lower = compute_ewma_limits(
        limits["center"], limits["sigma"], EWMA_LAMBDA, SIGMA_LIMIT, len(values)
    )

    alerts = []
    for alert in detect_sigma_violation(values, limits):
        alerts.append(alert)
    for alert in detect_run_of_nine(values, limits["center"]):
        alerts.append(alert)
    for alert in detect_trend_of_six(moving_average):
        alerts.append(alert)
    for alert in detect_ewma_violation(ewma_values, ewma_upper, ewma_lower):
        alerts.append(alert)

    result = {}
    result["key"] = sequence_key
    result["records"] = records
    result["values"] = values
    result["moving_average"] = moving_average
    result["ewma"] = ewma_values
    result["limits"] = limits
    result["baseline_count"] = baseline_count
    result["alerts"] = alerts
    return result


def report_sequence(analysis):
    key = analysis["key"]
    limits = analysis["limits"]
    records = analysis["records"]
    values = analysis["values"]
    alerts = analysis["alerts"]

    key_text = str(key[0]) + " / " + str(key[1]) + "um / " + str(key[2])
    print("--- " + key_text + " ---")
    print("  프레임:", len(records),
          "| 기준구간:", analysis["baseline_count"], "프레임")
    print("  중심선:", round(limits["center"], 4),
          "| sigma:", round(limits["sigma"], 4),
          "| UCL:", round(limits["upper"], 4),
          "| LCL:", round(limits["lower"], 4))

    # 알람 종류별 집계
    alert_counts = {}
    for alert in alerts:
        rule_name = alert[1]
        if rule_name not in alert_counts:
            alert_counts[rule_name] = 0
        alert_counts[rule_name] = alert_counts[rule_name] + 1

    if len(alert_counts) == 0:
        print("  알람 없음")
    else:
        text = "  알람: "
        for rule_name in sorted(alert_counts.keys()):
            text = text + rule_name + "=" + str(alert_counts[rule_name]) + "  "
        print(text)

    # 알람 시점의 실제 라벨과 대조 - 알람이 실제 결함 증가와 일치하는가
    alert_indices = set()
    for alert in alerts:
        alert_indices.add(alert[0])

    if len(alert_indices) > 0:
        alert_crack = 0
        normal_crack = 0
        alert_total = 0
        normal_total = 0

        index = 0
        while index < len(records):
            if index in alert_indices:
                alert_crack = alert_crack + records[index]["label_crack"]
                alert_total = alert_total + 1
            else:
                normal_crack = normal_crack + records[index]["label_crack"]
                normal_total = normal_total + 1
            index = index + 1

        alert_rate = 0.0
        if alert_total > 0:
            alert_rate = alert_crack / float(alert_total)
        normal_rate = 0.0
        if normal_total > 0:
            normal_rate = normal_crack / float(normal_total)

        print("  알람 시점 크랙 라벨 비율:", round(alert_rate, 3),
              "| 비알람 시점:", round(normal_rate, 3))

    print("  결함비율 범위:", round(float(np.min(values)), 4),
          "~", round(float(np.max(values)), 4))
    print()


def main():
    if os.path.exists(LABELS_PATH) is False:
        print("labels.csv 를 찾을 수 없습니다. 데이터셋 루트에서 실행하세요.")
        return

    print("=" * 82)
    print("SPC 레이어 - 공정 관리도 및 이상 감지")
    print("=" * 82)
    print()

    metadata = load_frame_metadata(LABELS_PATH)
    sequences = group_into_sequences(metadata)

    print("시퀀스:", len(sequences), "개")
    print()

    print("패치 이상 임계값 결정 중 (결함 라벨 없는 프레임 기준)...")
    patch_threshold, clean_count = determine_patch_threshold(metadata, IMAGE_DIR)
    print("  정상 프레임:", clean_count, "장")
    print("  패치 임계값 (95 퍼센타일):", round(patch_threshold, 4))
    print()

    print("=" * 82)
    print("시퀀스별 관리도")
    print("=" * 82)
    print()

    all_records = []
    all_alerts = []

    sequence_keys = sorted(sequences.keys(), key=lambda item: (str(item[0]), item[1], str(item[2])))

    for key in sequence_keys:
        frame_list = sequences[key]
        records = process_sequence(key, frame_list, metadata, IMAGE_DIR, patch_threshold)
        analysis = analyze_sequence(key, records, 0.3)

        if analysis is None:
            continue

        report_sequence(analysis)

        index = 0
        while index < len(analysis["records"]):
            record = analysis["records"][index]
            row = {}
            row["stem"] = record["stem"]
            row["run_id"] = key[0]
            row["coating_gap"] = key[1]
            row["position"] = key[2]
            row["frame_number"] = record["frame_number"]
            row["defect_ratio"] = round(record["defect_ratio"], 4)
            row["moving_average"] = round(analysis["moving_average"][index], 4)
            row["ewma"] = round(analysis["ewma"][index], 4)
            row["ucl"] = round(analysis["limits"]["upper"], 4)
            row["center"] = round(analysis["limits"]["center"], 4)
            row["label_crack"] = record["label_crack"]
            row["label_delam"] = record["label_delam"]
            row["label_pinhole"] = record["label_pinhole"]
            all_records.append(row)
            index = index + 1

        for alert in analysis["alerts"]:
            alert_index = alert[0]
            alert_row = {}
            alert_row["run_id"] = key[0]
            alert_row["coating_gap"] = key[1]
            alert_row["position"] = key[2]
            alert_row["frame_number"] = analysis["records"][alert_index]["frame_number"]
            alert_row["rule"] = alert[1]
            alert_row["value"] = round(alert[2], 4)
            all_alerts.append(alert_row)

    write_csv(RESULT_PATH,
              ["stem", "run_id", "coating_gap", "position", "frame_number", "defect_ratio",
               "moving_average", "ewma", "ucl", "center",
               "label_crack", "label_delam", "label_pinhole"],
              all_records)

    write_csv(ALERT_PATH,
              ["run_id", "coating_gap", "position", "frame_number", "rule", "value"],
              all_alerts)

    print("=" * 82)
    print("저장:", RESULT_PATH, "(", len(all_records), "행 )")
    print("저장:", ALERT_PATH, "(", len(all_alerts), "건 )")
    print("=" * 82)


def write_csv(path, field_names, rows):
    if len(rows) == 0:
        return
    output_file = open(path, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(output_file, fieldnames=field_names)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    output_file.close()


main()
