"""
05. 박리(delamination) 패치 검증

문제:
  박리 패치가 밝고 흐릿하게 나타난다. 두 가지 해석이 가능하다.
  (A) 진짜 박리 - 코팅이 들뜨면서 초점면을 벗어나고 하부 PTFE 기재(흰색)가 비쳐 밝아짐
  (B) 촬영 아티팩트 - 단순히 초점이 나간 프레임을 저자들이 박리로 라벨링

  둘을 구분하지 못하면 "박리 검출"이 실제로는 "초점 이상 검출"이 되어
  포트폴리오의 주장이 무너진다.

접근:
  1) 시각 검증 - 원본 이미지 전체와 마스크 오버레이를 나란히 저장해 육안 확인
  2) 정량 검증 - 클래스별 밝기/초점 지표 비교

정량 지표:
  - mean_brightness : 평균 밝기. 기재가 비치면 상승
  - laplacian_var   : 라플라시안 분산. 초점이 나가면 고주파가 사라져 급감
  - local_contrast  : 국소 표준편차. 텍스처 유무

판정:
  (A)라면 박리 영역은 밝고 라플라시안 분산이 낮지만,
     같은 이미지의 정상 영역은 초점이 맞아 라플라시안 분산이 정상 수준을 유지한다.
     즉 이미지 내부에서 국소적으로만 흐릿하다.
  (B)라면 이미지 전체가 흐릿하므로 정상 영역도 라플라시안 분산이 낮다.

사용법:
  python3 05_verify_delamination.py
"""

import csv
import re
import os
import glob
import random
import numpy as np
from PIL import Image

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


OUTPUT_DIR = "check_delamination"
NUM_VISUAL_SAMPLES = 6


# ===============================================================
# 메타데이터
# ===============================================================

def parse_original_filename(original_name):
    result = {}
    result["run_id"] = None
    result["coating_gap"] = None
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

    return result


def load_metadata(csv_path):
    metadata = {}
    csv_file = open(csv_path, "r", encoding="utf-8")
    reader = csv.DictReader(csv_file)
    for row in reader:
        parsed = parse_original_filename(row["original_file_name"])
        parsed["original_name"] = row["original_file_name"]
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


# ===============================================================
# 지표 계산
# ===============================================================

def compute_laplacian_variance(gray_array):
    """
    라플라시안 분산. 초점이 맞으면 에지가 살아 있어 값이 크고,
    흐려지면 급감한다. 초점 판정의 표준적 지표다.
    """
    if HAS_CV2 is True:
        laplacian = cv2.Laplacian(gray_array, cv2.CV_64F)
        return float(np.var(laplacian))

    # cv2 없을 때: 3x3 라플라시안 커널을 직접 적용
    kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)
    height = gray_array.shape[0]
    width = gray_array.shape[1]
    output = np.zeros((height - 2, width - 2), dtype=np.float64)

    row = 0
    while row < height - 2:
        column = 0
        while column < width - 2:
            window = gray_array[row:row + 3, column:column + 3].astype(np.float64)
            output[row, column] = np.sum(window * kernel)
            column = column + 1
        row = row + 1

    return float(np.var(output))


def compute_patch_metrics(patch_array):
    """패치 하나의 밝기/초점/대비 지표를 계산한다."""
    if len(patch_array.shape) == 3:
        gray = np.mean(patch_array, axis=2).astype(np.uint8)
    else:
        gray = patch_array

    metrics = {}
    metrics["mean_brightness"] = float(np.mean(gray))
    metrics["max_brightness"] = float(np.max(gray))
    metrics["local_contrast"] = float(np.std(gray))
    metrics["laplacian_var"] = compute_laplacian_variance(gray)
    return metrics


def summarize(values):
    array = np.array(values, dtype=float)
    result = {}
    result["n"] = len(array)
    if len(array) == 0:
        return result
    result["median"] = float(np.median(array))
    result["mean"] = float(np.mean(array))
    result["p10"] = float(np.percentile(array, 10))
    result["p90"] = float(np.percentile(array, 90))
    return result


# ===============================================================
# 1) 클래스별 지표 비교
# ===============================================================

