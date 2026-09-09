"""
13. 마스크 -> YOLO 검출 라벨 생성 (수정판)

11 에서 재현율 0.239 로 검증 실패했으나, 12 진단 결과 **변환 로직은 옳았고
검증 기준이 잘못되었다**는 것이 밝혀졌다.

12 진단 요약:
  - 채널 매핑 정확: class 0 박스 564개 전부(100%)가 마스크 채널 2(핀홀) 픽셀 포함
                    class 1 박스 85개 전부가 채널 3(unclassified) 대응
  - 좌표계 정확: YOLO 표준(중심) 해석에서 채움률 0.1395, 좌상단 해석 0.0402
  - **박스 크기만 다름**: 저자 323px vs 마스크 81px, 정확히 4배

  박스가 4배 크면 중심이 완벽히 일치해도 IoU 상한이 81/323 = 0.25 다.
  임계값 0.3 으로 걸렀으니 대부분 탈락한 것이 당연했다.
  **서로 다른 라벨링 관례를 IoU 로 비교한 것이 오류였다.**

수정 사항:

1) 클래스별 패딩
   저자 관례(여유 있는 박스)에 맞춘다. 근거:
     - 저자 baseline(YOLOv11 mAP@0.5 = 0.796)과 비교하려면 같은 관례여야 한다.
       박스 크기가 다르면 mAP 자체가 비교 불가다.
     - 작은 객체 검출에서 타이트 박스는 학습이 불안정하다. 핀홀 81px 는
       640x480 에서 극히 작으며, 검출 모델은 객체 주변 문맥을 함께 봐야 한다.
     - 실무 검사 시스템도 "여기쯤 결함이 있다"를 알려주지 픽셀 경계를 요구하지 않는다.

   단 크랙은 선형 결함이라 확대하면 배경만 늘어난다. 박리는 이미 면적이 크다.
   따라서 핀홀만 확대한다.

2) 검증 기준 변경
   IoU 대신 **중심 거리**로 잰다.
   실제로 알고 싶은 것은 "저자 박스와 마스크 결함이 같은 것을 가리키는가"이고,
   그것은 중심이 얼마나 가까운지로 판단해야 한다.
   보조 지표로 패딩 적용 후 IoU 도 함께 본다.

사용법:
  python3 13_mask_to_detection_v2.py           # dry-run
  python3 13_mask_to_detection_v2.py --write   # 라벨 + 데이터셋 구성
"""

import csv
import os
import sys
import glob
import random
import shutil
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

CLASS_NAMES = ["surface_crack", "delamination", "pinhole"]

MIN_AREA = {
    0: 30,
    1: 30,
    2: 8,
}

# 클래스별 박스 선형 확대 배율
# 핀홀 2.0 = 면적 4배. 12 진단에서 측정한 저자/마스크 면적비 3.988 에 대응한다.
BOX_SCALE = {
    0: 1.0,   # crack - 선형 결함이라 확대 시 배경만 증가
    1: 1.0,   # delamination - 면적 중앙값 3457px 로 이미 충분
    2: 2.0,   # pinhole - 저자 관례에 맞춤
}

# 확대 후 최소 박스 크기 (px). 너무 작으면 학습이 불안정하다.
MIN_BOX_SIDE = 12

CRACK_SEGMENT_LENGTH = 96

SPLIT_RATIOS = {"train": 0.70, "val": 0.15, "test": 0.15}
RANDOM_SEED = 42


# ===============================================================
# 메타데이터 (갭별 층화 분할용)
# ===============================================================

def parse_coating_gap(original_name):
    import re
    gap_match = re.search(r"(\d+)um", original_name)
    if gap_match is not None:
        return int(gap_match.group(1))
    return None


def load_gap_lookup(csv_path):
    lookup = {}
    if os.path.exists(csv_path) is False:
        return lookup

    csv_file = open(csv_path, "r", encoding="utf-8")
    reader = csv.DictReader(csv_file)
    for row in reader:
        gap = parse_coating_gap(row["original_file_name"])
        stem = row["file_name"]
        if stem.endswith(".jpg"):
            stem = stem[:-4]
        lookup[stem] = gap
    csv_file.close()
    return lookup


# ===============================================================
# 박스 추출
# ===============================================================

