"""
11. 마스크 -> YOLO 검출 라벨 자동 생성

배경:
  데이터셋에는 검출 라벨이 581개 파일(564박스)뿐이며 핀홀만 라벨링되어 있다.
  반면 세그멘테이션 마스크는 2,227장 전부 존재한다.

  마스크에서 연결 요소를 찾아 바운딩 박스로 변환하면
  크랙/박리/핀홀 3클래스의 검출 라벨을 자동 생성할 수 있다.
  저자들이 핀홀만 만든 것을 전 클래스로 확장하는 셈이다.

  손으로 그리지 않는 이유는 노동을 아끼려는 것이 아니라,
  저자들이 U-Net 앙상블 pseudo-label + 사람 수정으로 만든 마스크가
  새로 그린 것보다 정확하고 일관되기 때문이다.

핵심 검증:
  자동 생성한 핀홀 박스를 저자의 기존 핀홀 라벨과 대조한다.
  IoU 가 높으면 변환 로직이 타당하다는 근거가 된다.
  이 검증 없이는 생성된 라벨을 믿을 수 없다.

크랙 처리의 특수성:
  크랙은 길고 구불구불한 곡선이라 바운딩 박스가 부적합하다.
  예를 들어 대각선으로 뻗은 크랙의 박스는 대부분이 배경이다.
  두 가지 방식을 모두 생성하고 비교한다.
    whole    : 연결 요소 전체를 하나의 박스로
    segment  : 윤곽을 일정 길이로 분할하여 여러 박스로

사용법:
  python3 11_mask_to_detection.py              # dry-run, 통계만
  python3 11_mask_to_detection.py --write      # 라벨 파일 생성
"""

import csv
import os
import sys
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
EXISTING_LABEL_DIR = "detection/labels"
LABELS_CSV = "classification/labels.csv"

OUTPUT_ROOT = "data/detection_auto"

IMAGE_WIDTH = 640
IMAGE_HEIGHT = 480

# 채널 -> 클래스 인덱스 (YOLO 학습용)
CHANNEL_TO_CLASS = {
    0: 0,   # surface_crack
    1: 1,   # delamination
    2: 2,   # pinhole
}
CLASS_NAMES = ["surface_crack", "delamination", "pinhole"]

# 클래스별 최소 면적 (px). 이보다 작은 연결 요소는 노이즈로 간주
MIN_AREA = {
    0: 30,    # crack
    1: 30,    # delamination
    2: 8,     # pinhole - 03 분석에서 면적 중앙값 21~66px 이었음
}

# 크랙 분할 시 한 세그먼트의 최대 길이 (px)
CRACK_SEGMENT_LENGTH = 96


# ===============================================================
# 마스크 -> 박스
# ===============================================================

