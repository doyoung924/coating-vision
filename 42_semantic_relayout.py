"""
42. data/semantic 재배치 — 3-split → 4-split (test_in 신설 반영)

배경:
  39 는 splits.csv 3-split (train/val/test) 을 기반으로 data/semantic 을 만들었다.
  41 재분할로 splits.csv 가 4-split (train/val/test_in/test_out) 이 되었으므로
  data/semantic 을 이에 맞춰 재배치한다.

방법:
  1. data/semantic/test → data/semantic/test_out 리네임 (기존 test = R7/700 그대로)
  2. splits.csv 에서 test_in 배정 stem 목록 조회
  3. train/{images,masks} 에서 test_in stem 을 test_in/{images,masks} 로 이동
  4. 4 split 파일 수를 splits.csv 기대치와 대조 검증
"""

import csv
import os
import shutil
import sys
from collections import defaultdict


SEMANTIC_ROOT = "data/semantic"
SPLITS_CSV = "splits.csv"


def load_splits():
    result = {}
    with open(SPLITS_CSV) as f:
        for r in csv.DictReader(f):
            result[r["stem"]] = r["split"]
    return result


def main():
    if not os.path.exists(SEMANTIC_ROOT):
        print("data/semantic 없음. 39 를 먼저 실행할 것.")
        sys.exit(1)

    assignment = load_splits()

    print("=" * 82)
    print("42. data/semantic 재배치 (3-split → 4-split)")
    print("=" * 82)

    # 1. test → test_out (있으면 리네임)
    src_test = os.path.join(SEMANTIC_ROOT, "test")
    dst_test_out = os.path.join(SEMANTIC_ROOT, "test_out")
    if os.path.exists(src_test) and not os.path.exists(dst_test_out):
        os.rename(src_test, dst_test_out)
        print(f"리네임: {src_test} → {dst_test_out}")
    elif os.path.exists(dst_test_out):
        print(f"이미 존재: {dst_test_out}")
    else:
        print(f"⚠ 원본 test 폴더 없음: {src_test}")

    # 2. test_in 폴더 준비
    test_in_root = os.path.join(SEMANTIC_ROOT, "test_in")
    if os.path.exists(test_in_root):
        print(f"⚠ 이미 존재: {test_in_root}. 재실행 여부 확인 필요")
        sys.exit(1)
    os.makedirs(os.path.join(test_in_root, "images"))
    os.makedirs(os.path.join(test_in_root, "masks"))
    print(f"신설: {test_in_root}/{{images,masks}}")

    # 3. train → test_in 이동
    test_in_stems = [s for s, sp in assignment.items() if sp == "test_in"]
    print(f"이동 대상 (splits.csv 상 test_in): {len(test_in_stems)}")

    moved_img = 0
    moved_mask = 0
    missing = 0
    for stem in test_in_stems:
        src_img = os.path.join(SEMANTIC_ROOT, "train", "images", stem + ".jpg")
        dst_img = os.path.join(test_in_root, "images", stem + ".jpg")
        src_mask = os.path.join(SEMANTIC_ROOT, "train", "masks", stem + ".png")
        dst_mask = os.path.join(test_in_root, "masks", stem + ".png")

        if os.path.exists(src_img):
            shutil.move(src_img, dst_img)
            moved_img += 1
        else:
            missing += 1
        if os.path.exists(src_mask):
            shutil.move(src_mask, dst_mask)
            moved_mask += 1

    print(f"이동 완료: 이미지 {moved_img}, 마스크 {moved_mask} (누락 {missing})")
    print()

    # 4. 검증
    print("=" * 82)
    print("검증: 4 split 파일 수 vs splits.csv 기대값")
    print("=" * 82)
    expected = defaultdict(int)
    for stem, sp in assignment.items():
        expected[sp] += 1

    for split in ["train", "val", "test_in", "test_out"]:
        img_dir = os.path.join(SEMANTIC_ROOT, split, "images")
        mask_dir = os.path.join(SEMANTIC_ROOT, split, "masks")
        n_img = len(os.listdir(img_dir)) if os.path.exists(img_dir) else 0
        n_mask = len(os.listdir(mask_dir)) if os.path.exists(mask_dir) else 0
        exp = expected[split]
        ok = (n_img == n_mask == exp)
        mark = "OK" if ok else "⚠"
        print(f"  {split:<10}  images {n_img:>5} / masks {n_mask:>5} / 기대 {exp:>5}  [{mark}]")


main()