def apply_scale(box, scale, image_width, image_height, min_side):
    """
    박스를 중심 기준으로 확대한다.
    이미지 경계를 벗어나면 잘라낸다.
    """
    x_min = box[0]
    y_min = box[1]
    x_max = box[2]
    y_max = box[3]

    center_x = (x_min + x_max) / 2.0
    center_y = (y_min + y_max) / 2.0
    width = (x_max - x_min) * scale
    height = (y_max - y_min) * scale

    if width < min_side:
        width = min_side
    if height < min_side:
        height = min_side

    new_x_min = center_x - width / 2.0
    new_y_min = center_y - height / 2.0
    new_x_max = center_x + width / 2.0
    new_y_max = center_y + height / 2.0

    if new_x_min < 0:
        new_x_min = 0
    if new_y_min < 0:
        new_y_min = 0
    if new_x_max > image_width:
        new_x_max = image_width
    if new_y_max > image_height:
        new_y_max = image_height

    return (new_x_min, new_y_min, new_x_max, new_y_max)


def extract_boxes_whole(binary_mask, min_area):
    contours, _ = cv2.findContours(
        binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    boxes = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        boxes.append((float(x), float(y), float(x + w), float(y + h)))
    return boxes


def extract_boxes_segmented(binary_mask, min_area, segment_length):
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
            boxes.append((float(x), float(y), float(x + w), float(y + h)))
            continue

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

            tight_y_start = float(seg_y_start + int(row_indices[0]))
            tight_y_end = float(seg_y_start + int(row_indices[-1]) + 1)
            tight_x_start = float(seg_x_start + int(column_indices[0]))
            tight_x_end = float(seg_x_start + int(column_indices[-1]) + 1)

            boxes.append((tight_x_start, tight_y_start, tight_x_end, tight_y_end))
            segment_index = segment_index + 1

    return boxes


def extract_class_boxes(mask_array, class_index, crack_mode):
    """한 클래스의 박스를 추출하고 스케일을 적용한다."""
    binary = (mask_array[:, :, class_index] > 127).astype(np.uint8)
    if np.sum(binary) == 0:
        return []

    if class_index == 0 and crack_mode == "segment":
        raw_boxes = extract_boxes_segmented(binary, MIN_AREA[class_index], CRACK_SEGMENT_LENGTH)
    else:
        raw_boxes = extract_boxes_whole(binary, MIN_AREA[class_index])

    scale = BOX_SCALE[class_index]
    scaled_boxes = []
    for box in raw_boxes:
        scaled_boxes.append(
            apply_scale(box, scale, IMAGE_WIDTH, IMAGE_HEIGHT, MIN_BOX_SIDE)
        )
    return scaled_boxes


def box_to_yolo(box):
    width = (box[2] - box[0]) / float(IMAGE_WIDTH)
    height = (box[3] - box[1]) / float(IMAGE_HEIGHT)
    x_center = (box[0] + box[2]) / 2.0 / float(IMAGE_WIDTH)
    y_center = (box[1] + box[3]) / 2.0 / float(IMAGE_HEIGHT)
    return (x_center, y_center, width, height)


# ===============================================================
# 검증 (중심 거리 기반)
# ===============================================================

def load_existing_pinhole_boxes(label_dir):
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
            if len(parts) < 5 or parts[0] != "0":
                continue
            x_center = float(parts[1]) * IMAGE_WIDTH
            y_center = float(parts[2]) * IMAGE_HEIGHT
            width = float(parts[3]) * IMAGE_WIDTH
            height = float(parts[4]) * IMAGE_HEIGHT
            boxes.append((x_center - width / 2.0, y_center - height / 2.0,
                          x_center + width / 2.0, y_center + height / 2.0))
        if len(boxes) > 0:
            lookup[stem] = boxes
    return lookup


def compute_iou(box_a, box_b):
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


def box_center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def validate_by_center_distance(mask_paths, existing_boxes, distance_threshold):
    """
    IoU 가 아니라 중심 거리로 검증한다.
    알고 싶은 것은 "저자 박스와 마스크 결함이 같은 것을 가리키는가"이며,
    그것은 중심의 근접성으로 판단해야 한다.
    """
    print("=" * 82)
    print("검증: 중심 거리 기반 (IoU 아님)")
    print("=" * 82)
    print()
    print("12 진단에서 박스 크기 관례가 4배 다름이 확인되었다.")
    print("크기가 4배 다르면 중심이 완벽히 일치해도 IoU 상한이 0.25 다.")
    print("따라서 중심 거리로 대응 여부를 판단하고, IoU 는 보조 지표로 본다.")
    print()

    total_existing = 0
    total_generated = 0
    matched = 0
    distances = []
    iou_values = []

    checked = 0
    for path in mask_paths:
        stem = os.path.basename(path)
        if stem.endswith(".png"):
            stem = stem[:-4]
        if stem not in existing_boxes:
            continue

        mask_array = np.array(Image.open(path))
        generated = extract_class_boxes(mask_array, 2, "whole")

        author_boxes = existing_boxes[stem]
        total_existing = total_existing + len(author_boxes)
        total_generated = total_generated + len(generated)

        used = set()
        for author_box in author_boxes:
            author_center = box_center(author_box)

            best_distance = None
            best_index = -1
            index = 0
            while index < len(generated):
                if index in used:
                    index = index + 1
                    continue
                generated_center = box_center(generated[index])
                dx = author_center[0] - generated_center[0]
                dy = author_center[1] - generated_center[1]
                distance = np.sqrt(dx * dx + dy * dy)
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    best_index = index
                index = index + 1

            if best_index >= 0 and best_distance <= distance_threshold:
                matched = matched + 1
                used.add(best_index)
                distances.append(float(best_distance))
                iou_values.append(compute_iou(author_box, generated[best_index]))

        checked = checked + 1

    print("대조 이미지:", checked, "장")
    print("저자 라벨 박스:", total_existing)
    print("자동 생성 박스:", total_generated)
    print("매칭 (중심거리 <=", distance_threshold, "px):", matched)
    print()

    recall = 0.0
    if total_existing > 0:
        recall = matched / float(total_existing)
    precision = 0.0
    if total_generated > 0:
        precision = matched / float(total_generated)

    print("재현율:", round(recall, 4), "(저자 라벨 중 자동 생성이 찾은 비율)")
    print("정밀도:", round(precision, 4), "(자동 생성 중 저자 라벨과 대응한 비율)")

    if len(distances) > 0:
        array = np.array(distances)
        print("중심 거리: 중앙값", round(float(np.median(array)), 2), "px",
              "| p90", round(float(np.percentile(array, 90)), 2), "px")
    if len(iou_values) > 0:
        array = np.array(iou_values)
        print("패딩 적용 후 IoU: 중앙값", round(float(np.median(array)), 4),
              "| 평균", round(float(np.mean(array)), 4))
    print()

    if recall >= 0.8:
        print("판정: 변환 로직 타당. 저자 라벨을 대부분 재현한다.")
    elif recall >= 0.6:
        print("판정: 대체로 일치. 미검출 원인 확인 권장.")
    else:
        print("판정: 재검토 필요.")
    print()

    return recall, precision


# ===============================================================
# 생성
# ===============================================================

def generate_labels(mask_paths, crack_mode, gap_lookup, write_mode):
    print("=" * 82)
    print("라벨 생성 (crack_mode =", crack_mode, ")")
    print("=" * 82)
    print()

    all_labels = {}

    class_counts = {}
    class_images = {}
    class_areas = {}
    for class_index in range(3):
        class_counts[class_index] = 0
        class_images[class_index] = 0
        class_areas[class_index] = []

    processed = 0
    for path in mask_paths:
        stem = os.path.basename(path)
        if stem.endswith(".png"):
            stem = stem[:-4]

        mask_array = np.array(Image.open(path))
        lines = []

        for class_index in range(3):
            boxes = extract_class_boxes(mask_array, class_index, crack_mode)
            if len(boxes) > 0:
                class_images[class_index] = class_images[class_index] + 1

            for box in boxes:
                yolo_box = box_to_yolo(box)
                area = (box[2] - box[0]) * (box[3] - box[1])
                class_counts[class_index] = class_counts[class_index] + 1
                class_areas[class_index].append(area)

                lines.append(str(class_index) + " "
                             + str(round(yolo_box[0], 6)) + " "
                             + str(round(yolo_box[1], 6)) + " "
                             + str(round(yolo_box[2], 6)) + " "
                             + str(round(yolo_box[3], 6)))

        all_labels[stem] = lines
        processed = processed + 1
        if processed % 500 == 0:
            print("  처리 중:", processed, "/", len(mask_paths))

    print("  완료:", processed, "장")
    print()

    print("class".ljust(18) + "images".ljust(9) + "boxes".ljust(9)
          + "box/img".ljust(10) + "area_median")
    for class_index in range(3):
        if class_counts[class_index] == 0:
            continue
        areas = np.array(class_areas[class_index])
        per_image = class_counts[class_index] / float(class_images[class_index])
        line = CLASS_NAMES[class_index].ljust(18)
        line = line + str(class_images[class_index]).ljust(9)
        line = line + str(class_counts[class_index]).ljust(9)
        line = line + str(round(per_image, 2)).ljust(10)
        line = line + str(round(float(np.median(areas)), 1))
        print(line)
    print()

    return all_labels


def build_dataset(all_labels, gap_lookup, output_root):
    """갭별 층화 분할로 YOLO 데이터셋 디렉토리를 구성한다."""
    print("=" * 82)
    print("데이터셋 구성")
    print("=" * 82)
    print()

    gap_groups = {}
    for stem in all_labels:
        gap = gap_lookup.get(stem)
        if gap is None:
            gap = 0
        if gap not in gap_groups:
            gap_groups[gap] = []
        gap_groups[gap].append(stem)

    assignment = {}
    random_generator = random.Random(RANDOM_SEED)

    for gap in sorted(gap_groups.keys()):
        stems = sorted(gap_groups[gap])
        random_generator.shuffle(stems)
        total = len(stems)
        train_end = int(total * SPLIT_RATIOS["train"])
        val_end = train_end + int(total * SPLIT_RATIOS["val"])

        index = 0
        while index < total:
            if index < train_end:
                assignment[stems[index]] = "train"
            elif index < val_end:
                assignment[stems[index]] = "val"
            else:
                assignment[stems[index]] = "test"
            index = index + 1

    for split_name in ["train", "val", "test"]:
        image_dir = os.path.join(output_root, "images", split_name)
        label_dir = os.path.join(output_root, "labels", split_name)
        if os.path.exists(image_dir) is False:
            os.makedirs(image_dir)
        if os.path.exists(label_dir) is False:
            os.makedirs(label_dir)

    counts = {"train": 0, "val": 0, "test": 0}
    box_counts = {"train": 0, "val": 0, "test": 0}

    for stem in sorted(all_labels.keys()):
        split_name = assignment[stem]

        source_image = os.path.join(IMAGE_DIR, stem + ".jpg")
        if os.path.exists(source_image) is False:
            continue

        target_image = os.path.join(output_root, "images", split_name, stem + ".jpg")
        shutil.copy(source_image, target_image)

        label_path = os.path.join(output_root, "labels", split_name, stem + ".txt")
        label_file = open(label_path, "w")
        for line in all_labels[stem]:
            label_file.write(line + "\n")
        label_file.close()

        counts[split_name] = counts[split_name] + 1
        box_counts[split_name] = box_counts[split_name] + len(all_labels[stem])

    print("split".ljust(10) + "images".ljust(10) + "boxes")
    for split_name in ["train", "val", "test"]:
        print(split_name.ljust(10) + str(counts[split_name]).ljust(10)
              + str(box_counts[split_name]))
    print()

    yaml_lines = []
    yaml_lines.append("path: " + os.path.abspath(output_root))
    yaml_lines.append("train: images/train")
    yaml_lines.append("val: images/val")
    yaml_lines.append("test: images/test")
    yaml_lines.append("")
    yaml_lines.append("names:")
    for class_index in range(3):
        yaml_lines.append("  " + str(class_index) + ": " + CLASS_NAMES[class_index])

    yaml_path = os.path.join(output_root, "data.yaml")
    yaml_file = open(yaml_path, "w")
    for line in yaml_lines:
        yaml_file.write(line + "\n")
    yaml_file.close()

    print("data.yaml:", yaml_path)
    print()


def main():
    if HAS_CV2 is False:
        print("cv2 가 필요합니다.")
        return
    if os.path.exists(MASK_DIR) is False:
        print("마스크 폴더를 찾을 수 없습니다:", MASK_DIR)
        return

    write_mode = "--write" in sys.argv

    mask_paths = sorted(glob.glob(MASK_DIR + "/*.png"))
    print("마스크:", len(mask_paths), "장")
    print("박스 확대 배율:", BOX_SCALE)
    print("모드:", "실제 생성" if write_mode else "dry-run")
    print()

    existing_boxes = load_existing_pinhole_boxes(EXISTING_LABEL_DIR)
    validate_by_center_distance(mask_paths, existing_boxes, 20.0)

    gap_lookup = load_gap_lookup(LABELS_CSV)
    all_labels = generate_labels(mask_paths, "segment", gap_lookup, write_mode)

    if write_mode is True:
        build_dataset(all_labels, gap_lookup, OUTPUT_ROOT)
        print("생성 완료. 학습 명령:")
        print("  yolo detect train data=" + os.path.abspath(OUTPUT_ROOT)
              + "/data.yaml model=yolov8n.pt epochs=50 imgsz=640")
    else:
        print("dry-run 종료. 검증이 통과하면 --write 로 실행하세요.")


main()