def compare_class_metrics(patch_root):
    """
    저장된 패치들의 클래스별 지표를 비교한다.
    박리가 정말 밝고 흐린지, 다른 클래스와 어떻게 다른지 본다.
    """
    print("=" * 78)
    print("1. 클래스별 밝기 / 초점 지표 비교")
    print("=" * 78)

    folders = {
        "normal": patch_root + "/train/normal",
        "surface_crack": patch_root + "/eval/surface_crack",
        "delamination": patch_root + "/eval/delamination",
        "pinhole": patch_root + "/eval/pinhole",
    }

    sample_size = 400
    random_generator = random.Random(42)

    results = {}

    for class_name in ["normal", "surface_crack", "delamination", "pinhole"]:
        folder = folders[class_name]
        paths = sorted(glob.glob(folder + "/*.png"))
        if len(paths) == 0:
            print(class_name, ": 패치 없음 -", folder)
            continue

        random_generator.shuffle(paths)
        take = min(sample_size, len(paths))

        brightness_values = []
        laplacian_values = []
        contrast_values = []
        max_values = []

        index = 0
        while index < take:
            patch_array = np.array(Image.open(paths[index]).convert("RGB"))
            metrics = compute_patch_metrics(patch_array)
            brightness_values.append(metrics["mean_brightness"])
            laplacian_values.append(metrics["laplacian_var"])
            contrast_values.append(metrics["local_contrast"])
            max_values.append(metrics["max_brightness"])
            index = index + 1

        results[class_name] = {
            "brightness": summarize(brightness_values),
            "laplacian": summarize(laplacian_values),
            "contrast": summarize(contrast_values),
            "max": summarize(max_values),
        }

    print("class".ljust(16) + "n".ljust(7) + "brightness".ljust(24)
          + "max_bright".ljust(12) + "contrast".ljust(12) + "laplacian_var")
    print("".ljust(16) + "".ljust(7) + "median (p10~p90)".ljust(24)
          + "median".ljust(12) + "median".ljust(12) + "median (p10~p90)")

    for class_name in ["normal", "surface_crack", "delamination", "pinhole"]:
        if class_name not in results:
            continue
        record = results[class_name]

        bright_text = (str(round(record["brightness"]["median"], 1)) + " ("
                       + str(round(record["brightness"]["p10"], 1)) + "~"
                       + str(round(record["brightness"]["p90"], 1)) + ")")
        lap_text = (str(round(record["laplacian"]["median"], 1)) + " ("
                    + str(round(record["laplacian"]["p10"], 1)) + "~"
                    + str(round(record["laplacian"]["p90"], 1)) + ")")

        line = class_name.ljust(16)
        line = line + str(record["brightness"]["n"]).ljust(7)
        line = line + bright_text.ljust(24)
        line = line + str(round(record["max"]["median"], 1)).ljust(12)
        line = line + str(round(record["contrast"]["median"], 1)).ljust(12)
        line = line + lap_text
        print(line)
    print()

    return results


# ===============================================================
# 2) 박리 이미지 내부의 국소성 검증
# ===============================================================

