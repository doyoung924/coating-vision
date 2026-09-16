"""
33. 겹침·band 실측 (라벨 변환 전 확인)

조사 항목:
  4-1. ch0(crack) AND ch1(delam) 이 같은 픽셀에서 둘 다 양수인 경우의
       픽셀 수와 비율. 전체 및 시퀀스별
  4-2. 겹침 픽셀이 delam 전체 픽셀에서 차지하는 비율
       (delam 이 crack 안에 얼마나 포함되는지)
  4-3. band(y >= 448) 에 속한 crack·delam 픽셀 수와 비율.
       band crop 시 버려지는 결함량

임계 (사용자 지시):
  - band crop 결함 손실 > 5% → 멈추고 보고
  - 겹침 > delam 픽셀 50% → 클래스 정의 재검토 필요, 멈추고 보고
"""

import csv
import os
import re
import sys
import numpy as np
from PIL import Image
from collections import defaultdict


LABELS_CSV = "classification/labels.csv"
MASK_DIR = "segmentation/masks"

CELL_SIZE = 64
BAND_Y_MIN = 448   # 7 rows × 64
LOSS_THRESHOLD = 0.05
OVERLAP_THRESHOLD = 0.50


def parse(name):
    m = re.match(r"^(R\d+)-?(\d+)um[-_](.+?)_frame_(\d+)", name)
    if not m: return None
    return {
        "run_id": m.group(1),
        "coating_gap": int(m.group(2)),
        "position": m.group(3),
        "frame_number": int(m.group(4)),
    }


