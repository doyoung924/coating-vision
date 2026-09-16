"""
54. 실패 케이스 축별 집계 + 콘택트 시트 (§22-DET, 2026-09-15)

results_detection_failures.csv 읽어 축별 집계 및 콘택트 시트 생성.
축: 크기·위치·밝기·다른 결함 공존.
해석 없음. 수치만.
"""

import csv
import os
from collections import defaultdict
import numpy as np
from PIL import Image
import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.patches import Rectangle
from ultralytics import YOLO

_kf = None
for _f in fm.fontManager.ttflist:
    if _f.name == "Noto Sans CJK KR":
        _kf = _f.name
        break
if _kf:
    plt.rcParams["font.family"] = _kf
plt.rcParams["axes.unicode_minus"] = False

IMG_H, IMG_W = 480, 640
CROP_MARGIN = 64
WEIGHTS = "runs/pinhole_frames_seed0/weights/best.pt"
VAL_DIR = "data/pinhole_frames_balanced/images/val"
TEST_DIR = "data/pinhole_frames_balanced/images/test"
MASK_DIR = "segmentation/masks"


def load_failures():
    rows = []
    with open("results_detection_failures.csv") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def summarize_fn(fns, total_positive_val, total_positive_test):
    """FN 축별 집계. total_positive = 각 split의 마스크 성분 총수"""
    N_val = 89   # val comps
    N_test = 19  # test comps
    N_total = N_val + N_test
    print()
    print(f"§1 FN 사례 (val {sum(1 for r in fns if r['split']=='val')}건 / val comps 89, "
          f"test {sum(1 for r in fns if r['split']=='test')}건 / test comps 19)")
    print(f"미검률 전체: {len(fns)}/{N_total} = {len(fns)/N_total:.3f}")

    # 크기 (comp_area) 구간별
    print()
    print("[크기 (comp_area px) 구간별]")
    bins = [(1, 10), (11, 30), (31, 100), (101, 500), (501, 5000)]
    for lo, hi in bins:
        subset = [r for r in fns if r["comp_area"] and lo <= int(r["comp_area"]) <= hi]
        # 전체 그 구간 comps 수는 별도 산출 필요 - 여기서는 FN 카운트만
        print(f"  area {lo:>4}~{hi:>4}: FN {len(subset)}건")

    # 위치 - row_band
    print()
    print("[위치 row_band]")
    for band in ("top", "mid", "bot"):
        subset = [r for r in fns if r["row_band"] == band]
        print(f"  {band:<5} : FN {len(subset)}건")

    print()
    print("[위치 col_band]")
    for band in ("l", "mid", "r"):
        subset = [r for r in fns if r["col_band"] == band]
        print(f"  {band:<5} : FN {len(subset)}건")

    # bottom 32px 밴드
    band = sum(1 for r in fns if int(r["in_bottom_32px_band"]))
    print(f"\n[bottom 32px 밴드 (y >= 448)]  FN {band}건")

    # 밝기 구간
    print()
    print("[주변 128px 배경 밝기]")
    b_bins = [(0, 100), (100, 130), (130, 150), (150, 180), (180, 255)]
    for lo, hi in b_bins:
        subset = [r for r in fns if lo <= float(r["bg_brightness"]) < hi]
        print(f"  brightness {lo:>3}~{hi:>3}: FN {len(subset)}건")

    # 다른 결함 공존
    print()
    print("[다른 결함 공존]")
    crack_yes = sum(1 for r in fns if int(r["cocurring_crack"]) == 1)
    delam_yes = sum(1 for r in fns if int(r["cocurring_delam"]) == 1)
    print(f"  crack 공존   : {crack_yes}/{len(fns)}")
    print(f"  delam 공존   : {delam_yes}/{len(fns)}")


def summarize_fp(fps):
    N = len(fps)
    print()
    print(f"§2 FP 사례 (val {sum(1 for r in fps if r['split']=='val')}건 / test {sum(1 for r in fps if r['split']=='test')}건)")
    print(f"과검 전체: {N}건")

    print()
    print("[박스 크기 (box_area px)]")
    bins = [(0, 100), (100, 300), (300, 1000), (1000, 5000), (5000, 100000)]
    for lo, hi in bins:
        subset = [r for r in fps if r["box_area"] and lo <= float(r["box_area"]) < hi]
        print(f"  area {lo:>5}~{hi:>5}: FP {len(subset)}건")

    print()
    print("[conf 구간]")
    c_bins = [(0.25, 0.35), (0.35, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01)]
    for lo, hi in c_bins:
        subset = [r for r in fps if r["conf"] and lo <= float(r["conf"]) < hi]
        print(f"  conf {lo:.2f}~{hi:.2f}: FP {len(subset)}건")

    print()
    print("[위치 row_band]")
    for band in ("top", "mid", "bot"):
        subset = [r for r in fps if r["row_band"] == band]
        print(f"  {band:<5} : FP {len(subset)}건")

    band = sum(1 for r in fps if int(r["in_bottom_32px_band"]))
    print(f"\n[bottom 32px 밴드]  FP {band}건")

    print()
    print("[주변 128px 배경 밝기]")
    b_bins = [(0, 100), (100, 130), (130, 150), (150, 180), (180, 255)]
    for lo, hi in b_bins:
        subset = [r for r in fps if lo <= float(r["bg_brightness"]) < hi]
        print(f"  brightness {lo:>3}~{hi:>3}: FP {len(subset)}건")


