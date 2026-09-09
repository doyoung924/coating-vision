"""
14. 핀홀 단일 클래스 데이터셋 생성

배경:
  11 학습 결과 클래스별 성격이 완전히 달랐다.

  | class | test mAP@0.5 | box/img | 판정 |
  | pinhole | 0.858 | 1.07 | 검출에 적합 |
  | surface_crack | 0.753 | 24.46 | 검출 부적합 - 예측 이미지가 박스로 뒤덮임 |
  | delamination | 0.399 | 3.24 | 데이터 부족 (478 박스) |

  크랙은 개수를 세는 대상이 아니다. "크랙이 몇 개인가"는 의미가 없고
  "얼마나 퍼져 있는가"가 실무 질문이며, 09 SPC 가 이미 이상 패치 비율로 그것을 다룬다.

  검출기가 실제로 필요한 대상은 핀홀이다.
    - 개별 객체이고 위치/크기가 의미를 가진다
    - 03 에서 도출한 형태 판별 규칙(종횡비, roundness)의 입력이 된다
    - 저자 baseline 과 동일 조건(핀홀 단일 클래스) 비교가 가능하다

  102:1:1 불균형을 제거하면 성능이 더 오를 가능성이 있다.

배경 이미지 비율 설계:
  핀홀이 없는 이미지를 학습에 넣으면 오탐이 줄지만 학습 시간이 늘고
  양성 샘플 비율이 떨어진다.

  세 가지 구성을 만들어 비교할 수 있게 한다.
    positive_only : 핀홀 있는 이미지만 (저자 조건에 가장 가까움)
    balanced      : 핀홀 있는 이미지 + 같은 수의 배경 이미지
    all           : 전체 2,227장

사용법:
  python3 14_pinhole_dataset.py                      # dry-run
  python3 14_pinhole_dataset.py --write              # balanced 생성
  python3 14_pinhole_dataset.py --write --mode all   # 전체 생성
"""

import csv
import os
import sys
import glob
import random
import shutil
import numpy as np


SOURCE_ROOT = "data/detection_auto"
LABELS_CSV = "classification/labels.csv"

PINHOLE_CLASS_INDEX = 2      # 원본 3클래스에서의 인덱스
SPLIT_NAMES = ["train", "val", "test"]

RANDOM_SEED = 42


# ===============================================================
# 원본 라벨 읽기
# ===============================================================

def read_label_file(path):
    """YOLO txt 를 읽어 (class_index, x, y, w, h) 리스트로 반환."""
    entries = []
    text_file = open(path, "r")
    for line in text_file.readlines():
        stripped = line.strip()
        if len(stripped) == 0:
            continue
        parts = stripped.split()
        if len(parts) < 5:
            continue
        entries.append((int(parts[0]), parts[1], parts[2], parts[3], parts[4]))
    text_file.close()
    return entries


def scan_source(source_root):
    """
    분할별로 각 이미지의 핀홀 박스 수를 센다.
    반환: {split: [(stem, pinhole_boxes), ...]}
    """
    result = {}

    for split_name in SPLIT_NAMES:
        label_dir = os.path.join(source_root, "labels", split_name)
        paths = sorted(glob.glob(label_dir + "/*.txt"))

        records = []
        for path in paths:
            stem = os.path.basename(path)
            if stem.endswith(".txt"):
                stem = stem[:-4]

            entries = read_label_file(path)
            pinhole_entries = []
            for entry in entries:
                if entry[0] == PINHOLE_CLASS_INDEX:
                    pinhole_entries.append(entry)

            records.append((stem, pinhole_entries))

        result[split_name] = records

    return result


def report_scan(scanned):
    print("=" * 76)
    print("원본 스캔")
    print("=" * 76)
    print()
    print("split".ljust(9) + "총이미지".ljust(11) + "핀홀有".ljust(10)
          + "핀홀박스".ljust(11) + "배경이미지")

    for split_name in SPLIT_NAMES:
        records = scanned[split_name]
        total_images = len(records)

        positive_images = 0
        total_boxes = 0
        for stem, entries in records:
            if len(entries) > 0:
                positive_images = positive_images + 1
                total_boxes = total_boxes + len(entries)

        background_images = total_images - positive_images

        line = split_name.ljust(9)
        line = line + str(total_images).ljust(11)
        line = line + str(positive_images).ljust(10)
        line = line + str(total_boxes).ljust(11)
        line = line + str(background_images)
        print(line)
    print()


# ===============================================================
# 구성별 이미지 선택
# ===============================================================

def select_images(records, mode, seed):
    """
    mode 에 따라 사용할 이미지를 고른다.
      positive_only : 핀홀 있는 것만
      balanced      : 핀홀 있는 것 + 같은 수의 배경
      all           : 전부
    """
    positive = []
    background = []
    for stem, entries in records:
        if len(entries) > 0:
            positive.append((stem, entries))
        else:
            background.append((stem, entries))

    if mode == "positive_only":
        return positive

    if mode == "all":
        selected = []
        for item in positive:
            selected.append(item)
        for item in background:
            selected.append(item)
        return selected

    # balanced
    random_generator = random.Random(seed)
    shuffled = list(background)
    random_generator.shuffle(shuffled)

    take = min(len(positive), len(shuffled))
    selected = []
    for item in positive:
        selected.append(item)
    index = 0
    while index < take:
        selected.append(shuffled[index])
        index = index + 1

    return selected


