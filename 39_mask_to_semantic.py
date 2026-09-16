"""
39. 마스크 → 시맨틱 라벨 변환 (§18-8 확정 방침)

입력:
  segmentation/masks/*.png  (2,227 장, RGBA 480×640)
  segmentation/images/*.jpg (같은 스템)
  splits.csv                (§18-9 확정)

출력:
  data/semantic/{train,val,test}/images/*.jpg  (원본 복사)
  data/semantic/{train,val,test}/masks/*.png   (단일 채널 uint8)

라벨 매핑 (배경 0 / crack 1 / delam 2):
  - ch0 > 0 → 1 (crack)
  - ch1 > 0 → 2 (delam)
  - ch2 (pinhole)  → 배경으로 처리, 제외 로그
  - ch3 (unclassified) → 배경으로 처리, 제외 로그
  - 겹침 (ch0 & ch1) 검출 시 에러 후 중단 (§18-10 관측 재확인)

방침 (§18):
  - 480×640 그대로. crop·resize 하지 않음. §18-11 참조
  - 겹침 처리 로직 없음. crack ∩ delam = 0 픽셀 사실 재확인 (§18-10)

원칙:
  - 학습·업로드 시작 없음. 라벨 변환만
"""

import csv
import os
import sys
import glob
import shutil
import numpy as np
from PIL import Image
from collections import defaultdict


MASK_DIR = "segmentation/masks"
IMAGE_DIR = "segmentation/images"
SPLITS_CSV = "splits.csv"
OUT_ROOT = "data/semantic"

CLASS_NAMES = {0: "background", 1: "crack", 2: "delam"}


def load_splits(path):
    """stem → split"""
    result = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            result[r["stem"]] = r["split"]
    return result


def convert_mask(mask_rgba):
    """
    RGBA (H, W, 4) → uint8 (H, W).
    배경 0, crack 1, delam 2. ch2/ch3 제외.
    겹침 검출 시 예외.
    """
    ch0 = mask_rgba[..., 0] > 0
    ch1 = mask_rgba[..., 1] > 0
    overlap = ch0 & ch1
    n_overlap = int(overlap.sum())
    if n_overlap > 0:
        raise RuntimeError(f"겹침 픽셀 발견: {n_overlap} 개 (crack ∩ delam)")

    label = np.zeros(mask_rgba.shape[:2], dtype=np.uint8)
    label[ch0] = 1
    label[ch1] = 2
    return label, n_overlap


