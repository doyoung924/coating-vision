"""
12. 마스크 - 검출 라벨 불일치 진단

문제:
  11 에서 마스크로 생성한 핀홀 박스와 저자의 기존 검출 라벨을 대조한 결과
    저자 라벨 564개 / 자동 생성 468개 / 매칭 135개
    재현율 0.239, 정밀도 0.289, 매칭된 것의 IoU 중앙값 0.35

  박스 개수는 얼추 비슷한데 매칭이 안 된다. 위치나 크기가 어긋난다는 뜻이다.
  또한 저자가 핀홀을 표시한 이미지는 519장인데 마스크에서 핀홀이 나온 것은 437장이다.
  82장에서 불일치가 있다.

검증할 가설:

  H1. 좌표계 차이
      YOLO 포맷은 (x_center, y_center, w, h) 정규화다.
      만약 저자가 (x_min, y_min, w, h) 로 저장했다면 중심이 어긋난다.
      -> 저자 박스 중심과 마스크 무게중심의 오프셋 분포를 본다.

  H2. 박스 크기 관례 차이
      저자가 결함 주변에 여유(padding)를 두고 박스를 그렸다면
      마스크 기반 타이트 박스와 IoU 가 낮게 나온다.
      -> 크기 비율 분포를 본다.

  H3. 채널 매핑 오류
      마스크 채널 2가 핀홀이 아닐 수 있다.
      또는 저자 검출 라벨의 class 0 이 핀홀이 아닐 수 있다.
      -> 각 채널과 저자 박스의 겹침을 전수 비교한다.

  H4. 대상 자체가 다름
      저자 검출 라벨이 마스크와 독립적으로 만들어졌을 수 있다.
      -> 저자 박스 위치에 마스크 픽셀이 존재하는지 본다.

사용법:
  python3 12_diagnose_label_mismatch.py
"""

import os
import glob
import numpy as np
from PIL import Image

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


MASK_DIR = "segmentation/masks"
IMAGE_DIR = "segmentation/images"
LABEL_DIR = "detection/labels"
OUTPUT_DIR = "check_label_mismatch"

IMAGE_WIDTH = 640
IMAGE_HEIGHT = 480


def load_label_file(path):
    """원시 값 그대로 읽는다. 해석은 나중에."""
    entries = []
    text_file = open(path, "r")
    for line in text_file.readlines():
        stripped = line.strip()
        if len(stripped) == 0:
            continue
        parts = stripped.split()
        if len(parts) < 5:
            continue
        entry = {}
        entry["class_id"] = parts[0]
        entry["v1"] = float(parts[1])
        entry["v2"] = float(parts[2])
        entry["v3"] = float(parts[3])
        entry["v4"] = float(parts[4])
        entries.append(entry)
    text_file.close()
    return entries