def check_local_vs_global_blur(metadata, image_dir, mask_dir):
    """
    박리 라벨이 붙은 이미지에서
      - 박리 영역
      - 같은 이미지의 정상 영역
    의 라플라시안 분산을 비교한다.

    (A) 진짜 박리라면: 박리 영역만 흐리고 정상 영역은 선명 -> 차이가 크다
    (B) 초점 아티팩트라면: 이미지 전체가 흐리다 -> 차이가 작다

    비교군으로 박리가 없는 이미지의 정상 영역도 측정한다.
    """
    print("=" * 78)
    print("2. 박리 영역 vs 같은 이미지 정상 영역의 초점 비교")
    print("=" * 78)
    print("(A) 진짜 박리 -> 국소적으로만 흐림 -> 두 값의 차이가 큼")
    print("(B) 초점 문제 -> 전체가 흐림 -> 두 값이 비슷함")
    print()

    delam_region_values = []
    same_image_normal_values = []
    other_image_normal_values = []

    processed_delam = 0
    processed_other = 0
    limit = 150

    stems = sorted(metadata.keys())
    for stem in stems:
        has_delamination = metadata[stem]["delamination"] == 1

        if has_delamination is True and processed_delam >= limit:
            continue
        if has_delamination is False and processed_other >= limit:
            continue
        if processed_delam >= limit and processed_other >= limit:
            break

        image_path = os.path.join(image_dir, stem + ".jpg")
        mask_path = os.path.join(mask_dir, stem + ".png")
        if os.path.exists(image_path) is False:
            continue
        if os.path.exists(mask_path) is False:
            continue

        image_array = np.array(Image.open(image_path).convert("RGB"))
        gray = np.mean(image_array, axis=2).astype(np.uint8)

        mask_array = np.array(Image.open(mask_path))
        delam_mask = mask_array[:, :, 1] > 127

        any_defect = np.zeros(delam_mask.shape, dtype=bool)
        channel_index = 0
        while channel_index < 4:
            any_defect = np.logical_or(any_defect, mask_array[:, :, channel_index] > 127)
            channel_index = channel_index + 1

        if has_delamination is False:
            # 박리 없는 이미지: 정상 영역만 측정
            clean_value = measure_region_laplacian(gray, np.logical_not(any_defect))
            if clean_value is not None:
                other_image_normal_values.append(clean_value)
                processed_other = processed_other + 1
            continue

        # 박리 있는 이미지: 박리 영역과 정상 영역을 각각 측정
        delam_value = measure_region_laplacian(gray, delam_mask)
        clean_value = measure_region_laplacian(gray, np.logical_not(any_defect))

        if delam_value is not None and clean_value is not None:
            delam_region_values.append(delam_value)
            same_image_normal_values.append(clean_value)
            processed_delam = processed_delam + 1

    print("측정 대상".ljust(34) + "n".ljust(7) + "laplacian_var median (p10~p90)")

    groups = [
        ("박리 영역", delam_region_values),
        ("같은 이미지의 정상 영역", same_image_normal_values),
        ("박리 없는 이미지의 정상 영역", other_image_normal_values),
    ]

    for label, values in groups:
        stats = summarize(values)
        if stats["n"] == 0:
            print(label.ljust(34) + "0")
            continue
        text = (str(round(stats["median"], 1)) + " ("
                + str(round(stats["p10"], 1)) + "~"
                + str(round(stats["p90"], 1)) + ")")
        print(label.ljust(34) + str(stats["n"]).ljust(7) + text)
    print()

    # 판정 보조
    if len(delam_region_values) > 0 and len(same_image_normal_values) > 0:
        delam_median = float(np.median(delam_region_values))
        same_median = float(np.median(same_image_normal_values))
        ratio = 0.0
        if same_median > 0:
            ratio = delam_median / same_median
        print("박리영역 / 같은이미지 정상영역 비율:", round(ratio, 3))
        print("  0.5 미만 -> 국소적 흐림이 뚜렷 -> (A) 진짜 박리에 가까움")
        print("  0.8 이상 -> 차이 미미 -> (B) 초점 아티팩트 가능성")
        print()

    if len(same_image_normal_values) > 0 and len(other_image_normal_values) > 0:
        same_median = float(np.median(same_image_normal_values))
        other_median = float(np.median(other_image_normal_values))
        ratio2 = 0.0
        if other_median > 0:
            ratio2 = same_median / other_median
        print("박리이미지 정상영역 / 일반이미지 정상영역 비율:", round(ratio2, 3))
        print("  1에 가까우면 -> 박리 이미지도 전반적 초점은 정상")
        print("  크게 낮으면  -> 박리 이미지 자체가 전반적으로 흐림 (B 근거)")
        print()


def measure_region_laplacian(gray_array, region_mask):
    """
    지정 영역의 라플라시안 분산을 구한다.
    영역이 너무 작으면 None 을 반환한다.
    """
    pixel_count = int(np.sum(region_mask))
    if pixel_count < 500:
        return None

    if HAS_CV2 is True:
        laplacian = cv2.Laplacian(gray_array, cv2.CV_64F)
    else:
        laplacian = np.zeros(gray_array.shape, dtype=np.float64)
        laplacian[1:-1, 1:-1] = (
            gray_array[:-2, 1:-1].astype(np.float64)
            + gray_array[2:, 1:-1].astype(np.float64)
            + gray_array[1:-1, :-2].astype(np.float64)
            + gray_array[1:-1, 2:].astype(np.float64)
            - 4.0 * gray_array[1:-1, 1:-1].astype(np.float64)
        )

    values = laplacian[region_mask]
    return float(np.var(values))


