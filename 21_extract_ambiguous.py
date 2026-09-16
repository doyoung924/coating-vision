"""
21_extract_ambiguous.py

목적:
- 04_extract_patches.py 는 결함이 임계 미만인 "애매한" 패치를 전부 버렸다.
  이 스크립트는 그 애매 패치를 test 분할 이미지에서 추출해 저장하고,
  06 의 A3/B2 이상 점수를 계산해 VLM 캐스케이드 설계 근거를 만든다.

기준 (04 재사용):
- patch 64px, stride 32, margin 4px
- 정상: 팽창 결함 맵 기준 완전 깨끗
- 결함: crack/delam 82px 이상, pinhole 15px 이상
- **애매**: 팽창 기준 깨끗하지 않으면서 어느 클래스에서도 임계 미만.
           채널 픽셀 수가 최대인 클래스를 주 클래스로 분류.

CLI:
  python 21_extract_ambiguous.py           # dry-run, 통계만
  python 21_extract_ambiguous.py --write   # 저장 + A3/B2 점수 계산 + 격자 이미지

산출 (--write 시):
- data/patches/ambiguous/{crack,delamination,pinhole}/image_N_yYY_xXX.png
- manifest_ambiguous.csv
- check_ambiguous_{class}.png (클래스별 12장 격자)
"""

import argparse
import csv
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import cv2
    HAS_CV2 = True
except Exception:
    HAS_CV2 = False


PROJECT_ROOT = Path(__file__).resolve().parent
MANIFEST_04 = PROJECT_ROOT / "data" / "patches" / "manifest.csv"
IMAGE_DIR = PROJECT_ROOT / "segmentation" / "images"
MASK_DIR = PROJECT_ROOT / "segmentation" / "masks"
TRAIN_NORMAL_DIR = PROJECT_ROOT / "data" / "patches" / "train" / "normal"
TEST_NORMAL_DIR = PROJECT_ROOT / "data" / "patches" / "test" / "normal"

OUT_ROOT = PROJECT_ROOT / "data" / "patches" / "ambiguous"
OUT_MANIFEST = PROJECT_ROOT / "manifest_ambiguous.csv"
OUT_CHECK_DIR = PROJECT_ROOT / "check_ambiguous"

PATCH_SIZE = 64
STRIDE = 32
MARGIN_PIXELS = 4
MIN_DEFECT_PIXELS = {
    "surface_crack": 82,
    "delamination": 82,
    "pinhole": 15,
}
CHANNEL_NAMES = {
    0: "surface_crack",
    1: "delamination",
    2: "pinhole",
}
CLASS_NAMES = ["surface_crack", "delamination", "pinhole"]
CLASS_SHORT = {
    "surface_crack": "crack",
    "delamination": "delamination",
    "pinhole": "pinhole",
}
MAX_PER_CLASS = 300
RANDOM_SEED = 42
FPR_TARGET = 0.05  # 정상 95 퍼센타일을 임계값으로 삼는다


# ---- 04 로직 재사용 ----

def load_channel_masks(mask_path):
    mask_array = np.array(Image.open(mask_path))
    channels = []
    channel_index = 0
    while channel_index < mask_array.shape[2]:
        channel = mask_array[:, :, channel_index] > 127
        channels.append(channel)
        channel_index = channel_index + 1
    return channels


def combine_channels(channels):
    combined = np.zeros(channels[0].shape, dtype=bool)
    for channel in channels:
        combined = np.logical_or(combined, channel)
    return combined


def dilate_map(binary_map, margin_pixels):
    if margin_pixels <= 0:
        return binary_map
    if HAS_CV2:
        kernel_size = margin_pixels * 2 + 1
        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        dilated = cv2.dilate(binary_map.astype(np.uint8), kernel, iterations=1)
        return dilated > 0
    # fallback: 순진한 팽창
    dilated = binary_map.copy()
    shift = 1
    while shift <= margin_pixels:
        dilated[shift:, :] = np.logical_or(dilated[shift:, :], binary_map[:-shift, :])
        dilated[:-shift, :] = np.logical_or(dilated[:-shift, :], binary_map[shift:, :])
        dilated[:, shift:] = np.logical_or(dilated[:, shift:], binary_map[:, :-shift])
        dilated[:, :-shift] = np.logical_or(dilated[:, :-shift], binary_map[:, shift:])
        shift = shift + 1
    return dilated