def extract_boxes_whole(binary_mask, min_area):
    """
    연결 요소 하나당 박스 하나.
    반환: [(x_min, y_min, x_max, y_max, area), ...]
    """
    contours, _ = cv2.findContours(
        binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    boxes = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        boxes.append((x, y, x + w, y + h, float(area)))

    return boxes


def extract_boxes_segmented(binary_mask, min_area, segment_length):
    """
    긴 연결 요소를 여러 박스로 분할한다.

    크랙처럼 길게 뻗은 결함은 전체를 하나의 박스로 감싸면
    박스 면적 대부분이 배경이 되어 검출기 학습에 해롭다.
    긴 축을 따라 일정 길이로 잘라 여러 박스를 만든다.
    """
    contours, _ = cv2.findContours(
        binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    boxes = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue

        x, y, w, h = cv2.boundingRect(contour)
        longer_side = max(w, h)

        if longer_side <= segment_length:
            boxes.append((x, y, x + w, y + h, float(area)))
            continue

        # 긴 축을 따라 분할
        num_segments = int(np.ceil(longer_side / float(segment_length)))

        segment_index = 0
        while segment_index < num_segments:
            if w >= h:
                seg_x_start = x + int(w * segment_index / num_segments)
                seg_x_end = x + int(w * (segment_index + 1) / num_segments)
                seg_y_start = y
                seg_y_end = y + h
            else:
                seg_x_start = x
                seg_x_end = x + w
                seg_y_start = y + int(h * segment_index / num_segments)
                seg_y_end = y + int(h * (segment_index + 1) / num_segments)

            # 해당 구간에 실제 마스크 픽셀이 있는지 확인하고 타이트하게 재조정
            sub_mask = binary_mask[seg_y_start:seg_y_end, seg_x_start:seg_x_end]
            if np.sum(sub_mask) < min_area * 0.3:
                segment_index = segment_index + 1
                continue

            rows = np.any(sub_mask > 0, axis=1)
            columns = np.any(sub_mask > 0, axis=0)
            row_indices = np.where(rows)[0]
            column_indices = np.where(columns)[0]

            if len(row_indices) == 0 or len(column_indices) == 0:
                segment_index = segment_index + 1
                continue

            tight_y_start = seg_y_start + int(row_indices[0])
            tight_y_end = seg_y_start + int(row_indices[-1]) + 1
            tight_x_start = seg_x_start + int(column_indices[0])
            tight_x_end = seg_x_start + int(column_indices[-1]) + 1

            segment_area = float(np.sum(sub_mask > 0))
            boxes.append((tight_x_start, tight_y_start,
                          tight_x_end, tight_y_end, segment_area))

            segment_index = segment_index + 1

    return boxes


def box_to_yolo(box, image_width, image_height):
    """(x_min, y_min, x_max, y_max) -> (x_center, y_center, w, h) 정규화"""
    x_min = box[0]
    y_min = box[1]
    x_max = box[2]
    y_max = box[3]

    width = (x_max - x_min) / float(image_width)
    height = (y_max - y_min) / float(image_height)
    x_center = (x_min + x_max) / 2.0 / float(image_width)
    y_center = (y_min + y_max) / 2.0 / float(image_height)

    return (x_center, y_center, width, height)


# ===============================================================
# 검증: 기존 라벨과 대조
# ===============================================================

def load_existing_labels(label_dir):
    """저자의 기존 검출 라벨을 읽는다. class 0 = 핀홀."""
    lookup = {}
    paths = sorted(glob.glob(label_dir + "/*.txt"))

    for path in paths:
        stem = os.path.basename(path)
        if stem.endswith(".txt"):
            stem = stem[:-4]

        text_file = open(path, "r")
        lines = text_file.readlines()
        text_file.close()

        boxes = []
        for line in lines:
            stripped = line.strip()
            if len(stripped) == 0:
                continue
            parts = stripped.split()
            if len(parts) < 5:
                continue

            class_id = parts[0]
            x_center = float(parts[1]) * IMAGE_WIDTH
            y_center = float(parts[2]) * IMAGE_HEIGHT
            width = float(parts[3]) * IMAGE_WIDTH
            height = float(parts[4]) * IMAGE_HEIGHT

            x_min = x_center - width / 2.0
            y_min = y_center - height / 2.0
            x_max = x_center + width / 2.0
            y_max = y_center + height / 2.0
            boxes.append((class_id, x_min, y_min, x_max, y_max))

        lookup[stem] = boxes

    return lookup


def compute_iou(box_a, box_b):
    """두 박스의 IoU. 입력은 (x_min, y_min, x_max, y_max)."""
    x_min = max(box_a[0], box_b[0])
    y_min = max(box_a[1], box_b[1])
    x_max = min(box_a[2], box_b[2])
    y_max = min(box_a[3], box_b[3])

    if x_max <= x_min or y_max <= y_min:
        return 0.0

    intersection = (x_max - x_min) * (y_max - y_min)
    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
    union = area_a + area_b - intersection

    if union <= 0:
        return 0.0
    return intersection / union


def validate_against_existing(mask_paths, existing_labels, iou_threshold):
    """
    자동 생성한 핀홀 박스를 저자 라벨과 대조한다.
    이 검증을 통과해야 생성된 라벨을 신뢰할 수 있다.
    """
    print("=" * 80)
    print("검증: 자동 생성 핀홀 박스 vs 저자 기존 라벨")
    print("=" * 80)
    print()

    total_existing = 0
    total_generated = 0
    matched = 0
    iou_values = []

    checked = 0
    for path in mask_paths:
        stem = os.path.basename(path)
        if stem.endswith(".png"):
            stem = stem[:-4]
        if stem not in existing_labels:
            continue

        existing_boxes = []
        for entry in existing_labels[stem]:
            if entry[0] != "0":
                continue
            existing_boxes.append((entry[1], entry[2], entry[3], entry[4]))

        if len(existing_boxes) == 0:
            continue

        mask_array = np.array(Image.open(path))
        pinhole_binary = (mask_array[:, :, 2] > 127).astype(np.uint8)
        generated = extract_boxes_whole(pinhole_binary, MIN_AREA[2])

        total_existing = total_existing + len(existing_boxes)
        total_generated = total_generated + len(generated)

        # 그리디 매칭
        used = set()
        for existing_box in existing_boxes:
            best_iou = 0.0
            best_index = -1
            index = 0
            while index < len(generated):
                if index in used:
                    index = index + 1
                    continue
                candidate = (generated[index][0], generated[index][1],
                             generated[index][2], generated[index][3])
                iou = compute_iou(existing_box, candidate)
                if iou > best_iou:
                    best_iou = iou
                    best_index = index
                index = index + 1

            if best_index >= 0 and best_iou >= iou_threshold:
                matched = matched + 1
                used.add(best_index)
                iou_values.append(best_iou)

        checked = checked + 1

    print("대조 이미지:", checked, "장")
    print("저자 라벨 박스:", total_existing)
    print("자동 생성 박스:", total_generated)
    print("매칭 (IoU >=", iou_threshold, "):", matched)

    recall = 0.0
    if total_existing > 0:
        recall = matched / float(total_existing)
    precision = 0.0
    if total_generated > 0:
        precision = matched / float(total_generated)

    print("재현율 (저자 라벨 중 자동 생성이 찾은 비율):", round(recall, 4))
    print("정밀도 (자동 생성 중 저자 라벨과 일치한 비율):", round(precision, 4))

    if len(iou_values) > 0:
        array = np.array(iou_values)
        print("매칭된 박스의 IoU: 중앙값", round(float(np.median(array)), 4),
              "| 평균", round(float(np.mean(array)), 4))
    print()

    if recall >= 0.8:
        print("판정: 변환 로직 타당. 저자 라벨을 대부분 재현한다.")
    elif recall >= 0.5:
        print("판정: 부분 일치. MIN_AREA 조정 검토 필요.")
    else:
        print("판정: 불일치. 마스크와 검출 라벨의 기준이 다를 가능성.")
    print()

    return recall, precision


# ===============================================================
# 생성
# ===============================================================

def generate_all_labels(mask_paths, crack_mode, write_mode, output_root):
    """전체 마스크에서 3클래스 검출 라벨을 생성한다."""

    print("=" * 80)
    print("라벨 생성 (crack_mode =", crack_mode, ")")
    print("=" * 80)
    print()

    if write_mode is True:
        label_dir = os.path.join(output_root, "labels")
        if os.path.exists(label_dir) is False:
            os.makedirs(label_dir)

    class_box_counts = {}
    class_area_values = {}
    class_aspect_values = {}
    images_with_class = {}

    for class_index in range(3):
        class_box_counts[class_index] = 0
        class_area_values[class_index] = []
        class_aspect_values[class_index] = []
        images_with_class[class_index] = 0

    processed = 0
    empty_images = 0

    for path in mask_paths:
        stem = os.path.basename(path)
        if stem.endswith(".png"):
            stem = stem[:-4]

        mask_array = np.array(Image.open(path))
        output_lines = []

        for channel_index in range(3):
            class_index = CHANNEL_TO_CLASS[channel_index]
            binary = (mask_array[:, :, channel_index] > 127).astype(np.uint8)

            if np.sum(binary) == 0:
                continue

            if class_index == 0 and crack_mode == "segment":
                boxes = extract_boxes_segmented(
                    binary, MIN_AREA[class_index], CRACK_SEGMENT_LENGTH
                )
            else:
                boxes = extract_boxes_whole(binary, MIN_AREA[class_index])

            if len(boxes) > 0:
                images_with_class[class_index] = images_with_class[class_index] + 1

            for box in boxes:
                yolo_box = box_to_yolo(box, IMAGE_WIDTH, IMAGE_HEIGHT)

                box_width = box[2] - box[0]
                box_height = box[3] - box[1]
                box_area = box_width * box_height
                longer = max(box_width, box_height)
                shorter = min(box_width, box_height)
                aspect = 1.0
                if shorter > 0:
                    aspect = longer / float(shorter)

                # 박스 면적 대비 실제 결함 면적 비율 (fill ratio)
                class_box_counts[class_index] = class_box_counts[class_index] + 1
                class_area_values[class_index].append(box_area)
                class_aspect_values[class_index].append(aspect)

                line = (str(class_index) + " "
                        + str(round(yolo_box[0], 6)) + " "
                        + str(round(yolo_box[1], 6)) + " "
                        + str(round(yolo_box[2], 6)) + " "
                        + str(round(yolo_box[3], 6)))
                output_lines.append(line)

        if len(output_lines) == 0:
            empty_images = empty_images + 1

        if write_mode is True:
            out_path = os.path.join(output_root, "labels", stem + ".txt")
            out_file = open(out_path, "w")
            for line in output_lines:
                out_file.write(line + "\n")
            out_file.close()

        processed = processed + 1
        if processed % 500 == 0:
            print("  처리 중:", processed, "/", len(mask_paths))

    print("  처리 완료:", processed, "장 (결함 없음:", empty_images, "장)")
    print()

    print("class".ljust(18) + "images".ljust(9) + "boxes".ljust(9)
          + "box/img".ljust(10) + "area_median".ljust(13) + "aspect_median")
    for class_index in range(3):
        count = class_box_counts[class_index]
        if count == 0:
            continue
        areas = np.array(class_area_values[class_index])
        aspects = np.array(class_aspect_values[class_index])

        per_image = 0.0
        if images_with_class[class_index] > 0:
            per_image = count / float(images_with_class[class_index])

        line = CLASS_NAMES[class_index].ljust(18)
        line = line + str(images_with_class[class_index]).ljust(9)
        line = line + str(count).ljust(9)
        line = line + str(round(per_image, 2)).ljust(10)
        line = line + str(round(float(np.median(areas)), 1)).ljust(13)
        line = line + str(round(float(np.median(aspects)), 2))
        print(line)
    print()

    return class_box_counts


def write_dataset_yaml(output_root):
    """Ultralytics 학습용 data.yaml"""
    content = []
    content.append("path: " + os.path.abspath(output_root))
    content.append("train: images/train")
    content.append("val: images/val")
    content.append("")
    content.append("names:")
    for class_index in range(3):
        content.append("  " + str(class_index) + ": " + CLASS_NAMES[class_index])

    yaml_path = os.path.join(output_root, "data.yaml")
    yaml_file = open(yaml_path, "w")
    for line in content:
        yaml_file.write(line + "\n")
    yaml_file.close()
    print("data.yaml 생성:", yaml_path)


# ===============================================================

def main():
    if HAS_CV2 is False:
        print("cv2 가 필요합니다.")
        print("pip install opencv-python-headless --break-system-packages")
        return

    if os.path.exists(MASK_DIR) is False:
        print("마스크 폴더를 찾을 수 없습니다:", MASK_DIR)
        print("현재 위치:", os.getcwd())
        return

    write_mode = False
    if "--write" in sys.argv:
        write_mode = True

    mask_paths = sorted(glob.glob(MASK_DIR + "/*.png"))
    print("마스크:", len(mask_paths), "장")
    print("모드:", "실제 생성" if write_mode else "dry-run (통계만)")
    print()

    # 1) 검증
    existing_labels = load_existing_labels(EXISTING_LABEL_DIR)
    print("저자 기존 라벨:", len(existing_labels), "파일")
    print()
    validate_against_existing(mask_paths, existing_labels, 0.3)

    # 2) 두 가지 크랙 처리 방식 비교
    generate_all_labels(mask_paths, "whole", False, OUTPUT_ROOT)
    generate_all_labels(mask_paths, "segment", write_mode, OUTPUT_ROOT)

    if write_mode is True:
        write_dataset_yaml(OUTPUT_ROOT)
        print()
        print("생성 완료:", os.path.join(OUTPUT_ROOT, "labels"))
        print("다음: 이미지를 train/val 로 분할하여 배치해야 합니다.")
    else:
        print("dry-run 종료. 통계가 타당하면 --write 로 실행하세요.")


main()
