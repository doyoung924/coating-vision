"""
38. 데이터량 대조 실험용 다운샘플 (train~610 / val~124)

배경 (§3):
  §2 결과 val mAP 0.924 → 0.978. 분할 효과와 데이터량 효과가 섞임.
  프레임 단위 유지로 데이터량만 610/124 근접으로 축소해 재학습.

방법 (단순화):
  - 소스는 data/pinhole_frames_balanced (이미 balanced 처리됨).
  - 각 split 의 train/val 을 프레임 단위 그룹핑 후 seed 42 shuffle.
  - 프레임을 순서대로 추가하며 누적 patch 수가 목표(610/124) 를
    초과하는 시점 직전까지만 선택. balanced 성질은 프레임 안 patch 구성이
    이미 확정된 상태이므로 근사 유지.
  - test 는 R7/700 전체 유지 (38 patch).

산출:
  - data/pinhole_frames_small_balanced/  (기존 pinhole_frames_balanced 유지)

원칙: 새 로직 없음. 프레임 단위 재선별만.
"""

import csv
import glob
import os
import random
import re
import shutil
import sys
from collections import defaultdict


SRC_ROOT = "data/pinhole_frames_balanced"
DST_ROOT = "data/pinhole_frames_small_balanced"
LABELS_CSV = "classification/labels.csv"

TARGET = {"train": 610, "val": 124, "test": None}   # test 는 그대로
SEED = 42


def stem_to_frame(labels_csv):
    result = {}
    for row in csv.DictReader(open(labels_csv, "r", encoding="utf-8")):
        stem = row["file_name"]
        if stem.endswith(".jpg"): stem = stem[:-4]
        m = re.match(r"^(R\d+)-?(\d+)um[-_](.+?)_frame_(\d+)", row["original_file_name"])
        if m:
            result[stem] = (m.group(1), int(m.group(2)), m.group(3), int(m.group(4)))
    return result


def main():
    if os.path.exists(DST_ROOT):
        print("이미 존재:", DST_ROOT, "— 재실행 전 옮길 것.")
        sys.exit(1)

    print("=" * 82)
    print("38. 데이터량 대조 다운샘플 (프레임 단위)")
    print("=" * 82)

    frame_lookup = stem_to_frame(LABELS_CSV)

    for split in ["train", "val", "test"]:
        os.makedirs(os.path.join(DST_ROOT, "images", split), exist_ok=True)
        os.makedirs(os.path.join(DST_ROOT, "labels", split), exist_ok=True)

    counts = {}
    for split in ["train", "val", "test"]:
        img_dir = os.path.join(SRC_ROOT, "images", split)
        lab_dir = os.path.join(SRC_ROOT, "labels", split)
        stems = sorted(f[:-4] for f in os.listdir(img_dir) if f.endswith(".jpg"))

        # 프레임 단위 그룹핑
        by_frame = defaultdict(list)
        for s in stems:
            fk = frame_lookup.get(s)
            if fk is None:
                # 프레임 정보 없는 stem 은 별도로 놓음 (있으면 안 됨)
                by_frame[("_none_", 0, "_", -1)].append(s)
            else:
                by_frame[fk].append(s)

        frames = sorted(by_frame.keys())
        rng = random.Random(SEED + hash(split))   # split 별 별도 shuffle
        rng.shuffle(frames)

        target = TARGET[split]
        kept = []
        if target is None:
            for fk in frames:
                kept.extend(by_frame[fk])
        else:
            for fk in frames:
                batch = by_frame[fk]
                if len(kept) + len(batch) > target:
                    continue   # 이 프레임은 스킵 (누적 초과 방지)
                kept.extend(batch)
                if len(kept) >= target * 0.99:
                    break

        # 복사
        for s in kept:
            src_img = os.path.join(img_dir, s + ".jpg")
            dst_img = os.path.join(DST_ROOT, "images", split, s + ".jpg")
            shutil.copy(src_img, dst_img)
            src_lab = os.path.join(lab_dir, s + ".txt")
            if os.path.exists(src_lab):
                dst_lab = os.path.join(DST_ROOT, "labels", split, s + ".txt")
                shutil.copy(src_lab, dst_lab)

        # 통계
        n_pinhole = 0
        n_bg = 0
        for s in kept:
            lab_path = os.path.join(DST_ROOT, "labels", split, s + ".txt")
            if os.path.exists(lab_path) and os.path.getsize(lab_path) > 0:
                n_pinhole += 1
            else:
                n_bg += 1
        counts[split] = {"total": len(kept), "pinhole": n_pinhole, "bg": n_bg}

    # data.yaml
    yaml_lines = [
        "path: " + os.path.abspath(DST_ROOT),
        "train: images/train",
        "val: images/val",
        "test: images/test",
        "",
        "names:",
        "  0: pinhole",
    ]
    with open(os.path.join(DST_ROOT, "data.yaml"), "w") as f:
        for line in yaml_lines:
            f.write(line + "\n")

    print()
    print("{:<8}{:>10}{:>12}{:>12}".format("split", "total", "pinhole", "bg"))
    for split in ["train", "val", "test"]:
        c = counts[split]
        print("{:<8}{:>10}{:>12}{:>12}".format(split, c["total"], c["pinhole"], c["bg"]))
    print()
    print("저장:", DST_ROOT)


main()