# ---- test 분할 image 목록 ----

def load_test_source_images():
    source_images = set()
    with open(MANIFEST_04, "r", encoding="utf-8") as file_handle:
        reader = csv.DictReader(file_handle)
        for row in reader:
            if row["split"] == "test":
                source_images.add(row["source_image"])
    return sorted(source_images)


# ---- 이미지-갭 매핑 (04 manifest 에 있음) ----

def load_image_to_gap():
    mapping = {}
    with open(MANIFEST_04, "r", encoding="utf-8") as file_handle:
        reader = csv.DictReader(file_handle)
        for row in reader:
            mapping[row["source_image"]] = int(row["coating_gap"])
    return mapping


# ---- 애매 패치 스캔 ----

def scan_ambiguous_patches(source_image_name, image_to_gap):
    """
    한 이미지에서 애매 패치 후보를 반환한다.
    각 원소: (y, x, primary_class, defect_pixels_by_class, primary_pixels)
    """
    mask_path = MASK_DIR / (source_image_name + ".png")
    if mask_path.exists() is False:
        return []

    channels = load_channel_masks(str(mask_path))
    combined = combine_channels(channels)
    combined_dilated = dilate_map(combined, MARGIN_PIXELS)

    height = combined.shape[0]
    width = combined.shape[1]

    candidates = []
    y = 0
    while y + PATCH_SIZE <= height:
        x = 0
        while x + PATCH_SIZE <= width:
            dilated_patch = combined_dilated[y:y + PATCH_SIZE, x:x + PATCH_SIZE]
            if np.any(dilated_patch) == False:
                # 정상 - 애매 후보 아님
                x = x + STRIDE
                continue

            per_class_pixels = {}
            for channel_index in range(3):
                class_name = CHANNEL_NAMES[channel_index]
                channel_patch = channels[channel_index][y:y + PATCH_SIZE, x:x + PATCH_SIZE]
                per_class_pixels[class_name] = int(np.sum(channel_patch))

            # 결함 임계를 넘긴 채널이 하나라도 있으면 이건 명확한 결함 패치
            crosses_threshold = False
            for class_name in CLASS_NAMES:
                if per_class_pixels[class_name] >= MIN_DEFECT_PIXELS[class_name]:
                    crosses_threshold = True
                    break
            if crosses_threshold:
                x = x + STRIDE
                continue

            # 어느 채널에도 결함이 없으면 (팽창은 걸렸지만 실제는 없음) 스킵
            total_pixels = 0
            for class_name in CLASS_NAMES:
                total_pixels = total_pixels + per_class_pixels[class_name]
            if total_pixels == 0:
                x = x + STRIDE
                continue

            # 주 클래스 = 픽셀 수 최대인 채널
            primary_class = None
            primary_pixels = -1
            for class_name in CLASS_NAMES:
                if per_class_pixels[class_name] > primary_pixels:
                    primary_pixels = per_class_pixels[class_name]
                    primary_class = class_name

            candidates.append({
                "y": y,
                "x": x,
                "primary_class": primary_class,
                "primary_pixels": primary_pixels,
                "per_class_pixels": dict(per_class_pixels),
            })
            x = x + STRIDE
        y = y + STRIDE
    return candidates


# ---- 통계 리포트 ----

