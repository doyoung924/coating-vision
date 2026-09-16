"""
30. YOLO 경계 오검출 실태 확인

배경 (§16, §17-7):
  §16 은 A3 (밝기 표준편차) 가 코팅 영역 경계에 반응한다는 결과.
  YOLO 도 경계를 핀홀로 오검출하는지는 확인되지 않았다.
  ③ 네거티브 샘플 추가로 학습을 개선하려면 이 실측이 먼저다.

방법:
  - YOLO 재실행 금지. results_yolo_cache.csv 사용 (전량 1 회 추론된 conf 0.25 이상 박스)
  - 고 std 음성 cell H (pinhole_px==0 AND std >= 7.407, §26 정의, N=179):
    실제로 코팅 경계로 판정된 cell (사용자 육안 확인, §16-10)
  - 정상 기준선 N (pinhole_px==0 이고 std ≈ median 1.794 근처 200 sample):
    §26 콘택트 시트 C 와 같은 방식
  - 각 cell 에 YOLO 박스 중심이 있는지 카운트
  - "박스 중심이 안에 있는 cell 비율" 을 H 대 N 으로 비교

판단:
  - H 의 박스 출현율이 N 대비 뚜렷이 높으면 → YOLO 도 경계에 반응. ③ 근거 성립
  - 차이 없으면 → YOLO 는 형태를 보므로 경계에 속지 않음. ③ 건너뛰기

산출:
  - stdout 통계 표
  - figures/yolo_boundary_fp.png 오검출 12 장 콘택트 시트 (conf 상위)
"""

import csv
import json
import os
import sys
import numpy as np
from PIL import Image
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.font_manager as font_manager
from collections import defaultdict


CELLS_CSV = "results_pinhole_cells.csv"
YOLO_CACHE_CSV = "results_yolo_cache.csv"
IMAGE_DIR = "segmentation/images"
FIG_FP = "figures/yolo_boundary_fp.png"

CELL_SIZE = 64
STRIDE = 64
GRID_ROWS = 7
GRID_COLS = 10
BAND_Y_MIN = GRID_ROWS * STRIDE   # 448

# §26 정의 그대로
HIGH_STD_THRESHOLD_P99 = 7.4071   # pinhole_px==0 부분집합의 std 99 퍼센타일
MEDIAN_STD_TARGET = 1.7942        # pinhole_px==0 median


# ===============================================================
# 로딩
# ===============================================================

