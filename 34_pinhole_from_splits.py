"""
34. 마스크 → YOLO detection 라벨 (프레임 단위 splits.csv 기반)

배경 (§18-12):
  13_mask_to_detection_v2.py 는 patch 단위 무작위 shuffle 로 분할해
  원본 프레임의 49.6% 가 여러 split 에 걸쳤다. 프레임 단위 분할로
  재구성해 val 마다 82% 원본 공유 문제를 해소한다.

원칙:
  - 13 원본 스크립트는 수정하지 않는다 (importlib 로 함수만 재사용)
  - 마스크 → bbox 변환 로직 (extract_class_boxes 등) 은 13 그대로 사용
  - box_scale·min_area·crack segment length 등 임계값도 13 그대로 사용
  - 분할만 splits.csv (32 산출) 로 대체
  - 출력: data/detection_auto_frames/ (기존 data/detection_auto 유지)

산출:
  - data/detection_auto_frames/images/{train,val,test}/*.jpg
  - data/detection_auto_frames/labels/{train,val,test}/*.txt
  - data/detection_auto_frames/data.yaml

검증:
  - 같은 원본 프레임이 두 split 에 걸친 경우가 0 인지 확인
"""

import csv
import glob
import importlib.util
import os
import re
import shutil
import sys
from collections import defaultdict


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
V2_PATH = os.path.join(PROJECT_ROOT, "13_mask_to_detection_v2.py")

SPLITS_CSV = "splits.csv"
LABELS_CSV = "classification/labels.csv"
MASK_DIR = "segmentation/masks"
IMAGE_DIR = "segmentation/images"
OUTPUT_ROOT = "data/detection_auto_frames"


# ===============================================================
# 13 모듈 import
# ===============================================================