def report_stats(all_candidates, image_to_gap, total_normal_patches, total_defect_patches):
    print("=" * 60)
    print("애매 패치 통계")
    print("=" * 60)
    print("전체 애매 패치: {}".format(len(all_candidates)))
    print()

    # 클래스별
    by_class = defaultdict(list)
    for entry in all_candidates:
        by_class[entry["primary_class"]].append(entry)

    print("클래스별:")
    for class_name in CLASS_NAMES:
        candidates = by_class[class_name]
        if len(candidates) == 0:
            print("  {}: 0".format(class_name))
            continue
        pixel_values = []
        for candidate in candidates:
            pixel_values.append(candidate["primary_pixels"])
        pixel_array = np.array(pixel_values)
        print("  {}: n={}, primary_pixels median={:.0f} p25={:.0f} p75={:.0f} max={}".format(
            class_name, len(candidates),
            np.median(pixel_array), np.percentile(pixel_array, 25),
            np.percentile(pixel_array, 75), int(np.max(pixel_array))))

    # 갭별
    print()
    print("클래스 x 갭별 개수:")
    by_class_gap = defaultdict(lambda: defaultdict(int))
    all_images_seen = defaultdict(int)
    for entry in all_candidates:
        gap = image_to_gap[entry["source_image"]]
        by_class_gap[entry["primary_class"]][gap] += 1
        all_images_seen[gap] += 1
    all_gaps = sorted(set(all_images_seen.keys()))
    header = "class".ljust(16)
    for gap in all_gaps:
        header = header + str(gap).rjust(7)
    print(header)
    for class_name in CLASS_NAMES:
        row = class_name.ljust(16)
        for gap in all_gaps:
            row = row + str(by_class_gap[class_name][gap]).rjust(7)
        print(row)

    # 비율
    print()
    print("샘플 규모 대비 비율:")
    if total_normal_patches > 0:
        print("  정상 패치 (전 이미지 스캔 시): {}".format(total_normal_patches))
    if total_defect_patches > 0:
        print("  결함 패치 (전 이미지 스캔 시): {}".format(total_defect_patches))
    grand_total = total_normal_patches + total_defect_patches + len(all_candidates)
    if grand_total > 0:
        share = len(all_candidates) / float(grand_total)
        print("  애매 비율 = {}/{}  = {:.2%}".format(
            len(all_candidates), grand_total, share))


# ---- A3 (밝기 표준편차) ----

def score_a3(patch_uint8):
    gray = np.mean(patch_uint8.astype(np.float64), axis=2)
    return float(np.std(gray))


def compute_a3_batch(patch_arrays):
    scores = []
    for patch_array in patch_arrays:
        scores.append(score_a3(patch_array))
    return np.array(scores)


# ---- 정상 패치 로더 (임계값 산정용) ----

def load_normal_patches(normal_dir, limit):
    files = sorted(os.listdir(normal_dir))
    if limit is not None and len(files) > limit:
        random.Random(RANDOM_SEED).shuffle(files)
        files = files[:limit]
    patches = []
    for name in files:
        path = normal_dir / name
        image = Image.open(path).convert("RGB")
        patches.append(np.array(image))
    return np.stack(patches, axis=0)


# ---- B2 (Mahalanobis) 특징 추출 및 스코어러 ----

def extract_deep_features(patch_uint8_stack, device_name="cpu", batch_size=32):
    import torch
    import torchvision

    device = torch.device(device_name)
    weights = torchvision.models.ResNet18_Weights.IMAGENET1K_V1
    model = torchvision.models.resnet18(weights=weights)
    model.eval()
    model.to(device)

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    features_list = []
    total = patch_uint8_stack.shape[0]
    start = 0
    while start < total:
        end = min(start + batch_size, total)
        batch = patch_uint8_stack[start:end].astype(np.float32) / 255.0
        batch = (batch - mean) / std
        batch = np.transpose(batch, (0, 3, 1, 2))
        tensor = torch.from_numpy(batch).to(device)
        with torch.no_grad():
            x = model.conv1(tensor)
            x = model.bn1(x)
            x = model.relu(x)
            x = model.maxpool(x)
            x = model.layer1(x)
            feature2 = model.layer2(x)
            feature3 = model.layer3(feature2)
            pooled2 = torch.nn.functional.adaptive_avg_pool2d(feature2, 1)
            pooled3 = torch.nn.functional.adaptive_avg_pool2d(feature3, 1)
            combined = torch.cat([pooled2.flatten(1), pooled3.flatten(1)], dim=1)
        features_list.append(combined.cpu().numpy())
        start = end
    return np.concatenate(features_list, axis=0)


