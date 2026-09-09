"""
03. 핀홀 형태 분석 보강

배경:
  02 스크립트에서 갭별 핀홀 형태를 분석했으나 판정 불가였다.
  - 600장 샘플에서 갭별 핀홀이 9~40개뿐이라 표본이 부족
  - per_image 가 모든 갭에서 0.9~1.1 로 나옴. 이미지당 핀홀이 정확히 1개꼴이라는 것은
    물리적으로 부자연스럽고, 세그멘테이션 마스크에서 인접 핀홀들이 하나의 연결 요소로
    병합되었을 가능성을 시사
  - area_mean 이 area_median 의 2~4배 (700um: 126 vs 306). 소수 거대 영역이 평균을 왜곡

접근:
  A) 세그멘테이션 마스크를 전체 2227장으로 재분석하여 표본 확대
  B) 검출 라벨(YOLO txt, 581개)로 핀홀 개별 인스턴스를 분석
     검출 라벨은 인스턴스별 바운딩 박스이므로 마스크 병합 문제가 없다.
     A와 B의 per_image 가 크게 다르면 마스크 병합 가설이 확인된다.

검증 대상 가설:
  좁은 갭 -> 고전단/기포팽창 -> 작고 많은 미세 핀홀
  넓은 갭 -> 비드 불안정/공기포집 -> 크고 불규칙한 핀홀
"""

import csv
import re
import os
import glob
import numpy as np
from PIL import Image

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


# ---------------------------------------------------------------
# 메타데이터
# ---------------------------------------------------------------

def parse_original_filename(original_name):
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


def load_metadata(csv_path):
    metadata = {}
    csv_file = open(csv_path, "r", encoding="utf-8")
    reader = csv.DictReader(csv_file)
    for row in reader:
        parsed = parse_original_filename(row["original_file_name"])
        parsed["surface_crack"] = int(row["Surface_Crack"])
        parsed["delamination"] = int(row["Delamination"])
        parsed["pinhole"] = int(row["Pinhole"])
        parsed["unclassified"] = int(row["unclassified"])

        stem = row["file_name"]
        if stem.endswith(".jpg"):
            stem = stem[:-4]
        metadata[stem] = parsed
    csv_file.close()
    return metadata


# ---------------------------------------------------------------
# 통계 헬퍼
# ---------------------------------------------------------------

def describe_values(values):
    """리스트의 요약 통계를 dict 로 반환한다."""
    array = np.array(values, dtype=float)
    result = {}
    result["n"] = len(array)
    if len(array) == 0:
        return result
    result["median"] = float(np.median(array))
    result["mean"] = float(np.mean(array))
    result["p25"] = float(np.percentile(array, 25))
    result["p75"] = float(np.percentile(array, 75))
    result["max"] = float(np.max(array))
    return result


# ---------------------------------------------------------------
# B) 검출 라벨 구조 확인
# ---------------------------------------------------------------

def inspect_detection_labels(label_dir):
    """
    YOLO txt 라벨의 실제 구조를 확인한다.
    class_id 종류, 파일당 박스 수 분포를 본다.
    """
    print("=" * 72)
    print("B-0. 검출 라벨 구조 확인")
    print("=" * 72)

    label_paths = sorted(glob.glob(label_dir + "/*.txt"))
    print("라벨 파일 수:", len(label_paths))

    if len(label_paths) == 0:
        print("라벨을 찾을 수 없습니다.")
        print()
        return None

    class_id_counts = {}
    boxes_per_file = []
    empty_files = 0
    malformed_lines = 0

    for path in label_paths:
        text_file = open(path, "r")
        lines = text_file.readlines()
        text_file.close()

        box_count = 0
        for line in lines:
            stripped = line.strip()
            if len(stripped) == 0:
                continue
            parts = stripped.split()
            if len(parts) < 5:
                malformed_lines = malformed_lines + 1
                continue

            class_id = parts[0]
            if class_id not in class_id_counts:
                class_id_counts[class_id] = 0
            class_id_counts[class_id] = class_id_counts[class_id] + 1
            box_count = box_count + 1

        if box_count == 0:
            empty_files = empty_files + 1
        boxes_per_file.append(box_count)

    print("빈 파일:", empty_files)
    print("형식 오류 라인:", malformed_lines)
    print()
    print("class_id 분포:")
    for class_id in sorted(class_id_counts.keys()):
        print("  class", class_id, ":", class_id_counts[class_id], "박스")
    print()

    stats = describe_values(boxes_per_file)
    print("파일당 박스 수: median", round(stats["median"], 1),
          "| mean", round(stats["mean"], 2),
          "| max", int(stats["max"]))
    print()

    # 샘플 3개 출력
    print("샘플 라벨 내용:")
    for i in range(min(3, len(label_paths))):
        path = label_paths[i]
        text_file = open(path, "r")
        content = text_file.read().strip()
        text_file.close()
        print(" ", os.path.basename(path))
        for line in content.split("\n")[:4]:
            print("   ", line)
    print()

    return label_paths


