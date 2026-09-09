"""
02. 정상 패치 추출 검증 + 갭별 핀홀 형태 분석

목적 1: 완전 정상 이미지가 44장(2%)뿐이므로 이미지 단위 비지도 학습이 불가능하다.
        마스크에서 결함 없는 영역을 패치로 잘라내 정상 학습셋을 만들 수 있는지 검증한다.
        핵심 요구사항은 코팅 갭 조건별로 균등하게 확보되는지 여부다.

목적 2: 코팅 갭과 핀홀의 관계는 U자형으로 알려져 있다.
        - 갭이 좁으면: 고전단 응력으로 압축된 기포가 통과 직후 팽창/파열 -> 작고 많은 미세 핀홀
        - 갭이 넓으면: 비드 불안정과 공기 포집 -> 크고 불규칙한 핀홀, 디웨팅 구역
        갭별 핀홀의 개수, 면적, roundness 분포를 측정해 어느 메커니즘이 지배적인지 판별한다.
        roundness = 4*pi*A / p^2  (1에 가까울수록 원형)

마스크 채널: 0=Surface Crack, 1=Delamination, 2=Pinhole, 3=Unclassified
마스크 값: 0 또는 255
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
# 메타데이터 파싱 (01 스크립트와 동일)
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
    """image_1.jpg -> 메타데이터 매핑을 만든다."""
    metadata = {}

    csv_file = open(csv_path, "r", encoding="utf-8")
    reader = csv.DictReader(csv_file)
    for row in reader:
        parsed = parse_original_filename(row["original_file_name"])
        parsed["surface_crack"] = int(row["Surface_Crack"])
        parsed["delamination"] = int(row["Delamination"])
        parsed["pinhole"] = int(row["Pinhole"])
        parsed["unclassified"] = int(row["unclassified"])

        # image_1.jpg -> image_1
        stem = row["file_name"]
        if stem.endswith(".jpg"):
            stem = stem[:-4]
        metadata[stem] = parsed
    csv_file.close()

    return metadata


# ---------------------------------------------------------------
# 정상 패치 추출 검증
# ---------------------------------------------------------------

def build_defect_map_2d(mask_array):
    """
    4채널 마스크를 하나의 2D 결함 맵으로 합친다.
    어떤 채널이든 결함이 있으면 True.
    """
    height = mask_array.shape[0]
    width = mask_array.shape[1]
    num_channels = mask_array.shape[2]

    combined = np.zeros((height, width), dtype=bool)
    for channel_index in range(num_channels):
        channel = mask_array[:, :, channel_index]
        channel_binary = channel > 127
        combined = np.logical_or(combined, channel_binary)

    return combined


def dilate_defect_map(defect_map, margin_pixels):
    """
    결함 경계 인근 픽셀도 오염으로 간주하기 위해 결함 영역을 확장한다.
    cv2가 없으면 numpy 시프트로 근사한다.
    """
    if margin_pixels <= 0:
        return defect_map

    if HAS_CV2 is True:
        kernel_size = margin_pixels * 2 + 1
        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        dilated = cv2.dilate(defect_map.astype(np.uint8), kernel, iterations=1)
        return dilated > 0

    # cv2 미설치 시 대체: 상하좌우 시프트 누적
    dilated = defect_map.copy()
    for shift in range(1, margin_pixels + 1):
        dilated[shift:, :] = np.logical_or(dilated[shift:, :], defect_map[:-shift, :])
        dilated[:-shift, :] = np.logical_or(dilated[:-shift, :], defect_map[shift:, :])
        dilated[:, shift:] = np.logical_or(dilated[:, shift:], defect_map[:, :-shift])
        dilated[:, :-shift] = np.logical_or(dilated[:, :-shift], defect_map[:, shift:])
    return dilated


def count_clean_patches(defect_map, patch_size, stride):
    """
    격자 방식으로 패치를 훑어 결함이 전혀 없는 패치 수를 센다.
    반환: (정상 패치 수, 전체 패치 수)
    """
    height = defect_map.shape[0]
    width = defect_map.shape[1]

    clean_count = 0
    total_count = 0

    y = 0
    while y + patch_size <= height:
        x = 0
        while x + patch_size <= width:
            patch = defect_map[y:y + patch_size, x:x + patch_size]
            total_count = total_count + 1
            if np.any(patch) == False:
                clean_count = clean_count + 1
            x = x + stride
        y = y + stride

    return clean_count, total_count


def run_patch_analysis(mask_paths, metadata, patch_sizes, margin_pixels, sample_limit):
    """
    패치 크기별 / 코팅 갭별 정상 패치 수확량을 집계한다.
    """
    print("=" * 70)
    print("정상 패치 추출 검증")
    print("margin(결함 경계 여유):", margin_pixels, "px")
    if HAS_CV2 is False:
        print("(cv2 미설치 - numpy 시프트로 팽창 근사)")
    print("=" * 70)
    print()

    for patch_size in patch_sizes:
        stride = patch_size  # 겹치지 않는 격자

        gap_clean = {}
        gap_total = {}
        gap_images = {}

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
            defect_map = build_defect_map_2d(mask_array)
            defect_map = dilate_defect_map(defect_map, margin_pixels)

            clean_count, total_count = count_clean_patches(defect_map, patch_size, stride)

            if gap not in gap_clean:
                gap_clean[gap] = 0
                gap_total[gap] = 0
                gap_images[gap] = 0
            gap_clean[gap] = gap_clean[gap] + clean_count
            gap_total[gap] = gap_total[gap] + total_count
            gap_images[gap] = gap_images[gap] + 1

            processed = processed + 1

        print("--- patch_size =", patch_size, "px (stride =", stride, ") ---")
        print("gap(um)  images  clean_patches  total_patches  clean_ratio")

        overall_clean = 0
        overall_total = 0
        gap_keys = sorted(gap_clean.keys())
        for gap in gap_keys:
            clean = gap_clean[gap]
            total = gap_total[gap]
            ratio = 0.0
            if total > 0:
                ratio = round(clean / total * 100, 1)

            line = str(gap).ljust(9)
            line = line + str(gap_images[gap]).ljust(8)
            line = line + str(clean).ljust(15)
            line = line + str(total).ljust(15)
            line = line + str(ratio) + "%"
            print(line)

            overall_clean = overall_clean + clean
            overall_total = overall_total + total

        print("합계:", overall_clean, "/", overall_total, "패치")

        # 균등 샘플링 가능량 = 최소 갭의 수확량 * 갭 수
        if len(gap_keys) > 0:
            min_clean = min(gap_clean[g] for g in gap_keys)
            balanced_total = min_clean * len(gap_keys)
            print("갭별 균등 샘플링 시 확보 가능:", balanced_total,
                  "패치 (최소 갭 기준", min_clean, "x", len(gap_keys), "조건)")
        print()


# ---------------------------------------------------------------
# 갭별 핀홀 형태 분석
# ---------------------------------------------------------------

def analyze_pinhole_shapes(mask_paths, metadata, sample_limit):
    """
    핀홀 채널(2)에서 연결 요소를 찾아 개수, 면적, roundness를 측정한다.
    갭이 좁을 때와 넓을 때의 핀홀 형성 메커니즘이 다르다는 가설을 검증한다.
    """
    print("=" * 70)
    print("갭별 핀홀 형태 분석")
    print("가설: 좁은 갭 -> 고전단/기포팽창 -> 작고 많은 미세 핀홀")
    print("      넓은 갭 -> 비드 불안정/공기포집 -> 크고 불규칙한 핀홀")
    print("=" * 70)
    print()

    if HAS_CV2 is False:
        print("cv2가 없어 연결 요소 분석을 건너뜁니다.")
        print("pip install opencv-python-headless --break-system-packages")
        print()
        return

    gap_areas = {}
    gap_roundness = {}
    gap_counts_per_image = {}

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
        pinhole_channel = mask_array[:, :, 2]
        pinhole_binary = (pinhole_channel > 127).astype(np.uint8)

        if np.sum(pinhole_binary) == 0:
            processed = processed + 1
            continue

        contours, _ = cv2.findContours(
            pinhole_binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        if gap not in gap_areas:
            gap_areas[gap] = []
            gap_roundness[gap] = []
            gap_counts_per_image[gap] = []

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

        gap_counts_per_image[gap].append(valid_count)
        processed = processed + 1

    print("gap(um)  n_pinholes  per_image  area_median  area_mean  roundness_median")
    gap_keys = sorted(gap_areas.keys())
    for gap in gap_keys:
        areas = np.array(gap_areas[gap])
        roundness_values = np.array(gap_roundness[gap])
        counts = np.array(gap_counts_per_image[gap])

        if len(areas) == 0:
            continue

        line = str(gap).ljust(9)
        line = line + str(len(areas)).ljust(12)
        line = line + str(round(float(np.mean(counts)), 1)).ljust(11)
        line = line + str(round(float(np.median(areas)), 1)).ljust(13)
        line = line + str(round(float(np.mean(areas)), 1)).ljust(11)
        line = line + str(round(float(np.median(roundness_values)), 3))
        print(line)
    print()

    print("해석 가이드:")
    print("  area_median 이 작고 per_image 가 크면 -> 미세 핀홀 다발 (고전단 메커니즘)")
    print("  area_median 이 크고 roundness 가 낮으면 -> 불규칙 대형 핀홀 (비드 불안정)")
    print()


# ---------------------------------------------------------------

def main():
    csv_path = "classification/labels.csv"
    mask_dir = "segmentation/masks"

    if os.path.exists(csv_path) is False:
        print("labels.csv 를 찾을 수 없습니다. 데이터셋 루트에서 실행하세요.")
        print("현재 위치:", os.getcwd())
        return

    metadata = load_metadata(csv_path)
    mask_paths = sorted(glob.glob(mask_dir + "/*.png"))

    print("메타데이터:", len(metadata), "건")
    print("마스크:", len(mask_paths), "장")
    print()

    # 전체를 돌리면 오래 걸리므로 우선 600장으로 확인한다.
    # 결과가 타당하면 sample_limit 을 None 으로 바꿔 전체 실행한다.
    sample_limit = 600

    patch_sizes = [32, 64, 128]
    margin_pixels = 4

    run_patch_analysis(mask_paths, metadata, patch_sizes, margin_pixels, sample_limit)
    analyze_pinhole_shapes(mask_paths, metadata, sample_limit)


main()