def build_mahalanobis_scorer(train_features):
    mean_vector = np.mean(train_features, axis=0)
    centered = train_features - mean_vector
    covariance = np.cov(centered, rowvar=False)
    dimension = covariance.shape[0]
    covariance = covariance + np.eye(dimension) * 0.01
    inverse_covariance = np.linalg.inv(covariance)

    def scorer(query_features):
        difference = query_features - mean_vector
        left = np.dot(difference, inverse_covariance)
        distances = np.sum(left * difference, axis=1)
        return np.sqrt(np.maximum(distances, 0.0))

    return scorer


# ---- 시각 격자 ----

def build_visual_grid(patch_infos, out_path, upscale=4, columns=4):
    if len(patch_infos) == 0:
        return
    tile = PATCH_SIZE * upscale
    caption_height = 18
    rows = (len(patch_infos) + columns - 1) // columns
    canvas_width = columns * tile
    canvas_height = rows * (tile + caption_height)
    canvas = Image.new("RGB", (canvas_width, canvas_height), (240, 240, 240))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(canvas)

    for index in range(len(patch_infos)):
        info = patch_infos[index]
        patch_image = info["image"].resize((tile, tile), resample=Image.NEAREST)
        row = index // columns
        col = index % columns
        px = col * tile
        py = row * (tile + caption_height)
        canvas.paste(patch_image, (px, py))
        caption = "px={} · {}".format(info["primary_pixels"], info["source"])
        draw.text((px + 4, py + tile + 2), caption, fill=(30, 30, 30))
    canvas.save(str(out_path))