def load_cells():
    result = []
    with open(CELLS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            result.append({
                "stem": row["stem"],
                "run_id": row["run_id"],
                "coating_gap": int(row["coating_gap"]),
                "position": row["position"],
                "row": int(row["row"]),
                "col": int(row["col"]),
                "std": float(row["std"]),
                "pinhole_px": int(row["pinhole_px"]),
            })
    return result


def load_yolo_boxes():
    """stem -> list of {x1,y1,x2,y2,conf,cx,cy,w,h,area}"""
    result = defaultdict(list)
    with open(YOLO_CACHE_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            boxes = json.loads(row["boxes"])
            for b in boxes:
                x1, y1, x2, y2, conf = b
                w = x2 - x1
                h = y2 - y1
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                result[row["stem"]].append({
                    "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                    "conf": conf,
                    "cx": cx, "cy": cy,
                    "w": w, "h": h,
                    "area": w * h,
                })
    return result


def cell_of_point(cy, cx):
    """(cy, cx) 픽셀 좌표 → (row, col). band 이면 None."""
    if cy >= BAND_Y_MIN:
        return None
    r = int(cy // CELL_SIZE)
    c = int(cx // CELL_SIZE)
    if r < 0 or r >= GRID_ROWS: return None
    if c < 0 or c >= GRID_COLS: return None
    return r, c


# ===============================================================
# 메인
# ===============================================================

def main():
    if os.path.exists(CELLS_CSV) is False or os.path.exists(YOLO_CACHE_CSV) is False:
        print("전제 CSV 부재. 24, 29 실행 후 재시도.")
        sys.exit(1)

    print("=" * 92)
    print("30. YOLO 경계 오검출 실태 확인")
    print("=" * 92)

    cells = load_cells()
    yolo_by_stem = load_yolo_boxes()
    print("cell 총합:", len(cells))
    print("YOLO 박스 캐시된 stem 수:", len(yolo_by_stem))

    # 그룹 정의
    negatives = [c for c in cells if c["pinhole_px"] == 0]
    H = [c for c in negatives if c["std"] >= HIGH_STD_THRESHOLD_P99]
    H.sort(key=lambda x: -x["std"])
    N_baseline = sorted(negatives, key=lambda x: abs(x["std"] - MEDIAN_STD_TARGET))[:200]

    print()
    print("H (고 std 음성, std >= {:.3f}): {} cell".format(HIGH_STD_THRESHOLD_P99, len(H)))
    print("N (음성 기준선, std ≈ {:.3f} 근처): {} cell".format(MEDIAN_STD_TARGET, len(N_baseline)))
    print()

    # 각 그룹의 cell 안에 YOLO 박스 중심 존재 여부
    def analyze_group(group, label):
        cell_has_box = 0
        total_boxes_inside = 0
        matched_boxes = []      # (cell_ref, box)
        for c in group:
            stem = c["stem"]
            cr, cc = c["row"], c["col"]
            boxes = yolo_by_stem.get(stem, [])
            hits = 0
            for b in boxes:
                rc = cell_of_point(b["cy"], b["cx"])
                if rc is None: continue
                if rc[0] == cr and rc[1] == cc:
                    hits = hits + 1
                    total_boxes_inside = total_boxes_inside + 1
                    matched_boxes.append((c, b))
            if hits > 0:
                cell_has_box = cell_has_box + 1
        return {
            "group": label,
            "n_cells": len(group),
            "cells_with_box": cell_has_box,
            "cells_with_box_rate": cell_has_box / max(1, len(group)),
            "total_boxes_inside": total_boxes_inside,
            "boxes_per_cell": total_boxes_inside / max(1, len(group)),
            "matched_boxes": matched_boxes,
        }

    H_stats = analyze_group(H, "H")
    N_stats = analyze_group(N_baseline, "N")

    # 참고: 정답 cell (pinhole_px > 0) 도 함께 뽑는다
    positives = [c for c in cells if c["pinhole_px"] > 0]
    P_stats = analyze_group(positives, "P")

    print("=" * 92)
    print("11-1. YOLO 박스 중심이 cell 안에 있는 비율")
    print("=" * 92)
    print("  {:<40}{:>8}{:>12}{:>12}{:>12}{:>14}".format(
        "그룹", "N cell", "박스있음", "비율", "박스총합", "박스/cell"))
    for s in [H_stats, N_stats, P_stats]:
        print("  {:<40}{:>8}{:>12}{:>12.3f}{:>12}{:>14.3f}".format(
            s["group"], s["n_cells"], s["cells_with_box"],
            s["cells_with_box_rate"], s["total_boxes_inside"], s["boxes_per_cell"]))
    print()

    # 오검출 박스 통계
    fp_boxes = [b for _, b in H_stats["matched_boxes"]]
    print("=" * 92)
    print("11-2. H 그룹 안에 걸린 박스 (오검출 후보) 통계")
    print("=" * 92)
    print("  박스 수:", len(fp_boxes))
    if len(fp_boxes) > 0:
        confs = np.array([b["conf"] for b in fp_boxes])
        widths = np.array([b["w"] for b in fp_boxes])
        heights = np.array([b["h"] for b in fp_boxes])
        areas = np.array([b["area"] for b in fp_boxes])
        for name, arr in [("conf", confs), ("width(px)", widths),
                           ("height(px)", heights), ("area(px)", areas)]:
            q = np.percentile(arr, [25, 50, 75, 90])
            print("  {:<12} min={:>7.2f}  p25={:>7.2f}  p50={:>7.2f}  p75={:>7.2f}  p90={:>7.2f}  max={:>7.2f}".format(
                name, float(arr.min()), q[0], q[1], q[2], q[3], float(arr.max())))
    print()

    # 참고: P 그룹 박스 통계 (정답 매칭 박스)
    tp_boxes = [b for _, b in P_stats["matched_boxes"]]
    print("참고: P 그룹 (pinhole_px > 0) 안에 걸린 박스 통계, 박스 수:", len(tp_boxes))
    if len(tp_boxes) > 0:
        confs = np.array([b["conf"] for b in tp_boxes])
        areas = np.array([b["area"] for b in tp_boxes])
        q_c = np.percentile(confs, [25, 50, 75, 90])
        q_a = np.percentile(areas, [25, 50, 75, 90])
        print("  conf     min={:.2f}  p25={:.2f}  p50={:.2f}  p75={:.2f}  p90={:.2f}".format(
            float(confs.min()), q_c[0], q_c[1], q_c[2], q_c[3]))
        print("  area     min={:.1f}  p25={:.1f}  p50={:.1f}  p75={:.1f}  p90={:.1f}".format(
            float(areas.min()), q_a[0], q_a[1], q_a[2], q_a[3]))
    print()

    # 콘택트 시트: 오검출 conf 상위 12
    if len(fp_boxes) == 0:
        print("오검출 후보 없음. 시트 생략.")
        return

    fp_pairs = sorted(H_stats["matched_boxes"], key=lambda pair: -pair[1]["conf"])[:12]

    korean = None
    for f in font_manager.fontManager.ttflist:
        if f.name == "Noto Sans CJK KR":
            korean = f.name
            break
    if korean is not None:
        plt.rcParams["font.family"] = korean
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(3, 4, figsize=(16, 12))
    fig.subplots_adjust(hspace=0.55, wspace=0.15, top=0.94, bottom=0.03,
                        left=0.03, right=0.97)
    fig.suptitle("YOLO 오검출 후보 12 (H 그룹 안 박스 중심, conf 상위)", fontsize=13)

    context_size = 128   # 컨텍스트 크롭 크기 (박스와 인접 경계까지 보이도록)

    idx = 0
    for cell_info, box in fp_pairs:
        r = idx // 4
        c = idx % 4
        ax = axes[r][c]

        stem = cell_info["stem"]
        image_path = os.path.join(IMAGE_DIR, stem + ".jpg")
        image = np.array(Image.open(image_path).convert("RGB"))

        # 박스 중심 기준으로 context_size 창 크롭
        cx = int(round(box["cx"]))
        cy = int(round(box["cy"]))
        x0 = max(0, cx - context_size // 2)
        y0 = max(0, cy - context_size // 2)
        x1 = min(image.shape[1], x0 + context_size)
        y1 = min(image.shape[0], y0 + context_size)

        crop = image[y0:y1, x0:x1]
        ax.imshow(crop, interpolation="nearest")

        # 크롭 좌표계에서 박스 그리기
        bx1 = box["x1"] - x0
        by1 = box["y1"] - y0
        bw = box["w"]
        bh = box["h"]
        rect = mpatches.Rectangle((bx1, by1), bw, bh,
                                   fill=False, edgecolor="#d62728", linewidth=1.6)
        ax.add_patch(rect)

        # 이 cell 의 격자 경계 (녹색 점선)
        cell_x0 = cell_info["col"] * CELL_SIZE - x0
        cell_y0 = cell_info["row"] * CELL_SIZE - y0
        cell_rect = mpatches.Rectangle((cell_x0, cell_y0), CELL_SIZE, CELL_SIZE,
                                        fill=False, edgecolor="#2ca02c",
                                        linewidth=1.0, linestyle="--")
        ax.add_patch(cell_rect)

        caption = "{}  r{} c{}\nconf={:.3f}  box={}x{}px  std={:.2f}".format(
            stem, cell_info["row"], cell_info["col"],
            box["conf"], int(box["w"]), int(box["h"]), cell_info["std"])
        ax.set_title(caption, fontsize=8, pad=4)
        ax.set_xticks([])
        ax.set_yticks([])
        idx = idx + 1

    while idx < 12:
        r = idx // 4
        c = idx % 4
        axes[r][c].axis("off")
        idx = idx + 1

    fig.savefig(FIG_FP, dpi=140)
    plt.close(fig)
    print("저장:", FIG_FP)


main()
