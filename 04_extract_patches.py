"""
04. 정상/결함 패치 추출

확정 사양 (03 검증 결과):
  - 패치 크기 64px, stride 32px(50% 중첩), margin 4px
  - 정상 패치: 코팅 갭별 균등 샘플링
  - 결함 패치: 클래스별로 확보 (평가용)

설계상 중요한 두 가지:

1) 누수 방지
   같은 이미지에서 나온 패치들은 서로 매우 유사하다. train/test 에 나눠 담으면
   성능이 부풀려진다. 따라서 이미지 단위로 먼저 분할한 뒤 각 분할 안에서 패치를 뽑는다.
   분할은 코팅 갭 조건별로 층화(stratified)한다.

2) 결함 패치 판정 기준
   패치에 결함이 1픽셀만 걸쳐도 결함으로 치면 애매한 샘플이 대량 발생한다.
   패치 면적의 min_defect_ratio 이상을 차지할 때만 확실한 결함 패치로 인정한다.
   그 사이의 애매한 패치(0 초과 ~ 기준 미만)는 정상에도 결함에도 넣지 않고 버린다.

출력:
  data/patches/train/normal/*.png
  data/patches/val/normal/*.png
  data/patches/test/normal/*.png
  data/patches/test/surface_crack/*.png
  data/patches/test/delamination/*.png
  data/patches/test/pinhole/*.png
  data/patches/manifest.csv

사용법:
  python3 04_extract_patches.py           # dry-run, 수확량만 보고
  python3 04_extract_patches.py --write   # 실제 파일 저장
"""

import csv
import re
import os
import sys
import glob
import random
import numpy as np
from PIL import Image

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


# ===============================================================
# 설정
# ===============================================================

PATCH_SIZE = 64
STRIDE = 32
MARGIN_PIXELS = 4

# 결함 패치로 인정할 최소 결함 픽셀 수 (클래스별)
#
# 클래스마다 결함의 물리적 크기가 다르므로 단일 비율 기준을 쓰면 안 된다.
# 03 분석에서 측정한 핀홀 면적 중앙값은 갭에 따라 21~66px 이었다.
# 64x64=4096px 패치에 2% 비율(81px) 기준을 적용하면 핀홀 대부분이 탈락한다.
#
# 크랙과 박리는 면적이 큰 결함이므로 비율 기준이 적절하고,
# 핀홀은 절대 픽셀 수로 낮게 잡는다.
MIN_DEFECT_PIXELS = {
    "surface_crack": 82,    # 패치 면적의 약 2%
    "delamination": 82,     # 패치 면적의 약 2%
    "pinhole": 15,          # 실측 중앙값 하한(21px)보다 약간 아래
}

# 갭 조건별 정상 패치 목표 수 (train 기준)
# dry-run 결과 최소 갭(1000um)에서 15,441개 확보 가능하므로 여유롭게 잡는다.
# PatchCore 계열은 메모리 뱅크가 클수록 정상 분포를 촘촘히 덮어 유리하다.
NORMAL_PER_GAP = 3000

# 결함 패치 상한 (클래스 x 갭)
# 크랙은 과잉(29,113개)이므로 제한하고, 핀홀은 부족(559개)하므로 전량 사용한다.
DEFECT_MAX_PER_CLASS_GAP = {
    "surface_crack": 200,
    "delamination": 200,
    "pinhole": 200,
}

# 이미지 단위 분할 비율
SPLIT_RATIOS = {"train": 0.70, "val": 0.15, "test": 0.15}

RANDOM_SEED = 42

OUTPUT_ROOT = "data/patches"

CHANNEL_NAMES = {
    0: "surface_crack",
    1: "delamination",
    2: "pinhole",
    3: "unclassified",
}


# ===============================================================
# 메타데이터
# ===============================================================

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


# ===============================================================
# 이미지 단위 분할 (누수 방지)
# ===============================================================