# ---- 실행 ----

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="PNG · manifest · 격자 저장")
    parser.add_argument("--max-per-class", type=int, default=MAX_PER_CLASS)
    args = parser.parse_args()

    print("21_extract_ambiguous.py")
    print("mode:", "WRITE" if args.write else "DRY-RUN")
    print()

    test_images = load_test_source_images()
    print("04 test 분할 이미지: {}".format(len(test_images)))

    image_to_gap = load_image_to_gap()

    all_candidates = []
    # 부수적으로 정상 · 결함 패치 개수도 세어 애매 비율을 계산한다.
    total_normal_scanned = 0
    total_defect_scanned = 0

    progress = 0
    for source_image in test_images:
        candidates = scan_ambiguous_patches(source_image, image_to_gap)
        for candidate in candidates:
            candidate["source_image"] = source_image
            all_candidates.append(candidate)

        # 같은 이미지에서 정상 · 결함 패치 몇 개가 있었는지도 함께 집계 (비율 근거)
        counts = count_normal_defect(source_image)
        total_normal_scanned = total_normal_scanned + counts["normal"]
        total_defect_scanned = total_defect_scanned + counts["defect"]

        progress = progress + 1
        if progress % 50 == 0:
            print("  scan: {}/{}".format(progress, len(test_images)))

    print()
    report_stats(all_candidates, image_to_gap, total_normal_scanned, total_defect_scanned)

    if not args.write:
        print()
        print("DRY-RUN 완료. --write 로 다시 실행하면 저장.")
        return

    # ---- 저장 단계 ----
    print()
    print("=" * 60)
    print("저장 단계")
    print("=" * 60)

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    OUT_CHECK_DIR.mkdir(parents=True, exist_ok=True)
    for class_name in CLASS_NAMES:
        (OUT_ROOT / class_name).mkdir(parents=True, exist_ok=True)

    # 클래스별로 300장씩 샘플링
    selected = select_samples(all_candidates, args.max_per_class)

    # 정상 패치 로드 + A3/B2 임계값 산정
    print()
    print("정상 패치 로드 및 임계값 산정 (FPR=0.05, 95pct):")
    normal_test_patches = load_normal_patches(TEST_NORMAL_DIR, limit=2000)
    print("  test normal 로드: {} 패치".format(normal_test_patches.shape[0]))

    normal_a3 = compute_a3_batch(normal_test_patches)
    a3_threshold = float(np.percentile(normal_a3, 95))
    print("  A3 threshold (test normal 95pct) = {:.4f}".format(a3_threshold))

    # B2: train normal 로 스코어러 구축, test normal 로 임계값
    print("  B2 특징 추출 (ResNet18 layer2+layer3):")
    train_normal_patches = load_normal_patches(TRAIN_NORMAL_DIR, limit=2000)
    print("    train normal 로드: {} 패치".format(train_normal_patches.shape[0]))

    train_features = extract_deep_features(train_normal_patches)
    print("    train features: {}".format(train_features.shape))

    test_normal_features = extract_deep_features(normal_test_patches)
    print("    test  features: {}".format(test_normal_features.shape))

    b2_scorer = build_mahalanobis_scorer(train_features)
    normal_b2 = b2_scorer(test_normal_features)
    b2_threshold = float(np.percentile(normal_b2, 95))
    print("  B2 threshold (test normal 95pct) = {:.4f}".format(b2_threshold))

    # 선택된 애매 패치 이미지 로드 + 저장 + 점수 계산
    print()
    print("애매 패치 저장 + 점수 계산 시작:")
    manifest_rows = []
    per_class_gallery = defaultdict(list)

    for entry in selected:
        source_image = entry["source_image"]
        image_path = IMAGE_DIR / (source_image + ".jpg")
        if image_path.exists() is False:
            continue
        image_array = np.array(Image.open(image_path).convert("RGB"))
        y = entry["y"]
        x = entry["x"]
        patch_array = image_array[y:y + PATCH_SIZE, x:x + PATCH_SIZE, :]
        class_short = CLASS_SHORT[entry["primary_class"]]
        patch_name = "{}_y{}_x{}.png".format(source_image, y, x)
        out_path = OUT_ROOT / entry["primary_class"] / patch_name
        Image.fromarray(patch_array).save(str(out_path))
        entry["patch_array"] = patch_array
        entry["patch_file"] = patch_name

        # 갤러리용 12장까지 별도 보관
        if len(per_class_gallery[entry["primary_class"]]) < 12:
            per_class_gallery[entry["primary_class"]].append({
                "image": Image.fromarray(patch_array),
                "primary_pixels": entry["primary_pixels"],
                "source": source_image,
            })

    # A3 점수 일괄 계산
    all_patch_arrays = []
    for entry in selected:
        if "patch_array" in entry:
            all_patch_arrays.append(entry["patch_array"])
    all_patch_stack = np.stack(all_patch_arrays, axis=0)
    a3_scores = compute_a3_batch(all_patch_stack)
    b2_features = extract_deep_features(all_patch_stack)
    b2_scores = b2_scorer(b2_features)

    # manifest 조립
    index_within_selected = 0
    for entry in selected:
        if "patch_array" not in entry:
            continue
        a3 = float(a3_scores[index_within_selected])
        b2 = float(b2_scores[index_within_selected])
        index_within_selected = index_within_selected + 1

        if a3 >= a3_threshold:
            a3_verdict = "above"
        else:
            a3_verdict = "below"
        if b2 >= b2_threshold:
            b2_verdict = "above"
        else:
            b2_verdict = "below"

        manifest_rows.append({
            "patch_file": entry["patch_file"],
            "class": entry["primary_class"],
            "defect_pixels": entry["primary_pixels"],
            "crack_pixels": entry["per_class_pixels"]["surface_crack"],
            "delam_pixels": entry["per_class_pixels"]["delamination"],
            "pinhole_pixels": entry["per_class_pixels"]["pinhole"],
            "source_image": entry["source_image"],
            "coating_gap": image_to_gap[entry["source_image"]],
            "y": entry["y"],
            "x": entry["x"],
            "a3_score": round(a3, 4),
            "a3_threshold": round(a3_threshold, 4),
            "a3_verdict": a3_verdict,
            "b2_score": round(b2, 4),
            "b2_threshold": round(b2_threshold, 4),
            "b2_verdict": b2_verdict,
        })

    with open(OUT_MANIFEST, "w", encoding="utf-8", newline="") as file_handle:
        fieldnames = list(manifest_rows[0].keys())
        writer = csv.DictWriter(file_handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in manifest_rows:
            writer.writerow(row)
    print("  manifest: {} rows -> {}".format(len(manifest_rows), OUT_MANIFEST))

    # 격자 이미지
    for class_name in CLASS_NAMES:
        gallery = per_class_gallery[class_name]
        if len(gallery) == 0:
            continue
        out_path = OUT_CHECK_DIR / ("check_ambiguous_" + CLASS_SHORT[class_name] + ".png")
        build_visual_grid(gallery, out_path, upscale=4, columns=4)
        print("  격자: {} ({}장)".format(out_path.name, len(gallery)))

    # 점수 요약
    print()
    print("=" * 60)
    print("A3/B2 점수 요약 (선택된 애매 패치 기준)")
    print("=" * 60)
    for class_name in CLASS_NAMES:
        rows = []
        for row in manifest_rows:
            if row["class"] == class_name:
                rows.append(row)
        if len(rows) == 0:
            continue
        a3_values = []
        b2_values = []
        a3_above = 0
        b2_above = 0
        for row in rows:
            a3_values.append(row["a3_score"])
            b2_values.append(row["b2_score"])
            if row["a3_verdict"] == "above":
                a3_above = a3_above + 1
            if row["b2_verdict"] == "above":
                b2_above = b2_above + 1
        a3_arr = np.array(a3_values)
        b2_arr = np.array(b2_values)
        print("{} (n={})".format(class_name, len(rows)))
        print("  A3  median={:.2f}, p25={:.2f}, p75={:.2f}, above threshold={}/{} ({:.1%})".format(
            float(np.median(a3_arr)), float(np.percentile(a3_arr, 25)),
            float(np.percentile(a3_arr, 75)), a3_above, len(rows), a3_above / len(rows)))
        print("  B2  median={:.2f}, p25={:.2f}, p75={:.2f}, above threshold={}/{} ({:.1%})".format(
            float(np.median(b2_arr)), float(np.percentile(b2_arr, 25)),
            float(np.percentile(b2_arr, 75)), b2_above, len(rows), b2_above / len(rows)))

    print()
    print("완료.")


def count_normal_defect(source_image):
    """
    이 이미지에서 04 기준 정상/결함 패치 후보가 몇 개인지 센다.
    scan_ambiguous_patches 와 유사하지만 통계 목적이라 별도 함수로 둔다.
    """
    mask_path = MASK_DIR / (source_image + ".png")
    result = {"normal": 0, "defect": 0}
    if mask_path.exists() is False:
        return result
    channels = load_channel_masks(str(mask_path))
    combined = combine_channels(channels)
    combined_dilated = dilate_map(combined, MARGIN_PIXELS)
    height = combined.shape[0]
    width = combined.shape[1]
    y = 0
    while y + PATCH_SIZE <= height:
        x = 0
        while x + PATCH_SIZE <= width:
            dilated_patch = combined_dilated[y:y + PATCH_SIZE, x:x + PATCH_SIZE]
            if np.any(dilated_patch) == False:
                result["normal"] = result["normal"] + 1
                x = x + STRIDE
                continue
            crosses = False
            for channel_index in range(3):
                class_name = CHANNEL_NAMES[channel_index]
                channel_patch = channels[channel_index][y:y + PATCH_SIZE, x:x + PATCH_SIZE]
                if int(np.sum(channel_patch)) >= MIN_DEFECT_PIXELS[class_name]:
                    crosses = True
                    break
            if crosses:
                result["defect"] = result["defect"] + 1
            x = x + STRIDE
        y = y + STRIDE
    return result


def select_samples(candidates, max_per_class):
    """클래스별 무작위 max_per_class 개 선택."""
    by_class = defaultdict(list)
    for entry in candidates:
        by_class[entry["primary_class"]].append(entry)

    selected = []
    random_generator = random.Random(RANDOM_SEED)
    for class_name in CLASS_NAMES:
        pool = list(by_class[class_name])
        random_generator.shuffle(pool)
        chosen = pool[:max_per_class]
        for entry in chosen:
            selected.append(entry)
    print("선택 (클래스별 상한 {}): {} 패치".format(max_per_class, len(selected)))
    return selected


if __name__ == "__main__":
    main()