def contact_sheet_fn(fns, out_path, model):
    """FN 콘택트 시트 (원본 마스크 성분 크롭)"""
    fns_sorted = sorted(fns, key=lambda r: int(r["comp_area"]) if r["comp_area"] else 0)
    tiles = fns_sorted[:24]
    n = len(tiles)
    if n == 0:
        print("FN 없음, 시트 생략")
        return
    cols = 6
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 3.0, rows * 3.2))
    if rows == 1:
        axes = np.array([axes])
    for i, r in enumerate(tiles):
        stem = r["stem"]
        split = r["split"]
        img_dir = VAL_DIR if split == "val" else TEST_DIR
        image = np.array(Image.open(os.path.join(img_dir, stem + ".jpg")).convert("RGB"))
        cy = int(float(r["cy"])); cx = int(float(r["cx"]))
        y0 = max(0, cy - CROP_MARGIN); y1 = min(IMG_H, cy + CROP_MARGIN)
        x0 = max(0, cx - CROP_MARGIN); x1 = min(IMG_W, cx + CROP_MARGIN)
        crop = image[y0:y1, x0:x1]
        ax = axes[i // cols][i % cols]
        ax.imshow(crop)
        # 성분 위치 표시
        rel_cy = cy - y0; rel_cx = cx - x0
        ax.plot(rel_cx, rel_cy, "r+", markersize=10, markeredgewidth=1.5)
        ax.set_title(f"{r['sequence'][:24]}\n{stem} area={r['comp_area']} d={r['comp_diameter']}\n"
                      f"row={r['row_band']} col={r['col_band']} iou={r['iou']}",
                      fontsize=6.5)
        ax.axis("off")
    for i in range(n, rows * cols):
        axes[i // cols][i % cols].axis("off")
    fig.suptitle(f"§22-DET 핀홀 검출 미검 (FN) 사례 콘택트 시트 — {n}건 표시 (전체 {len(fns)}건)\n"
                 f"각 타일 128×128 크롭. + 표시 = 성분 중심. 성분 크기 오름차순",
                 fontsize=10)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    print(f"저장: {out_path}")


def contact_sheet_fp(fps, out_path):
    """FP 콘택트 시트 (YOLO 박스 크롭)"""
    fps_sorted = sorted(fps, key=lambda r: -float(r["conf"]))
    tiles = fps_sorted[:24]
    n = len(tiles)
    if n == 0:
        print("FP 없음, 시트 생략")
        return
    cols = 6
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 3.0, rows * 3.2))
    if rows == 1:
        axes = np.array([axes])
    for i, r in enumerate(tiles):
        stem = r["stem"]
        split = r["split"]
        img_dir = VAL_DIR if split == "val" else TEST_DIR
        image = np.array(Image.open(os.path.join(img_dir, stem + ".jpg")).convert("RGB"))
        cy = int(float(r["cy"])); cx = int(float(r["cx"]))
        y0 = max(0, cy - CROP_MARGIN); y1 = min(IMG_H, cy + CROP_MARGIN)
        x0 = max(0, cx - CROP_MARGIN); x1 = min(IMG_W, cx + CROP_MARGIN)
        crop = image[y0:y1, x0:x1]
        ax = axes[i // cols][i % cols]
        ax.imshow(crop)
        rel_cy = cy - y0; rel_cx = cx - x0
        # box 대략 크기 표시 (실제 학습 관례 크기)
        box_area = float(r["box_area"])
        box_side = np.sqrt(box_area)
        # 크롭 안에서 그리기
        ax.add_patch(Rectangle((rel_cx - box_side/2, rel_cy - box_side/2), box_side, box_side,
                                fill=False, edgecolor="yellow", linewidth=1.2))
        ax.set_title(f"{r['sequence'][:24]}\n{stem} conf={r['conf']} area={r['box_area']}\n"
                      f"row={r['row_band']} col={r['col_band']} iou={r['iou']}",
                      fontsize=6.5)
        ax.axis("off")
    for i in range(n, rows * cols):
        axes[i // cols][i % cols].axis("off")
    fig.suptitle(f"§22-DET 핀홀 검출 과검 (FP) 사례 콘택트 시트 — {n}건 표시 (전체 {len(fps)}건)\n"
                 f"각 타일 128×128 크롭. 노란 사각형 = YOLO 예측 박스 (근사 크기). conf 내림차순",
                 fontsize=10)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    print(f"저장: {out_path}")


def main():
    all_rows = load_failures()
    fns = [r for r in all_rows if r["category"] == "FN"]
    fps = [r for r in all_rows if r["category"] == "FP"]

    print("=" * 100)
    print("§22-DET 핀홀 검출 실패 사례 축별 집계")
    print("=" * 100)
    print(f"대상: val (N=150 프레임 / 89 comps) + test R7/700 (N=38 / 19 comps)")
    print(f"모델: runs/pinhole_frames_seed0/weights/best.pt (§19-7 대표), conf>=0.25")
    print(f"매칭: IoU 0.5 기준, 마스크 성분 tight bbox 에 §13 선형 2배 패딩 적용")
    print(f"전체: FN {len(fns)}건 (미검), FP {len(fps)}건 (과검)")

    summarize_fn(fns, 89, 19)
    summarize_fp(fps)

    print()
    print("=" * 100)
    print("§22-DET 콘택트 시트")
    print("=" * 100)
    contact_sheet_fn(fns, "figures/detection_failures_fn.png", None)
    contact_sheet_fp(fps, "figures/detection_failures_fp.png")


main()