def report_selection(scanned, mode, seed):
    print("=" * 76)
    print("구성:", mode)
    print("=" * 76)
    print()
    print("split".ljust(9) + "이미지".ljust(10) + "핀홀박스".ljust(11)
          + "양성비율".ljust(11) + "박스/양성이미지")

    totals = {"images": 0, "boxes": 0}

    for split_name in SPLIT_NAMES:
        selected = select_images(scanned[split_name], mode, seed)

        total_boxes = 0
        positive_images = 0
        for stem, entries in selected:
            total_boxes = total_boxes + len(entries)
            if len(entries) > 0:
                positive_images = positive_images + 1

        positive_ratio = 0.0
        if len(selected) > 0:
            positive_ratio = positive_images / float(len(selected))

        boxes_per_positive = 0.0
        if positive_images > 0:
            boxes_per_positive = total_boxes / float(positive_images)

        line = split_name.ljust(9)
        line = line + str(len(selected)).ljust(10)
        line = line + str(total_boxes).ljust(11)
        line = line + str(round(positive_ratio * 100, 1)).ljust(11)
        line = line + str(round(boxes_per_positive, 2))
        print(line)

        totals["images"] = totals["images"] + len(selected)
        totals["boxes"] = totals["boxes"] + total_boxes

    print()
    print("합계: 이미지", totals["images"], "/ 박스", totals["boxes"])
    print()


# ===============================================================
# 생성
# ===============================================================

def build_dataset(scanned, mode, output_root, source_root, seed):
    print("=" * 76)
    print("데이터셋 생성:", output_root)
    print("=" * 76)
    print()

    for split_name in SPLIT_NAMES:
        image_dir = os.path.join(output_root, "images", split_name)
        label_dir = os.path.join(output_root, "labels", split_name)
        if os.path.exists(image_dir) is False:
            os.makedirs(image_dir)
        if os.path.exists(label_dir) is False:
            os.makedirs(label_dir)

    for split_name in SPLIT_NAMES:
        selected = select_images(scanned[split_name], mode, seed)

        written_images = 0
        written_boxes = 0

        for stem, entries in selected:
            source_image = os.path.join(source_root, "images", split_name, stem + ".jpg")
            if os.path.exists(source_image) is False:
                continue

            target_image = os.path.join(output_root, "images", split_name, stem + ".jpg")
            shutil.copy(source_image, target_image)

            label_path = os.path.join(output_root, "labels", split_name, stem + ".txt")
            label_file = open(label_path, "w")
            for entry in entries:
                # 단일 클래스이므로 인덱스를 0 으로 바꾼다
                line = ("0 " + entry[1] + " " + entry[2] + " "
                        + entry[3] + " " + entry[4])
                label_file.write(line + "\n")
                written_boxes = written_boxes + 1
            label_file.close()

            written_images = written_images + 1

        print(split_name.ljust(9) + "이미지 " + str(written_images).ljust(8)
              + "박스 " + str(written_boxes))

    print()

    yaml_lines = []
    yaml_lines.append("path: " + os.path.abspath(output_root))
    yaml_lines.append("train: images/train")
    yaml_lines.append("val: images/val")
    yaml_lines.append("test: images/test")
    yaml_lines.append("")
    yaml_lines.append("names:")
    yaml_lines.append("  0: pinhole")

    yaml_path = os.path.join(output_root, "data.yaml")
    yaml_file = open(yaml_path, "w")
    for line in yaml_lines:
        yaml_file.write(line + "\n")
    yaml_file.close()

    print("data.yaml:", yaml_path)
    print()


# ===============================================================

def main():
    write_mode = "--write" in sys.argv

    mode = "balanced"
    if "--mode" in sys.argv:
        position = sys.argv.index("--mode")
        if position + 1 < len(sys.argv):
            mode = sys.argv[position + 1]

    if mode not in ["positive_only", "balanced", "all"]:
        print("mode 는 positive_only / balanced / all 중 하나여야 합니다.")
        return

    if os.path.exists(SOURCE_ROOT) is False:
        print("원본 데이터셋을 찾을 수 없습니다:", SOURCE_ROOT)
        print("현재 위치:", os.getcwd())
        return

    print("모드:", mode, "|", "실제 생성" if write_mode else "dry-run")
    print()

    scanned = scan_source(SOURCE_ROOT)
    report_scan(scanned)

    # 세 구성을 모두 비교해 보여준다
    for candidate_mode in ["positive_only", "balanced", "all"]:
        report_selection(scanned, candidate_mode, RANDOM_SEED)

    if write_mode is False:
        print("dry-run 종료. --write 로 생성하세요.")
        print("  python3 14_pinhole_dataset.py --write --mode balanced")
        return

    output_root = "data/pinhole_" + mode
    build_dataset(scanned, mode, output_root, SOURCE_ROOT, RANDOM_SEED)

    print("학습 명령:")
    print("  yolo detect train \\")
    print("    data=" + os.path.abspath(output_root) + "/data.yaml \\")
    print("    model=yolov8n.pt \\")
    print("    epochs=100 imgsz=640 batch=16 \\")
    print("    project=runs name=pinhole_" + mode)
    print()
    print("참고: 데이터가 작으므로 epochs 를 100 으로 늘렸습니다.")


main()