def load_v2_module():
    spec = importlib.util.spec_from_file_location("v2_module", V2_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ===============================================================
# splits.csv 로딩
# ===============================================================

def load_splits(path):
    """stem -> split"""
    result = {}
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            result[row["stem"]] = row["split"]
    return result


def parse_frame_key(name):
    """original_file_name → (run_id, coating_gap, position, frame_number)"""
    m = re.match(r"^(R\d+)-?(\d+)um[-_](.+?)_frame_(\d+)", name)
    if not m:
        return None
    return (m.group(1), int(m.group(2)), m.group(3), int(m.group(4)))


def load_stem_to_frame(labels_csv):
    result = {}
    with open(labels_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stem = row["file_name"]
            if stem.endswith(".jpg"):
                stem = stem[:-4]
            key = parse_frame_key(row["original_file_name"])
            if key is not None:
                result[stem] = key
    return result


# ===============================================================
# 프레임 단위 분할 검증
# ===============================================================

def verify_frame_split(assignment, stem_to_frame):
    """
    같은 원본 프레임의 형제 patch 가 여러 split 에 걸쳤는지 확인.
    걸침이 있으면 오류.
    """
    frame_to_splits = defaultdict(set)
    for stem, split in assignment.items():
        frame = stem_to_frame.get(stem)
        if frame is None:
            continue
        frame_to_splits[frame].add(split)

    shared = 0
    examples = []
    for frame, splits in frame_to_splits.items():
        if len(splits) > 1:
            shared += 1
            if len(examples) < 5:
                examples.append((frame, sorted(splits)))
    return len(frame_to_splits), shared, examples


# ===============================================================
# 데이터셋 구성 (13 의 build_dataset 을 참조하되 splits.csv 사용)
# ===============================================================

def build_from_splits(all_labels, assignment, output_root, class_names):
    """
    splits.csv 에서 미리 정해진 배정을 사용해 파일을 배치한다.
    13.build_dataset 의 파일 복사·라벨 저장·yaml 작성 부분과 동일.
    """
    for split_name in ["train", "val", "test"]:
        image_dir = os.path.join(output_root, "images", split_name)
        label_dir = os.path.join(output_root, "labels", split_name)
        os.makedirs(image_dir, exist_ok=True)
        os.makedirs(label_dir, exist_ok=True)

    counts = {"train": 0, "val": 0, "test": 0}
    box_counts = {"train": 0, "val": 0, "test": 0}
    missing = 0

    for stem in sorted(all_labels.keys()):
        if stem not in assignment:
            missing += 1
            continue
        split_name = assignment[stem]
        source_image = os.path.join(IMAGE_DIR, stem + ".jpg")
        if os.path.exists(source_image) is False:
            continue

        target_image = os.path.join(output_root, "images", split_name, stem + ".jpg")
        shutil.copy(source_image, target_image)

        label_path = os.path.join(output_root, "labels", split_name, stem + ".txt")
        with open(label_path, "w") as label_file:
            for line in all_labels[stem]:
                label_file.write(line + "\n")

        counts[split_name] += 1
        box_counts[split_name] += len(all_labels[stem])

    print()
    print("split".ljust(10) + "images".ljust(10) + "boxes")
    for split_name in ["train", "val", "test"]:
        print(split_name.ljust(10) + str(counts[split_name]).ljust(10)
              + str(box_counts[split_name]))
    if missing > 0:
        print("  (splits.csv 에 없는 stem: {})".format(missing))
    print()

    # data.yaml
    yaml_lines = []
    yaml_lines.append("path: " + os.path.abspath(output_root))
    yaml_lines.append("train: images/train")
    yaml_lines.append("val: images/val")
    yaml_lines.append("test: images/test")
    yaml_lines.append("")
    yaml_lines.append("names:")
    for i in range(len(class_names)):
        yaml_lines.append("  " + str(i) + ": " + class_names[i])
    yaml_path = os.path.join(output_root, "data.yaml")
    with open(yaml_path, "w") as f:
        for line in yaml_lines:
            f.write(line + "\n")
    print("data.yaml:", yaml_path)
    print()

    return counts, box_counts


# ===============================================================
# 메인
# ===============================================================

def main():
    if os.path.exists(OUTPUT_ROOT):
        print("이미 존재:", OUTPUT_ROOT, "— 재실행 전 옮길 것.")
        sys.exit(1)

    if os.path.exists(SPLITS_CSV) is False:
        print(SPLITS_CSV, "없음. 32 를 먼저 실행할 것.")
        sys.exit(1)

    write_mode = "--write" in sys.argv

    print("=" * 82)
    print("34. 마스크 → YOLO det 라벨 (프레임 단위 splits.csv 기반)")
    print("=" * 82)

    # 13 모듈 로드
    v2 = load_v2_module()
    if v2.HAS_CV2 is False:
        print("cv2 가 필요합니다.")
        return

    print("13 모듈 로드 완료. box_scale =", v2.BOX_SCALE, ", min_area =", v2.MIN_AREA)
    print()

    # splits.csv 및 원본 프레임 매핑
    assignment = load_splits(SPLITS_CSV)
    stem_to_frame = load_stem_to_frame(LABELS_CSV)

    n_frames, shared, examples = verify_frame_split(assignment, stem_to_frame)
    print("splits.csv 검증:")
    print("  unique 원본 프레임: {}".format(n_frames))
    print("  여러 split 에 걸친 프레임: {}".format(shared))
    if shared > 0:
        print("  경고 (있으면 안 됨):")
        for ex in examples:
            print("    ", ex)
        sys.exit(1)
    print("  프레임 누수 0 — 통과")
    print()

    # 마스크 스캔
    mask_paths = sorted(glob.glob(MASK_DIR + "/*.png"))
    print("마스크:", len(mask_paths), "장")

    existing_boxes = v2.load_existing_pinhole_boxes(v2.EXISTING_LABEL_DIR)
    v2.validate_by_center_distance(mask_paths, existing_boxes, 20.0)

    gap_lookup = v2.load_gap_lookup(v2.LABELS_CSV)
    all_labels = v2.generate_labels(mask_paths, "segment", gap_lookup, write_mode)

    print()
    if write_mode is True:
        counts, box_counts = build_from_splits(all_labels, assignment, OUTPUT_ROOT, v2.CLASS_NAMES)

        # 최종 검증: 새 데이터셋에서도 프레임 누수 0 확인
        actual_assignment = {}
        for split_name in ["train", "val", "test"]:
            img_dir = os.path.join(OUTPUT_ROOT, "images", split_name)
            for f in os.listdir(img_dir):
                if f.endswith(".jpg"):
                    actual_assignment[f[:-4]] = split_name
        n_f2, shared2, ex2 = verify_frame_split(actual_assignment, stem_to_frame)
        print("최종 데이터셋 검증: unique 프레임 {} / 누수 {}".format(n_f2, shared2))
        if shared2 == 0:
            print("  프레임 누수 0 — 통과")
        else:
            print("  ⚠ 프레임 누수 발생. 재확인 필요")
            for ex in ex2:
                print("    ", ex)
            sys.exit(1)
        print()
        print("다음 단계: 14_pinhole_dataset.py 를 이 경로로 재실행")
        print("  python3 14_pinhole_dataset.py --source", OUTPUT_ROOT,
              "--out data/pinhole_frames --mode balanced")
    else:
        print("dry-run 종료. --write 로 실제 생성.")


if __name__ == "__main__":
    main()