def main():
    if os.path.exists(OUT_ROOT):
        print("이미 존재:", OUT_ROOT, "— 재실행 전 옮길 것.")
        sys.exit(1)
    if not os.path.exists(SPLITS_CSV):
        print(SPLITS_CSV, "없음. 32 를 먼저 실행할 것.")
        sys.exit(1)

    print("=" * 82)
    print("39. 마스크 → 시맨틱 라벨 변환")
    print("=" * 82)
    print("클래스 매핑: 배경 0 / crack 1 (ch0) / delam 2 (ch1)")
    print("제외: ch2 (pinhole), ch3 (unclassified) — 배경으로 처리")
    print("       핀홀은 YOLO 검출로 별도 처리 (§18-8)")
    print("       unclassified 는 진단 대상 원인 축 미대응 (§18-6)")
    print("480×640 그대로. crop·resize 없음 (§18-11)")
    print()

    assignment = load_splits(SPLITS_CSV)
    print("splits.csv 로드:", len(assignment), "개 stem")

    # 출력 디렉터리 준비
    for split in ["train", "val", "test"]:
        os.makedirs(os.path.join(OUT_ROOT, split, "images"), exist_ok=True)
        os.makedirs(os.path.join(OUT_ROOT, split, "masks"), exist_ok=True)

    # 원본 클래스별 픽셀 카운트 (검증용, 전체)
    original_ch_pixels = {0: 0, 1: 0, 2: 0, 3: 0}
    # 변환 후 클래스별 카운트 (split 별)
    converted_pixels = defaultdict(lambda: {0: 0, 1: 0, 2: 0})
    converted_patch_with_class = defaultdict(lambda: {1: 0, 2: 0})
    converted_counts = defaultdict(int)

    mask_paths = sorted(glob.glob(MASK_DIR + "/*.png"))
    processed = 0
    skipped_no_split = 0
    total_ch2_pixels_dropped = 0
    total_ch3_pixels_dropped = 0

    for mask_path in mask_paths:
        stem = os.path.splitext(os.path.basename(mask_path))[0]
        if stem not in assignment:
            skipped_no_split += 1
            continue
        split = assignment[stem]

        mask_rgba = np.array(Image.open(mask_path))
        if mask_rgba.ndim != 3 or mask_rgba.shape[2] != 4:
            raise RuntimeError(f"예상 RGBA 아님: {mask_path}, shape={mask_rgba.shape}")

        # 원본 채널 픽셀 (검증용)
        for ci in range(4):
            original_ch_pixels[ci] += int((mask_rgba[..., ci] > 0).sum())
        # 제외 채널 카운트
        total_ch2_pixels_dropped += int((mask_rgba[..., 2] > 0).sum())
        total_ch3_pixels_dropped += int((mask_rgba[..., 3] > 0).sum())

        # 변환 (겹침 시 예외)
        label, _ = convert_mask(mask_rgba)

        # 저장
        src_img = os.path.join(IMAGE_DIR, stem + ".jpg")
        dst_img = os.path.join(OUT_ROOT, split, "images", stem + ".jpg")
        if os.path.exists(src_img):
            shutil.copy(src_img, dst_img)
        else:
            print(f"⚠ 이미지 없음: {src_img}")

        dst_mask = os.path.join(OUT_ROOT, split, "masks", stem + ".png")
        Image.fromarray(label, mode="L").save(dst_mask)

        # 통계
        for c in (0, 1, 2):
            n = int((label == c).sum())
            converted_pixels[split][c] += n
        if int((label == 1).sum()) > 0:
            converted_patch_with_class[split][1] += 1
        if int((label == 2).sum()) > 0:
            converted_patch_with_class[split][2] += 1
        converted_counts[split] += 1

        processed += 1
        if processed % 500 == 0:
            print(f"  진행 {processed}/{len(mask_paths)}")

    print()
    print("처리 patch:", processed)
    print("splits 미할당 스킵:", skipped_no_split)
    print()
    print(f"제외 픽셀 총합: ch2(pinhole)={total_ch2_pixels_dropped:,}, ch3(unclassified)={total_ch3_pixels_dropped:,}")
    print("(이 값은 시맨틱 라벨에서 배경으로 흡수됨)")
    print()

    # 파일 수 검증
    print("=" * 82)
    print("검증 1: 파일 수 일치 (이미지 vs 마스크 vs splits.csv)")
    print("=" * 82)
    split_target_count = defaultdict(int)
    for stem, s in assignment.items():
        split_target_count[s] += 1

    for split in ["train", "val", "test"]:
        n_img = len(os.listdir(os.path.join(OUT_ROOT, split, "images")))
        n_mask = len(os.listdir(os.path.join(OUT_ROOT, split, "masks")))
        expected = split_target_count[split]
        ok = (n_img == n_mask == expected)
        mark = "OK" if ok else "⚠"
        print(f"  {split}: images {n_img} / masks {n_mask} / splits.csv 기대 {expected}  [{mark}]")
    print()

    # 프레임 누수 검증 (splits.csv 자체가 프레임 단위이므로 재확인만)
    print("=" * 82)
    print("검증 2: 같은 원본 프레임이 두 split 에 걸친 경우 (0 이어야 함)")
    print("=" * 82)
    import re
    frame_to_splits = defaultdict(set)
    with open("classification/labels.csv") as f:
        for r in csv.DictReader(f):
            stem = r["file_name"][:-4] if r["file_name"].endswith(".jpg") else r["file_name"]
            if stem not in assignment: continue
            m = re.match(r"^(R\d+)-?(\d+)um[-_](.+?)_frame_(\d+)", r["original_file_name"])
            if not m: continue
            frame_key = (m.group(1), int(m.group(2)), m.group(3), int(m.group(4)))
            frame_to_splits[frame_key].add(assignment[stem])

    shared = sum(1 for splits in frame_to_splits.values() if len(splits) > 1)
    print(f"  unique 원본 프레임: {len(frame_to_splits)}")
    print(f"  두 split 에 걸친 프레임: {shared}  [{'OK' if shared == 0 else '⚠'}]")
    print()

    # 클래스별 픽셀 검증 (원본 ch0/ch1 vs 변환 후 1/2)
    print("=" * 82)
    print("검증 3: 클래스 픽셀 수 일치 (원본 ch0 = 변환 1, 원본 ch1 = 변환 2)")
    print("=" * 82)
    total_1 = sum(converted_pixels[s][1] for s in ["train","val","test"])
    total_2 = sum(converted_pixels[s][2] for s in ["train","val","test"])
    print(f"  원본 ch0 (crack) : {original_ch_pixels[0]:>15,}")
    print(f"  변환 후  class 1 : {total_1:>15,}  {'[OK]' if total_1 == original_ch_pixels[0] else '[⚠]'}")
    print(f"  원본 ch1 (delam) : {original_ch_pixels[1]:>15,}")
    print(f"  변환 후  class 2 : {total_2:>15,}  {'[OK]' if total_2 == original_ch_pixels[1] else '[⚠]'}")
    print()

    # 클래스 분포 보고 (§2)
    print("=" * 82)
    print("§2 클래스 분포 (split 별)")
    print("=" * 82)
    total_pix_all = sum(sum(converted_pixels[s].values()) for s in ["train","val","test"])
    for split in ["train", "val", "test"]:
        total = sum(converted_pixels[split].values())
        if total == 0:
            print(f"  {split}: N=0")
            continue
        bg = converted_pixels[split][0]
        cr = converted_pixels[split][1]
        dl = converted_pixels[split][2]
        n_patch = converted_counts[split]
        n_cr_patch = converted_patch_with_class[split][1]
        n_dl_patch = converted_patch_with_class[split][2]
        print(f"  {split} (patches {n_patch}, 총 pixel {total:,}):")
        print(f"    background : {bg:>15,} ({bg/total:.4%})")
        print(f"    crack (1)  : {cr:>15,} ({cr/total:.4%})  · crack 포함 patch {n_cr_patch}")
        print(f"    delam (2)  : {dl:>15,} ({dl/total:.4%})  · delam 포함 patch {n_dl_patch}")

    # delam 없는 split 검사
    print()
    empty_delam = [s for s in ["train","val","test"] if converted_patch_with_class[s][2] == 0]
    if empty_delam:
        print("⚠ delam 이 없는 split 발견:", empty_delam)
        print("사용자 지시: '즉시 보고하고 멈출 것'")
    else:
        print("모든 split 에 delam 존재 (train/val/test 모두)")


main()