# ===============================================================
# 3) 시각 검증용 이미지 저장
# ===============================================================

def save_visual_samples(metadata, image_dir, mask_dir, output_dir, num_samples):
    """
    박리 라벨 이미지를 원본 크기로, 마스크 오버레이와 나란히 저장한다.
    좌: 원본 / 우: 박리 영역을 빨강으로 표시
    """
    print("=" * 78)
    print("3. 시각 검증용 이미지 저장")
    print("=" * 78)

    if os.path.exists(output_dir) is False:
        os.makedirs(output_dir)

    candidates = []
    for stem in sorted(metadata.keys()):
        if metadata[stem]["delamination"] == 1:
            candidates.append(stem)

    random_generator = random.Random(7)
    random_generator.shuffle(candidates)

    saved = 0
    index = 0
    while saved < num_samples and index < len(candidates):
        stem = candidates[index]
        index = index + 1

        image_path = os.path.join(image_dir, stem + ".jpg")
        mask_path = os.path.join(mask_dir, stem + ".png")
        if os.path.exists(image_path) is False:
            continue
        if os.path.exists(mask_path) is False:
            continue

        image_array = np.array(Image.open(image_path).convert("RGB"))
        mask_array = np.array(Image.open(mask_path))
        delam_mask = mask_array[:, :, 1] > 127

        overlay = image_array.copy()
        overlay[delam_mask, 0] = 255
        overlay[delam_mask, 1] = 0
        overlay[delam_mask, 2] = 0

        height = image_array.shape[0]
        width = image_array.shape[1]
        combined = np.zeros((height, width * 2, 3), dtype=np.uint8)
        combined[:, :width, :] = image_array
        combined[:, width:, :] = overlay

        gap = metadata[stem]["coating_gap"]
        out_name = "delam_" + str(gap) + "um_" + stem + ".png"
        out_path = os.path.join(output_dir, out_name)
        Image.fromarray(combined).save(out_path)

        saved = saved + 1

    print("저장:", saved, "장 ->", output_dir)
    print("좌측=원본 / 우측=박리 영역 빨강 표시")
    print()

    # 비교용: 박리 없는 정상 이미지도 몇 장
    clean_candidates = []
    for stem in sorted(metadata.keys()):
        record = metadata[stem]
        total = (record["surface_crack"] + record["delamination"]
                 + record["pinhole"] + record["unclassified"])
        if total == 0:
            clean_candidates.append(stem)

    random_generator.shuffle(clean_candidates)
    saved_clean = 0
    index = 0
    while saved_clean < 3 and index < len(clean_candidates):
        stem = clean_candidates[index]
        index = index + 1
        image_path = os.path.join(image_dir, stem + ".jpg")
        if os.path.exists(image_path) is False:
            continue
        gap = metadata[stem]["coating_gap"]
        out_path = os.path.join(output_dir, "clean_" + str(gap) + "um_" + stem + ".png")
        Image.open(image_path).save(out_path)
        saved_clean = saved_clean + 1

    print("비교용 정상 이미지:", saved_clean, "장")
    print()


# ===============================================================

def main():
    csv_path = "classification/labels.csv"
    image_dir = "segmentation/images"
    mask_dir = "segmentation/masks"
    patch_root = "data/patches"

    if os.path.exists(csv_path) is False:
        print("labels.csv 를 찾을 수 없습니다. 데이터셋 루트에서 실행하세요.")
        return

    if HAS_CV2 is False:
        print("경고: cv2 없음. 라플라시안 계산이 느립니다.")
        print()

    metadata = load_metadata(csv_path)

    compare_class_metrics(patch_root)
    check_local_vs_global_blur(metadata, image_dir, mask_dir)
    save_visual_samples(metadata, image_dir, mask_dir, OUTPUT_DIR, NUM_VISUAL_SAMPLES)

    print("=" * 78)
    print("다음: check_delamination/ 폴더의 이미지를 원본 크기로 확인")
    print("=" * 78)


main()