def split_images_by_gap(metadata, split_ratios, seed):
    """
    코팅 갭 조건별로 층화하여 이미지를 train/val/test 로 나눈다.
    패치가 아니라 이미지 단위로 나누는 것이 핵심이다.
    """
    gap_groups = {}
    for stem in metadata:
        gap = metadata[stem]["coating_gap"]
        if gap is None:
            continue
        if gap not in gap_groups:
            gap_groups[gap] = []
        gap_groups[gap].append(stem)

    assignment = {}
    random_generator = random.Random(seed)

    gap_keys = sorted(gap_groups.keys())
    for gap in gap_keys:
        stems = sorted(gap_groups[gap])
        random_generator.shuffle(stems)

        total = len(stems)
        train_end = int(total * split_ratios["train"])
        val_end = train_end + int(total * split_ratios["val"])

        index = 0
        while index < total:
            stem = stems[index]
            if index < train_end:
                assignment[stem] = "train"
            elif index < val_end:
                assignment[stem] = "val"
            else:
                assignment[stem] = "test"
            index = index + 1

    return assignment


def report_split(assignment, metadata):
    counts = {}
    for stem in assignment:
        split_name = assignment[stem]
        gap = metadata[stem]["coating_gap"]
        key = (split_name, gap)
        if key not in counts:
            counts[key] = 0
        counts[key] = counts[key] + 1

    gap_values = set()
    for stem in assignment:
        gap_values.add(metadata[stem]["coating_gap"])
    gap_list = sorted(gap_values)

    print("=== 이미지 단위 분할 (갭별 층화) ===")
    header = "gap".ljust(8)
    for split_name in ["train", "val", "test"]:
        header = header + split_name.ljust(8)
    print(header)

    for gap in gap_list:
        line = str(gap).ljust(8)
        for split_name in ["train", "val", "test"]:
            key = (split_name, gap)
            value = 0
            if key in counts:
                value = counts[key]
            line = line + str(value).ljust(8)
        print(line)

    totals = {"train": 0, "val": 0, "test": 0}
    for stem in assignment:
        totals[assignment[stem]] = totals[assignment[stem]] + 1
    print("합계".ljust(8) + str(totals["train"]).ljust(8)
          + str(totals["val"]).ljust(8) + str(totals["test"]).ljust(8))
    print()


# ===============================================================
# 마스크 처리
# ===============================================================

def load_channel_masks(mask_path):
    """4채널 마스크를 채널별 bool 배열 리스트로 반환한다."""
    mask_array = np.array(Image.open(mask_path))
    channels = []
    channel_index = 0
    while channel_index < mask_array.shape[2]:
        channel = mask_array[:, :, channel_index] > 127
        channels.append(channel)
        channel_index = channel_index + 1
    return channels


def combine_channels(channels):
    """모든 채널을 OR 로 합친 결함 맵."""
    combined = np.zeros(channels[0].shape, dtype=bool)
    for channel in channels:
        combined = np.logical_or(combined, channel)
    return combined


def dilate_map(binary_map, margin_pixels):
    if margin_pixels <= 0:
        return binary_map

    if HAS_CV2 is True:
        kernel_size = margin_pixels * 2 + 1
        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        dilated = cv2.dilate(binary_map.astype(np.uint8), kernel, iterations=1)
        return dilated > 0

    dilated = binary_map.copy()
    shift = 1
    while shift <= margin_pixels:
        dilated[shift:, :] = np.logical_or(dilated[shift:, :], binary_map[:-shift, :])
        dilated[:-shift, :] = np.logical_or(dilated[:-shift, :], binary_map[shift:, :])
        dilated[:, shift:] = np.logical_or(dilated[:, shift:], binary_map[:, :-shift])
        dilated[:, :-shift] = np.logical_or(dilated[:, :-shift], binary_map[:, shift:])
        shift = shift + 1
    return dilated


# ===============================================================
# 패치 후보 수집
# ===============================================================