def mask_centroids(binary_mask, min_area):
    """마스크 연결 요소의 무게중심과 박스를 반환한다."""
    contours, _ = cv2.findContours(
        binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    results = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        moments = cv2.moments(contour)
        if moments["m00"] > 0:
            cx = moments["m10"] / moments["m00"]
            cy = moments["m01"] / moments["m00"]
        else:
            cx = x + w / 2.0
            cy = y + h / 2.0
        results.append({
            "cx": cx, "cy": cy,
            "x": x, "y": y, "w": w, "h": h,
            "area": float(area)
        })
    return results


# ===============================================================
# H4 먼저: 저자 박스 위치에 마스크 픽셀이 있는가
# ===============================================================

def test_overlap_with_channels(label_paths):
    """
    저자 박스 영역 안에 각 마스크 채널의 픽셀이 얼마나 있는지 센다.
    핀홀 채널에서 가장 높게 나와야 정상이다.
    """
    print("=" * 80)
    print("H3/H4. 저자 박스 영역과 각 마스크 채널의 겹침")
    print("=" * 80)
    print()
    print("저자 박스 안에 어느 채널의 픽셀이 있는지 확인한다.")
    print("class 0 이 핀홀이라면 채널 2에서 가장 높아야 한다.")
    print()

    # (class_id, channel) -> 픽셀이 존재한 박스 수
    hit_counts = {}
    total_counts = {}

    checked = 0
    for path in label_paths:
        stem = os.path.basename(path)
        if stem.endswith(".txt"):
            stem = stem[:-4]

        mask_path = os.path.join(MASK_DIR, stem + ".png")
        if os.path.exists(mask_path) is False:
            continue

        mask_array = np.array(Image.open(mask_path))
        entries = load_label_file(path)

        for entry in entries:
            class_id = entry["class_id"]
            if class_id not in total_counts:
                total_counts[class_id] = 0
            total_counts[class_id] = total_counts[class_id] + 1

            # YOLO 표준 해석
            x_center = entry["v1"] * IMAGE_WIDTH
            y_center = entry["v2"] * IMAGE_HEIGHT
            width = entry["v3"] * IMAGE_WIDTH
            height = entry["v4"] * IMAGE_HEIGHT

            x_min = int(max(0, x_center - width / 2.0))
            y_min = int(max(0, y_center - height / 2.0))
            x_max = int(min(IMAGE_WIDTH, x_center + width / 2.0))
            y_max = int(min(IMAGE_HEIGHT, y_center + height / 2.0))

            if x_max <= x_min or y_max <= y_min:
                continue

            channel_index = 0
            while channel_index < 4:
                region = mask_array[y_min:y_max, x_min:x_max, channel_index]
                key = (class_id, channel_index)
                if key not in hit_counts:
                    hit_counts[key] = 0
                if np.sum(region > 127) > 0:
                    hit_counts[key] = hit_counts[key] + 1
                channel_index = channel_index + 1

        checked = checked + 1

    print("검사 파일:", checked)
    print()
    print("class_id".ljust(11) + "박스수".ljust(9)
          + "ch0(crack)".ljust(13) + "ch1(delam)".ljust(13)
          + "ch2(pinhole)".ljust(14) + "ch3(unclass)")

    for class_id in sorted(total_counts.keys()):
        total = total_counts[class_id]
        line = class_id.ljust(11) + str(total).ljust(9)
        channel_index = 0
        while channel_index < 4:
            key = (class_id, channel_index)
            count = 0
            if key in hit_counts:
                count = hit_counts[key]
            ratio = count / float(total)
            text = str(count) + " (" + str(round(ratio * 100, 1)) + "%)"
            if channel_index == 3:
                line = line + text
            else:
                line = line + text.ljust(13 if channel_index < 2 else 14)
            channel_index = channel_index + 1
        print(line)
    print()


# ===============================================================
# H1: 좌표계 해석 비교
# ===============================================================

def test_coordinate_interpretation(label_paths):
    """
    두 가지 해석을 비교한다.
      해석A (YOLO 표준): v1,v2 = 중심
      해석B (좌상단):    v1,v2 = 좌상단
    각 해석에서 박스 안의 핀홀 픽셀 비율을 본다. 맞는 해석이 더 높게 나온다.
    """
    print("=" * 80)
    print("H1. 좌표계 해석 비교")
    print("=" * 80)
    print()

    hit_center = 0
    hit_topleft = 0
    total = 0

    fill_center = []
    fill_topleft = []

    for path in label_paths:
        stem = os.path.basename(path)
        if stem.endswith(".txt"):
            stem = stem[:-4]
        mask_path = os.path.join(MASK_DIR, stem + ".png")
        if os.path.exists(mask_path) is False:
            continue

        mask_array = np.array(Image.open(mask_path))
        pinhole = mask_array[:, :, 2] > 127

        entries = load_label_file(path)
        for entry in entries:
            if entry["class_id"] != "0":
                continue

            width = entry["v3"] * IMAGE_WIDTH
            height = entry["v4"] * IMAGE_HEIGHT

            # 해석 A: 중심
            cx = entry["v1"] * IMAGE_WIDTH
            cy = entry["v2"] * IMAGE_HEIGHT
            ax_min = int(max(0, cx - width / 2.0))
            ay_min = int(max(0, cy - height / 2.0))
            ax_max = int(min(IMAGE_WIDTH, cx + width / 2.0))
            ay_max = int(min(IMAGE_HEIGHT, cy + height / 2.0))

            # 해석 B: 좌상단
            bx_min = int(max(0, entry["v1"] * IMAGE_WIDTH))
            by_min = int(max(0, entry["v2"] * IMAGE_HEIGHT))
            bx_max = int(min(IMAGE_WIDTH, bx_min + width))
            by_max = int(min(IMAGE_HEIGHT, by_min + height))

            total = total + 1

            if ax_max > ax_min and ay_max > ay_min:
                region = pinhole[ay_min:ay_max, ax_min:ax_max]
                pixel_count = int(np.sum(region))
                if pixel_count > 0:
                    hit_center = hit_center + 1
                area = (ax_max - ax_min) * (ay_max - ay_min)
                if area > 0:
                    fill_center.append(pixel_count / float(area))

            if bx_max > bx_min and by_max > by_min:
                region = pinhole[by_min:by_max, bx_min:bx_max]
                pixel_count = int(np.sum(region))
                if pixel_count > 0:
                    hit_topleft = hit_topleft + 1
                area = (bx_max - bx_min) * (by_max - by_min)
                if area > 0:
                    fill_topleft.append(pixel_count / float(area))

    print("class 0 박스:", total)
    print()
    print("해석".ljust(22) + "핀홀 픽셀 포함 박스".ljust(24) + "채움률 중앙값")

    ratio_center = 0.0
    if total > 0:
        ratio_center = hit_center / float(total)
    ratio_topleft = 0.0
    if total > 0:
        ratio_topleft = hit_topleft / float(total)

    median_center = 0.0
    if len(fill_center) > 0:
        median_center = float(np.median(fill_center))
    median_topleft = 0.0
    if len(fill_topleft) > 0:
        median_topleft = float(np.median(fill_topleft))

    print(("A. 중심 (YOLO 표준)").ljust(22)
          + (str(hit_center) + " (" + str(round(ratio_center * 100, 1)) + "%)").ljust(24)
          + str(round(median_center, 4)))
    print(("B. 좌상단").ljust(22)
          + (str(hit_topleft) + " (" + str(round(ratio_topleft * 100, 1)) + "%)").ljust(24)
          + str(round(median_topleft, 4)))
    print()


# ===============================================================
# H2: 크기 비교
# ===============================================================

def test_box_size(label_paths):
    """저자 박스와 마스크 기반 타이트 박스의 크기를 비교한다."""
    print("=" * 80)
    print("H2. 박스 크기 비교")
    print("=" * 80)
    print()

    author_areas = []
    mask_areas = []

    for path in label_paths:
        stem = os.path.basename(path)
        if stem.endswith(".txt"):
            stem = stem[:-4]
        mask_path = os.path.join(MASK_DIR, stem + ".png")
        if os.path.exists(mask_path) is False:
            continue

        entries = load_label_file(path)
        for entry in entries:
            if entry["class_id"] != "0":
                continue
            width = entry["v3"] * IMAGE_WIDTH
            height = entry["v4"] * IMAGE_HEIGHT
            author_areas.append(width * height)

        mask_array = np.array(Image.open(mask_path))
        pinhole_binary = (mask_array[:, :, 2] > 127).astype(np.uint8)
        for component in mask_centroids(pinhole_binary, 8):
            mask_areas.append(component["w"] * component["h"])

    print("저자 박스 면적   : n =", len(author_areas))
    if len(author_areas) > 0:
        array = np.array(author_areas)
        print("  중앙값", round(float(np.median(array)), 1),
              "| p25", round(float(np.percentile(array, 25)), 1),
              "| p75", round(float(np.percentile(array, 75)), 1))

    print("마스크 박스 면적 : n =", len(mask_areas))
    if len(mask_areas) > 0:
        array = np.array(mask_areas)
        print("  중앙값", round(float(np.median(array)), 1),
              "| p25", round(float(np.percentile(array, 25)), 1),
              "| p75", round(float(np.percentile(array, 75)), 1))

    if len(author_areas) > 0 and len(mask_areas) > 0:
        ratio = float(np.median(author_areas)) / float(np.median(mask_areas))
        print()
        print("면적 비율 (저자/마스크):", round(ratio, 3))
        if ratio > 1.5:
            print("  -> 저자가 여유를 두고 박스를 그렸을 가능성")
        elif ratio < 0.67:
            print("  -> 저자 박스가 더 작다. 마스크가 과대 검출했을 가능성")
        else:
            print("  -> 크기는 비슷하다. 위치 문제일 가능성")
    print()


# ===============================================================
# 시각 확인
# ===============================================================

def save_visual_samples(label_paths, num_samples):
    """저자 박스(초록)와 마스크 핀홀(빨강)을 한 이미지에 겹쳐 저장한다."""
    print("=" * 80)
    print("시각 확인용 이미지 저장")
    print("=" * 80)

    if os.path.exists(OUTPUT_DIR) is False:
        os.makedirs(OUTPUT_DIR)

    saved = 0
    for path in label_paths:
        if saved >= num_samples:
            break

        stem = os.path.basename(path)
        if stem.endswith(".txt"):
            stem = stem[:-4]

        image_path = os.path.join(IMAGE_DIR, stem + ".jpg")
        mask_path = os.path.join(MASK_DIR, stem + ".png")
        if os.path.exists(image_path) is False:
            continue
        if os.path.exists(mask_path) is False:
            continue

        entries = load_label_file(path)
        pinhole_entries = []
        for entry in entries:
            if entry["class_id"] == "0":
                pinhole_entries.append(entry)
        if len(pinhole_entries) == 0:
            continue

        image_array = np.array(Image.open(image_path).convert("RGB"))
        mask_array = np.array(Image.open(mask_path))
        pinhole_mask = mask_array[:, :, 2] > 127

        overlay = image_array.copy()
        # 마스크 핀홀 = 빨강
        overlay[pinhole_mask, 0] = 255
        overlay[pinhole_mask, 1] = 0
        overlay[pinhole_mask, 2] = 0

        # 저자 박스 = 초록 테두리
        for entry in pinhole_entries:
            cx = entry["v1"] * IMAGE_WIDTH
            cy = entry["v2"] * IMAGE_HEIGHT
            width = entry["v3"] * IMAGE_WIDTH
            height = entry["v4"] * IMAGE_HEIGHT
            x_min = int(max(0, cx - width / 2.0))
            y_min = int(max(0, cy - height / 2.0))
            x_max = int(min(IMAGE_WIDTH - 1, cx + width / 2.0))
            y_max = int(min(IMAGE_HEIGHT - 1, cy + height / 2.0))

            overlay[y_min:y_min + 2, x_min:x_max, :] = [0, 255, 0]
            overlay[y_max - 2:y_max, x_min:x_max, :] = [0, 255, 0]
            overlay[y_min:y_max, x_min:x_min + 2, :] = [0, 255, 0]
            overlay[y_min:y_max, x_max - 2:x_max, :] = [0, 255, 0]

        combined = np.zeros((IMAGE_HEIGHT, IMAGE_WIDTH * 2, 3), dtype=np.uint8)
        combined[:, :IMAGE_WIDTH, :] = image_array
        combined[:, IMAGE_WIDTH:, :] = overlay

        out_path = os.path.join(OUTPUT_DIR, "cmp_" + stem + ".png")
        Image.fromarray(combined).save(out_path)
        saved = saved + 1

    print("저장:", saved, "장 ->", OUTPUT_DIR)
    print("좌=원본 / 우=마스크 핀홀(빨강) + 저자 박스(초록)")
    print()


def main():
    if HAS_CV2 is False:
        print("cv2 가 필요합니다.")
        return

    if os.path.exists(LABEL_DIR) is False:
        print("라벨 폴더를 찾을 수 없습니다:", LABEL_DIR)
        print("현재 위치:", os.getcwd())
        return

    label_paths = sorted(glob.glob(LABEL_DIR + "/*.txt"))
    print("라벨 파일:", len(label_paths))
    print()

    test_overlap_with_channels(label_paths)
    test_coordinate_interpretation(label_paths)
    test_box_size(label_paths)
    save_visual_samples(label_paths, 6)

    print("=" * 80)
    print("다음: check_label_mismatch/ 이미지를 확인하여")
    print("      초록 박스와 빨강 영역이 어긋나는 양상을 육안으로 판단")
    print("=" * 80)


main()