def analyze_detection_boxes(label_paths, metadata, target_class_id):
    """
    검출 라벨의 바운딩 박스로 갭별 핀홀 통계를 낸다.
    좌표는 정규화되어 있으므로 480x640 기준 픽셀로 환산한다.
    """
    print("=" * 72)
    print("B. 검출 라벨 기반 핀홀 인스턴스 분석 (class_id =", target_class_id, ")")
    print("=" * 72)

    image_height = 480
    image_width = 640

    gap_areas = {}
    gap_aspect = {}
    gap_counts = {}

    for path in label_paths:
        stem = os.path.basename(path)
        if stem.endswith(".txt"):
            stem = stem[:-4]
        if stem not in metadata:
            continue

        gap = metadata[stem]["coating_gap"]
        if gap is None:
            continue

        text_file = open(path, "r")
        lines = text_file.readlines()
        text_file.close()

        if gap not in gap_areas:
            gap_areas[gap] = []
            gap_aspect[gap] = []
            gap_counts[gap] = []

        box_count = 0
        for line in lines:
            stripped = line.strip()
            if len(stripped) == 0:
                continue
            parts = stripped.split()
            if len(parts) < 5:
                continue
            if parts[0] != target_class_id:
                continue

            width_norm = float(parts[3])
            height_norm = float(parts[4])

            box_width = width_norm * image_width
            box_height = height_norm * image_height
            box_area = box_width * box_height

            if box_area <= 0:
                continue

            longer = max(box_width, box_height)
            shorter = min(box_width, box_height)
            aspect_ratio = 1.0
            if shorter > 0:
                aspect_ratio = longer / shorter

            gap_areas[gap].append(box_area)
            gap_aspect[gap].append(aspect_ratio)
            box_count = box_count + 1

        gap_counts[gap].append(box_count)

    print("gap(um)  images  boxes  per_image  area_median  area_p75  area_max  aspect_median")
    gap_keys = sorted(gap_areas.keys())
    for gap in gap_keys:
        areas = gap_areas[gap]
        if len(areas) == 0:
            continue

        area_stats = describe_values(areas)
        count_stats = describe_values(gap_counts[gap])
        aspect_stats = describe_values(gap_aspect[gap])

        line = str(gap).ljust(9)
        line = line + str(len(gap_counts[gap])).ljust(8)
        line = line + str(len(areas)).ljust(7)
        line = line + str(round(count_stats["mean"], 2)).ljust(11)
        line = line + str(round(area_stats["median"], 1)).ljust(13)
        line = line + str(round(area_stats["p75"], 1)).ljust(10)
        line = line + str(round(area_stats["max"], 1)).ljust(10)
        line = line + str(round(aspect_stats["median"], 2))
        print(line)
    print()

    return gap_areas, gap_counts


# ---------------------------------------------------------------
# A) 세그멘테이션 마스크 전체 재분석
# ---------------------------------------------------------------