def collect_patch_candidates(mask_path, patch_size, stride, margin_pixels, min_defect_pixels_map):
    """
    한 이미지에서 패치 좌표를 훑어 분류한다.

    반환: {
      "normal": [(y, x), ...],
      "surface_crack": [(y, x), ...],
      "delamination": [...],
      "pinhole": [...],
    }

    애매한 패치(결함이 있으나 클래스별 기준 미만)는 어디에도 넣지 않는다.
    한 패치가 여러 클래스 기준을 동시에 넘으면 각 클래스에 모두 등록된다.
    """
    channels = load_channel_masks(mask_path)
    combined = combine_channels(channels)
    combined_dilated = dilate_map(combined, margin_pixels)

    height = combined.shape[0]
    width = combined.shape[1]

    result = {}
    result["normal"] = []
    result["surface_crack"] = []
    result["delamination"] = []
    result["pinhole"] = []

    y = 0
    while y + patch_size <= height:
        x = 0
        while x + patch_size <= width:
            # 정상 판정: 팽창된 결함 맵 기준으로 완전히 깨끗해야 함
            dilated_patch = combined_dilated[y:y + patch_size, x:x + patch_size]
            if np.any(dilated_patch) == False:
                result["normal"].append((y, x))
                x = x + stride
                continue

            # 결함 판정: 채널별 기준을 각각 적용
            channel_index = 0
            while channel_index < 3:
                class_name = CHANNEL_NAMES[channel_index]
                threshold = min_defect_pixels_map[class_name]

                channel_patch = channels[channel_index][y:y + patch_size, x:x + patch_size]
                defect_pixels = int(np.sum(channel_patch))
                if defect_pixels >= threshold:
                    result[class_name].append((y, x))
                channel_index = channel_index + 1

            x = x + stride
        y = y + stride

    return result


# ===============================================================
# 수확량 집계 (dry-run)
# ===============================================================

def survey_patches(mask_paths, metadata, assignment):
    """
    전체를 훑어 분할별/갭별/클래스별 패치 수확량을 집계한다.
    실제 저장 없이 계획만 세운다.
    반환: records 리스트 (stem, split, gap, class_name, y, x)
    """
    records = []
    processed = 0

    for path in mask_paths:
        stem = os.path.basename(path)
        if stem.endswith(".png"):
            stem = stem[:-4]
        if stem not in metadata:
            continue
        if stem not in assignment:
            continue

        gap = metadata[stem]["coating_gap"]
        split_name = assignment[stem]

        candidates = collect_patch_candidates(
            path, PATCH_SIZE, STRIDE, MARGIN_PIXELS, MIN_DEFECT_PIXELS
        )

        for class_name in candidates:
            for coordinate in candidates[class_name]:
                record = {}
                record["stem"] = stem
                record["split"] = split_name
                record["gap"] = gap
                record["class_name"] = class_name
                record["y"] = coordinate[0]
                record["x"] = coordinate[1]
                records.append(record)

        processed = processed + 1
        if processed % 300 == 0:
            print("  스캔 중:", processed, "/", len(mask_paths))

    print("  스캔 완료:", processed, "장")
    print()
    return records