def main():
    print("=" * 92)
    print("33. 겹침·band 실측 (라벨 변환 전)")
    print("=" * 92)

    metadata = {}
    with open(LABELS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = parse(row["original_file_name"])
            if parsed is None: continue
            stem = row["file_name"]
            if stem.endswith(".jpg"): stem = stem[:-4]
            parsed["stem"] = stem
            metadata[stem] = parsed

    total_pixels = 0
    crack_pixels = 0
    delam_pixels = 0
    overlap_pixels = 0
    band_pixels = 0
    band_crack_pixels = 0
    band_delam_pixels = 0
    band_overlap_pixels = 0

    seq_stats = defaultdict(lambda: {"total": 0, "crack": 0, "delam": 0,
                                      "overlap": 0, "band_crack": 0, "band_delam": 0})

    processed = 0
    for stem in metadata:
        record = metadata[stem]
        mask_path = os.path.join(MASK_DIR, stem + ".png")
        if os.path.exists(mask_path) is False:
            continue
        mask = np.array(Image.open(mask_path))
        ch0 = mask[..., 0] > 0
        ch1 = mask[..., 1] > 0
        overlap = ch0 & ch1

        n_pixels = ch0.size
        n_crack = int(ch0.sum())
        n_delam = int(ch1.sum())
        n_overlap = int(overlap.sum())

        band_mask = np.zeros_like(ch0, dtype=bool)
        band_mask[BAND_Y_MIN:, :] = True
        n_band = int(band_mask.sum())
        n_band_crack = int((ch0 & band_mask).sum())
        n_band_delam = int((ch1 & band_mask).sum())
        n_band_overlap = int((overlap & band_mask).sum())

        total_pixels += n_pixels
        crack_pixels += n_crack
        delam_pixels += n_delam
        overlap_pixels += n_overlap
        band_pixels += n_band
        band_crack_pixels += n_band_crack
        band_delam_pixels += n_band_delam
        band_overlap_pixels += n_band_overlap

        seq_key = (record["run_id"], record["coating_gap"], record["position"])
        s = seq_stats[seq_key]
        s["total"] += n_pixels
        s["crack"] += n_crack
        s["delam"] += n_delam
        s["overlap"] += n_overlap
        s["band_crack"] += n_band_crack
        s["band_delam"] += n_band_delam

        processed += 1
        if processed % 500 == 0:
            print("  진행 {}/{}".format(processed, len(metadata)))

    print()
    print("처리 patch:", processed)
    print()

    # 4-1, 4-2 전체
    print("=" * 92)
    print("4-1, 4-2. 겹침 픽셀 (crack AND delam)")
    print("=" * 92)
    print("  총 픽셀            : {:>14,}".format(total_pixels))
    print("  crack 픽셀          : {:>14,}  ({:.3%} of total)".format(
        crack_pixels, crack_pixels/total_pixels))
    print("  delam 픽셀          : {:>14,}  ({:.3%} of total)".format(
        delam_pixels, delam_pixels/total_pixels))
    print("  overlap 픽셀        : {:>14,}  ({:.3%} of total)".format(
        overlap_pixels, overlap_pixels/total_pixels))
    print()
    ratio_of_delam = overlap_pixels / max(1, delam_pixels)
    ratio_of_crack = overlap_pixels / max(1, crack_pixels)
    print("  overlap / delam    : {:.4f}  ({:.2%})".format(ratio_of_delam, ratio_of_delam))
    print("  overlap / crack    : {:.4f}  ({:.2%})".format(ratio_of_crack, ratio_of_crack))
    print()

    if ratio_of_delam > OVERLAP_THRESHOLD:
        print("  ⚠ overlap / delam = {:.2%} > 임계 {:.0%}. 클래스 정의 재검토 필요.".format(
            ratio_of_delam, OVERLAP_THRESHOLD))

    # 시퀀스별
    print("=" * 92)
    print("시퀀스별 겹침 (overlap / delam)")
    print("=" * 92)
    print("  {:<42}{:>12}{:>12}{:>12}{:>10}".format(
        "시퀀스", "crack px", "delam px", "overlap px", "ov/delam"))
    print("  " + "-" * 90)
    for seq in sorted(seq_stats.keys()):
        s = seq_stats[seq]
        od = s["overlap"] / max(1, s["delam"])
        print("  {:<42}{:>12,}{:>12,}{:>12,}{:>10.3%}".format(
            str(seq), s["crack"], s["delam"], s["overlap"], od))
    print()

    # 4-3. band
    print("=" * 92)
    print("4-3. band(y >= 448) 픽셀량 및 결함 손실")
    print("=" * 92)
    print("  band 총 픽셀       : {:>14,}  ({:.3%} of total)".format(
        band_pixels, band_pixels/total_pixels))
    print("  band 안 crack       : {:>14,}  ({:.3%} of crack)".format(
        band_crack_pixels, band_crack_pixels/max(1,crack_pixels)))
    print("  band 안 delam       : {:>14,}  ({:.3%} of delam)".format(
        band_delam_pixels, band_delam_pixels/max(1,delam_pixels)))
    print("  band 안 overlap     : {:>14,}  ({:.3%} of overlap)".format(
        band_overlap_pixels, band_overlap_pixels/max(1,overlap_pixels)))
    print()

    band_crack_loss = band_crack_pixels / max(1, crack_pixels)
    band_delam_loss = band_delam_pixels / max(1, delam_pixels)
    print("  band crop 시 결함 손실:")
    print("    crack 손실 비율   : {:.3%}   {}".format(
        band_crack_loss, "⚠ > 5% 임계 초과" if band_crack_loss > LOSS_THRESHOLD else "(허용)"))
    print("    delam 손실 비율   : {:.3%}   {}".format(
        band_delam_loss, "⚠ > 5% 임계 초과" if band_delam_loss > LOSS_THRESHOLD else "(허용)"))
    print()

    # 시퀀스별 band 손실
    print("  시퀀스별 band 손실 비율:")
    print("  {:<42}{:>15}{:>15}".format("시퀀스", "crack 손실%", "delam 손실%"))
    for seq in sorted(seq_stats.keys()):
        s = seq_stats[seq]
        cl = s["band_crack"] / max(1, s["crack"])
        dl = s["band_delam"] / max(1, s["delam"])
        print("  {:<42}{:>15.3%}{:>15.3%}".format(str(seq), cl, dl))
    print()

    print("=" * 92)
    print("결정 지원 (사용자 지시 §5 임계 대조)")
    print("=" * 92)
    print("  band crop 결함 손실 임계 5%:")
    print("    crack: {:.3%}  {}".format(
        band_crack_loss, "PASS" if band_crack_loss <= LOSS_THRESHOLD else "STOP"))
    print("    delam: {:.3%}  {}".format(
        band_delam_loss, "PASS" if band_delam_loss <= LOSS_THRESHOLD else "STOP"))
    print("  overlap 임계 50% (delam 대비):")
    print("    {:.2%}  {}".format(
        ratio_of_delam, "PASS" if ratio_of_delam <= OVERLAP_THRESHOLD else "STOP"))


main()