def analyze_mask_pinholes(mask_paths, metadata, sample_limit):
    """
    핀홀 채널(2)의 연결 요소를 전체 데이터에서 분석한다.
    """
    print("=" * 72)
    print("A. 세그멘테이션 마스크 기반 핀홀 분석 (전체)")
    print("=" * 72)

    if HAS_CV2 is False:
        print("cv2 미설치. pip install opencv-python-headless --break-system-packages")
        print()
        return

    gap_areas = {}
    gap_roundness = {}
    gap_counts = {}

    processed = 0
    for path in mask_paths:
        if sample_limit is not None and processed >= sample_limit:
            break

        stem = os.path.basename(path)
        if stem.endswith(".png"):
            stem = stem[:-4]
        if stem not in metadata:
            continue

        gap = metadata[stem]["coating_gap"]
        if gap is None:
            continue

        mask_array = np.array(Image.open(path))
        pinhole_binary = (mask_array[:, :, 2] > 127).astype(np.uint8)

        processed = processed + 1

        if np.sum(pinhole_binary) == 0:
            continue

        contours, _ = cv2.findContours(
            pinhole_binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        if gap not in gap_areas:
            gap_areas[gap] = []
            gap_roundness[gap] = []
            gap_counts[gap] = []

        valid_count = 0
        for contour in contours:
            area = cv2.contourArea(contour)
            perimeter = cv2.arcLength(contour, True)
            if area < 4:
                continue
            if perimeter <= 0:
                continue
            roundness = 4.0 * np.pi * area / (perimeter * perimeter)
            gap_areas[gap].append(area)
            gap_roundness[gap].append(roundness)
            valid_count = valid_count + 1

        gap_counts[gap].append(valid_count)

        if processed % 500 == 0:
            print("  처리 중:", processed, "장")

    print("처리 완료:", processed, "장")
    print()

    print("gap(um)  imgs_w_pin  regions  per_image  area_median  area_p75  area_max  round_median")
    gap_keys = sorted(gap_areas.keys())
    for gap in gap_keys:
        areas = gap_areas[gap]
        if len(areas) == 0:
            continue

        area_stats = describe_values(areas)
        count_stats = describe_values(gap_counts[gap])
        round_stats = describe_values(gap_roundness[gap])

        line = str(gap).ljust(9)
        line = line + str(len(gap_counts[gap])).ljust(12)
        line = line + str(len(areas)).ljust(9)
        line = line + str(round(count_stats["mean"], 2)).ljust(11)
        line = line + str(round(area_stats["median"], 1)).ljust(13)
        line = line + str(round(area_stats["p75"], 1)).ljust(10)
        line = line + str(round(area_stats["max"], 1)).ljust(10)
        line = line + str(round(round_stats["median"], 3))
        print(line)
    print()

    return gap_areas, gap_counts


# ---------------------------------------------------------------

def main():
    csv_path = "classification/labels.csv"
    mask_dir = "segmentation/masks"
    label_dir = "detection/labels"

    if os.path.exists(csv_path) is False:
        print("labels.csv 를 찾을 수 없습니다. 데이터셋 루트에서 실행하세요.")
        print("현재 위치:", os.getcwd())
        return

    metadata = load_metadata(csv_path)
    mask_paths = sorted(glob.glob(mask_dir + "/*.png"))

    print("메타데이터:", len(metadata), "건 / 마스크:", len(mask_paths), "장")
    print()

    # B 먼저 (빠름)
    label_paths = inspect_detection_labels(label_dir)
    if label_paths is not None:
        analyze_detection_boxes(label_paths, metadata, "0")

    # A (느림, 전체 2227장)
    analyze_mask_pinholes(mask_paths, metadata, None)

    print("=" * 72)
    print("판정 기준")
    print("=" * 72)
    print("1. A와 B의 per_image 가 크게 다르면 -> 마스크에서 인접 핀홀이 병합된 것")
    print("   (B가 더 크면 검출 라벨이 개별 인스턴스를 제대로 분리한 것)")
    print("2. B 기준으로 갭이 작을수록 per_image 가 크고 area_median 이 작으면")
    print("   -> 고전단/기포팽창 메커니즘 확인")
    print("3. 갭이 클수록 area_max 가 크고 aspect_median 이 1에서 멀면")
    print("   -> 비드 불안정/디웨팅 확인")
    print("4. 어느 방향도 뚜렷하지 않으면 가설 보류하고 로그에 기록 후 진행")


main()