def report_survey(records):
    """분할별 / 클래스별 / 갭별 집계."""
    by_split_class = {}
    by_split_class_gap = {}

    for record in records:
        key = (record["split"], record["class_name"])
        if key not in by_split_class:
            by_split_class[key] = 0
        by_split_class[key] = by_split_class[key] + 1

        key2 = (record["split"], record["class_name"], record["gap"])
        if key2 not in by_split_class_gap:
            by_split_class_gap[key2] = 0
        by_split_class_gap[key2] = by_split_class_gap[key2] + 1

    class_names = ["normal", "surface_crack", "delamination", "pinhole"]
    split_names = ["train", "val", "test"]

    print("=== 패치 수확량 (분할 x 클래스) ===")
    header = "class".ljust(16)
    for split_name in split_names:
        header = header + split_name.ljust(10)
    print(header)

    for class_name in class_names:
        line = class_name.ljust(16)
        for split_name in split_names:
            key = (split_name, class_name)
            value = 0
            if key in by_split_class:
                value = by_split_class[key]
            line = line + str(value).ljust(10)
        print(line)
    print()

    print("=== train normal 갭별 분포 (균등 샘플링 가능량 판단) ===")
    gap_values = set()
    for record in records:
        gap_values.add(record["gap"])
    gap_list = sorted(gap_values)

    print("gap".ljust(8) + "train_normal")
    min_available = None
    for gap in gap_list:
        key = ("train", "normal", gap)
        value = 0
        if key in by_split_class_gap:
            value = by_split_class_gap[key]
        print(str(gap).ljust(8) + str(value))
        if min_available is None or value < min_available:
            min_available = value

    print()
    print("=== 결함 패치: val+test 합계 (train 제외, 평가용) ===")
    eval_counts = {}
    for record in records:
        if record["class_name"] == "normal":
            continue
        if record["split"] == "train":
            continue
        name = record["class_name"]
        if name not in eval_counts:
            eval_counts[name] = 0
        eval_counts[name] = eval_counts[name] + 1
    for name in ["surface_crack", "delamination", "pinhole"]:
        value = 0
        if name in eval_counts:
            value = eval_counts[name]
        print(name.ljust(16) + str(value))
    print()

    print("최소 갭 수확량:", min_available)
    print("목표:", NORMAL_PER_GAP, "개/갭")
    if min_available is not None and min_available < NORMAL_PER_GAP:
        print("경고: 목표에 미달하는 갭이 있습니다. NORMAL_PER_GAP 을",
              min_available, "이하로 낮추거나 STRIDE 를 줄이세요.")
    print()


# ===============================================================
# 샘플링 및 저장
# ===============================================================

def sample_balanced_normals(records, normal_per_gap, seed):
    """train/val 의 정상 패치를 갭별로 균등 샘플링한다."""
    random_generator = random.Random(seed)

    grouped = {}
    for record in records:
        if record["class_name"] != "normal":
            continue
        key = (record["split"], record["gap"])
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(record)

    selected = []
    keys = sorted(grouped.keys(), key=lambda item: (item[0], item[1]))
    for key in keys:
        group = grouped[key]
        random_generator.shuffle(group)

        target = normal_per_gap
        if key[0] == "val":
            # val 정상은 임계값 결정용이므로 소량이면 충분하다
            target = int(normal_per_gap * 0.2)
        if key[0] == "test":
            # test 정상은 결함 패치와 균형을 맞춘다 (FPR 측정용)
            target = int(normal_per_gap * 0.2)

        take = min(target, len(group))
        index = 0
        while index < take:
            selected.append(group[index])
            index = index + 1

    return selected


def sample_defect_patches(records, max_per_class_gap_map, seed):
    """
    결함 패치를 val + test 분할 이미지에서 샘플링한다.

    train 분할을 제외하는 이유:
      비지도 이상 탐지는 정상 패치만 학습하므로 결함 패치 자체는 누수가 아니다.
      다만 결함 패치의 출처 이미지가 train 정상 패치의 출처와 같으면,
      모델이 그 이미지의 배경에 이미 익숙해진 상태로 평가받게 되어 유리해진다.
      따라서 train 이미지에서 나온 결함 패치는 사용하지 않는다.

    dry-run 결과 val+test 를 합치면:
      delamination 2113 + 887 = 3000
      pinhole      260 + 299 = 559
    로 test 단독일 때보다 크게 늘어난다.
    """
    random_generator = random.Random(seed + 1)

    grouped = {}
    for record in records:
        if record["class_name"] == "normal":
            continue
        if record["split"] == "train":
            continue
        key = (record["class_name"], record["gap"])
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(record)

    selected = []
    keys = sorted(grouped.keys())
    for key in keys:
        class_name = key[0]
        group = grouped[key]
        random_generator.shuffle(group)

        limit = max_per_class_gap_map[class_name]
        take = min(limit, len(group))

        index = 0
        while index < take:
            record = dict(group[index])
            # 평가용이므로 저장 위치는 eval 로 통일한다
            record["split"] = "eval"
            selected.append(record)
            index = index + 1

    return selected


def write_patches(selected_records, image_dir, output_root):
    """선택된 패치를 실제로 잘라 저장하고 manifest 를 만든다."""

    # 이미지별로 묶어 파일 I/O 를 줄인다
    by_stem = {}
    for record in selected_records:
        stem = record["stem"]
        if stem not in by_stem:
            by_stem[stem] = []
        by_stem[stem].append(record)

    manifest_rows = []
    written = 0

    stems = sorted(by_stem.keys())
    for stem in stems:
        image_path = os.path.join(image_dir, stem + ".jpg")
        if os.path.exists(image_path) is False:
            continue

        image = Image.open(image_path).convert("RGB")
        image_array = np.array(image)

        for record in by_stem[stem]:
            y = record["y"]
            x = record["x"]
            patch_array = image_array[y:y + PATCH_SIZE, x:x + PATCH_SIZE, :]

            out_dir = os.path.join(output_root, record["split"], record["class_name"])
            if os.path.exists(out_dir) is False:
                os.makedirs(out_dir)

            out_name = stem + "_y" + str(y) + "_x" + str(x) + ".png"
            out_path = os.path.join(out_dir, out_name)

            patch_image = Image.fromarray(patch_array)
            patch_image.save(out_path)

            manifest_row = {}
            manifest_row["patch_file"] = out_name
            manifest_row["split"] = record["split"]
            manifest_row["class_name"] = record["class_name"]
            manifest_row["source_image"] = stem
            manifest_row["coating_gap"] = record["gap"]
            manifest_row["y"] = y
            manifest_row["x"] = x
            manifest_rows.append(manifest_row)

            written = written + 1

        if written % 1000 < len(by_stem[stem]):
            print("  저장 중:", written, "패치")

    manifest_path = os.path.join(output_root, "manifest.csv")
    manifest_file = open(manifest_path, "w", newline="", encoding="utf-8")
    field_names = ["patch_file", "split", "class_name", "source_image",
                   "coating_gap", "y", "x"]
    writer = csv.DictWriter(manifest_file, fieldnames=field_names)
    writer.writeheader()
    for row in manifest_rows:
        writer.writerow(row)
    manifest_file.close()

    print("  저장 완료:", written, "패치")
    print("  manifest:", manifest_path)
    print()


# ===============================================================

def main():
    csv_path = "classification/labels.csv"
    mask_dir = "segmentation/masks"
    image_dir = "segmentation/images"

    if os.path.exists(csv_path) is False:
        print("labels.csv 를 찾을 수 없습니다. 데이터셋 루트에서 실행하세요.")
        print("현재 위치:", os.getcwd())
        return

    write_mode = False
    if "--write" in sys.argv:
        write_mode = True

    print("설정: patch", PATCH_SIZE, "px / stride", STRIDE, "/ margin", MARGIN_PIXELS, "px")
    print("클래스별 최소 결함 픽셀:", MIN_DEFECT_PIXELS)
    print("모드:", "실제 저장" if write_mode else "dry-run (수확량 조사만)")
    print()

    metadata = load_metadata(csv_path)
    mask_paths = sorted(glob.glob(mask_dir + "/*.png"))

    assignment = split_images_by_gap(metadata, SPLIT_RATIOS, RANDOM_SEED)
    report_split(assignment, metadata)

    print("=== 패치 스캔 ===")
    records = survey_patches(mask_paths, metadata, assignment)
    report_survey(records)

    if write_mode is False:
        print("dry-run 종료. 수확량이 적절하면 --write 로 다시 실행하세요.")
        return

    print("=== 샘플링 및 저장 ===")
    normal_selected = sample_balanced_normals(records, NORMAL_PER_GAP, RANDOM_SEED)
    defect_selected = sample_defect_patches(records, DEFECT_MAX_PER_CLASS_GAP, RANDOM_SEED)

    all_selected = []
    for record in normal_selected:
        all_selected.append(record)
    for record in defect_selected:
        all_selected.append(record)

    print("선택된 패치:", len(all_selected),
          "(정상", len(normal_selected), "/ 결함", len(defect_selected), ")")
    print()

    write_patches(all_selected, image_dir, OUTPUT_ROOT)


main()
