"""
26. 고 std 음성 cell 좌표 분포 및 육안 확인용 콘택트 시트

배경 (§16-5):
  25 에서 cell std max 로 집계해도 AUROC 0.657 이 상한임을 확인했다.
  원인 후보 중 하나로 "음성 cell (핀홀 없음) 의 std 상한 오염" 이 지목됐다.
  본 스크립트는 그 오염된 음성 cell 이 어디에 몰려 있는지 좌표·시퀀스로 찾고,
  실제 픽셀을 육안으로 볼 수 있게 콘택트 시트 세 장을 만든다.

정의:
  - 대상 집합 H: results_pinhole_cells.csv 에서 pinhole_px == 0 인 cell (N=17,898)
    중 std 상위 1% (= H 부분집합 안에서 std 의 99 퍼센타일 이상)
  - 비교 집합 P: pinhole_px > 0 인 cell (N=232) 중 std 상위 24 개
  - 기준선 집합 N: pinhole_px == 0 이고 std 가 pinhole_px==0 중앙값 근처
    (median ± 0.05) 범위에 있는 cell 중 24 개

새 계산법 도입 금지. std 값은 24 가 저장한 것을 그대로 사용.
이미지에서 cell 원본 픽셀을 잘라내는 것은 시각화 조작으로 계산이 아님.

출력:
  - stdout §8 표
  - figures/high_std_negative_heatmap.png
  - figures/contact_high_std_negative.png   (H 상위 24)
  - figures/contact_pinhole_positive.png    (P 상위 24)
  - figures/contact_normal_baseline.png     (N 24)
"""

import csv
import os
import sys
import numpy as np
from PIL import Image
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as font_manager


CELLS_CSV = "results_pinhole_cells.csv"
IMAGE_DIR = "segmentation/images"

FIG_HEATMAP = "figures/high_std_negative_heatmap.png"
FIG_H = "figures/contact_high_std_negative.png"
FIG_P = "figures/contact_pinhole_positive.png"
FIG_N = "figures/contact_normal_baseline.png"

CELL_SIZE = 64
STRIDE = 64
GRID_ROWS = 7
GRID_COLS = 10


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
                "cell_index": int(row["cell_index"]),
                "row": int(row["row"]),
                "col": int(row["col"]),
                "std": float(row["std"]),
                "pinhole_px": int(row["pinhole_px"]),
            })
    return result


def cell_image(stem, row, col):
    path = os.path.join(IMAGE_DIR, stem + ".jpg")
    image = np.array(Image.open(path).convert("RGB"))
    gray = np.mean(image.astype(np.float64), axis=2)
    y0 = row * STRIDE
    x0 = col * STRIDE
    return gray[y0:y0 + CELL_SIZE, x0:x0 + CELL_SIZE]


# ===============================================================
# 콘택트 시트
# ===============================================================

def contact_sheet(cells, out_path, title):
    """4 rows x 6 cols 격자. 각 타일 64x64 그레이."""
    korean = None
    for f in font_manager.fontManager.ttflist:
        if f.name == "Noto Sans CJK KR":
            korean = f.name
            break
    if korean is not None:
        plt.rcParams["font.family"] = korean
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(4, 6, figsize=(15, 11))
    fig.subplots_adjust(hspace=0.55, wspace=0.25, top=0.94, bottom=0.03,
                        left=0.03, right=0.97)
    fig.suptitle(title, fontsize=13)

    index = 0
    while index < 24:
        r = index // 6
        c = index % 6
        ax = axes[r][c]
        if index < len(cells):
            info = cells[index]
            image = cell_image(info["stem"], info["row"], info["col"])
            ax.imshow(image, cmap="gray", interpolation="nearest",
                      vmin=0, vmax=255)
            caption = "{}  r{} c{}\nstd={:.3f}  pin={}".format(
                info["stem"], info["row"], info["col"],
                info["std"], info["pinhole_px"])
            ax.set_title(caption, fontsize=8, pad=4)
        else:
            ax.axis("off")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine_name in ["top", "bottom", "left", "right"]:
            ax.spines[spine_name].set_edgecolor("#888888")
            ax.spines[spine_name].set_linewidth(0.6)
        index = index + 1

    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ===============================================================
# 메인
# ===============================================================

def main():
    if os.path.exists(CELLS_CSV) is False:
        print(CELLS_CSV, "없음. 24 를 먼저 실행할 것.")
        sys.exit(1)

    cells = load_cells()
    print("=" * 92)
    print("26. 고 std 음성 cell 좌표 분포 + 콘택트 시트")
    print("=" * 92)
    print("총 cell: {}".format(len(cells)))

    negatives = [c for c in cells if c["pinhole_px"] == 0]
    positives = [c for c in cells if c["pinhole_px"] > 0]
    print("pinhole_px == 0 cell: {}".format(len(negatives)))
    print("pinhole_px  > 0 cell: {}".format(len(positives)))
    print()

    # =====================================================
    # §8. 고 std 음성 cell 정의: pinhole_px==0 중 std 99pct 이상
    # =====================================================
    neg_stds = np.array([c["std"] for c in negatives], dtype=float)
    threshold = float(np.percentile(neg_stds, 99))
    high_neg = [c for c in negatives if c["std"] >= threshold]
    high_neg.sort(key=lambda x: -x["std"])   # 내림차순

    print("=" * 92)
    print("8. 고 std 음성 cell (pinhole_px==0 부분집합에서 std 상위 1%)")
    print("=" * 92)
    print("임계 std (pinhole_px==0 부분집합 99 퍼센타일): {:.4f}".format(threshold))
    print("결과 집합 크기: {}".format(len(high_neg)))
    print()

    hstds = np.array([c["std"] for c in high_neg])
    q = np.percentile(hstds, [0, 25, 50, 75, 100])
    print("  결과 집합 std 분포: min {:.3f}  p25 {:.3f}  p50 {:.3f}  p75 {:.3f}  max {:.3f}".format(
        q[0], q[1], q[2], q[3], q[4]))
    print()

    # row × col 격자 표 (결과 집합)
    high_grid = np.zeros((GRID_ROWS, GRID_COLS), dtype=int)
    for c in high_neg:
        high_grid[c["row"], c["col"]] += 1
    print("결과 집합의 row × col 카운트 표:")
    header = "        " + "".join("c{:>3} ".format(x) for x in range(GRID_COLS))
    print(header)
    for r in range(GRID_ROWS):
        line = "  r{:<3} ".format(r) + " ".join("{:>4}".format(high_grid[r, x]) for x in range(GRID_COLS))
        print(line)
    print()

    # 가장자리 (row 0, row 6, col 0, col 9)
    edge_mask = np.zeros_like(high_grid, dtype=bool)
    edge_mask[0, :] = True
    edge_mask[GRID_ROWS - 1, :] = True
    edge_mask[:, 0] = True
    edge_mask[:, GRID_COLS - 1] = True
    edge_count = int(high_grid[edge_mask].sum())
    interior_count = int(high_grid[~edge_mask].sum())
    edge_cells = int(edge_mask.sum())
    interior_cells = int((~edge_mask).sum())
    print("  가장자리 (row∈{{0,6}} OR col∈{{0,9}}) 셀 수 = {} / 70".format(edge_cells))
    print("  결과 집합 중 가장자리 카운트: {} / {}  ({:.3f})".format(
        edge_count, len(high_neg), edge_count / max(1, len(high_neg))))
    print("  결과 집합 중 내부 카운트   : {} / {}".format(interior_count, len(high_neg)))
    print()

    # 전체 cell 의 row × col 분포 (균등이면 각 셀 259)
    total_grid = np.zeros((GRID_ROWS, GRID_COLS), dtype=int)
    for c in cells:
        total_grid[c["row"], c["col"]] += 1
    print("비교: 전체 cell 의 row × col 카운트 (균등이면 각 셀 = 259):")
    print(header)
    for r in range(GRID_ROWS):
        line = "  r{:<3} ".format(r) + " ".join("{:>4}".format(total_grid[r, x]) for x in range(GRID_COLS))
        print(line)
    print()

    # 시퀀스별 카운트
    print("시퀀스별 결과 집합 카운트:")
    from collections import defaultdict
    seq_counts = defaultdict(int)
    seq_totals = defaultdict(int)
    for c in high_neg:
        seq_counts[(c["run_id"], c["coating_gap"], c["position"])] += 1
    for c in cells:
        seq_totals[(c["run_id"], c["coating_gap"], c["position"])] += 1
    print("  {:<40}{:>10}{:>12}{:>10}".format("시퀀스", "결과집합", "총 cell", "비율"))
    for k in sorted(seq_totals.keys()):
        n_high = seq_counts.get(k, 0)
        n_total = seq_totals[k]
        rate = n_high / max(1, n_total)
        print("  {:<40}{:>10}{:>12}{:>10.4f}".format(str(k), n_high, n_total, rate))
    print()

    # 히트맵
    korean = None
    for f in font_manager.fontManager.ttflist:
        if f.name == "Noto Sans CJK KR":
            korean = f.name
            break
    if korean is not None:
        plt.rcParams["font.family"] = korean
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(1, 2, figsize=(15, 5))
    im0 = axes[0].imshow(high_grid, cmap="Reds", aspect="auto")
    axes[0].set_title("고 std 음성 cell (N={}) — row×col 카운트".format(len(high_neg)))
    axes[0].set_xlabel("col (0=좌, 9=우)")
    axes[0].set_ylabel("row (0=상, 6=하)")
    axes[0].set_xticks(range(GRID_COLS))
    axes[0].set_yticks(range(GRID_ROWS))
    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            axes[0].text(c, r, str(high_grid[r, c]), ha="center", va="center",
                         fontsize=9, color="black" if high_grid[r, c] < high_grid.max() * 0.6 else "white")
    fig.colorbar(im0, ax=axes[0], shrink=0.85)

    im1 = axes[1].imshow(total_grid, cmap="Blues", aspect="auto")
    axes[1].set_title("전체 cell (N={}) — row×col 카운트 (참고, 균등=259)".format(len(cells)))
    axes[1].set_xlabel("col")
    axes[1].set_ylabel("row")
    axes[1].set_xticks(range(GRID_COLS))
    axes[1].set_yticks(range(GRID_ROWS))
    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            axes[1].text(c, r, str(total_grid[r, c]), ha="center", va="center",
                         fontsize=9, color="black" if total_grid[r, c] < total_grid.max() * 0.6 else "white")
    fig.colorbar(im1, ax=axes[1], shrink=0.85)

    fig.tight_layout()
    fig.savefig(FIG_HEATMAP, dpi=140)
    plt.close(fig)
    print("저장:", FIG_HEATMAP)
    print()

    # =====================================================
    # §9. 콘택트 시트 3 장
    # =====================================================
    # A: 고 std 음성 24 개 (std 내림차순)
    sheet_A = high_neg[:24]

    # B: pinhole_px > 0 cell 중 std 상위 24 개
    positives_sorted = sorted(positives, key=lambda x: -x["std"])
    sheet_B = positives_sorted[:24]

    # C: pinhole_px == 0 AND std 중앙값 근처 (median ± 0.05)
    neg_median = float(np.median(neg_stds))
    near_median = [c for c in negatives if abs(c["std"] - neg_median) <= 0.05]
    near_median.sort(key=lambda x: abs(x["std"] - neg_median))
    if len(near_median) < 24:
        # 반경 확대
        near_median = sorted(negatives, key=lambda x: abs(x["std"] - neg_median))[:24]
    sheet_C = near_median[:24]

    print("=" * 92)
    print("9. 콘택트 시트")
    print("=" * 92)
    print("A (고 std 음성): {} 타일, std 범위 {:.3f} ~ {:.3f}".format(
        len(sheet_A), sheet_A[-1]["std"] if sheet_A else 0.0,
        sheet_A[0]["std"] if sheet_A else 0.0))
    print("B (pinhole_px>0 상위): {} 타일, std 범위 {:.3f} ~ {:.3f}".format(
        len(sheet_B), sheet_B[-1]["std"] if sheet_B else 0.0,
        sheet_B[0]["std"] if sheet_B else 0.0))
    print("C (음성 기준선, median={:.3f} 근처): {} 타일, std 범위 {:.3f} ~ {:.3f}".format(
        neg_median, len(sheet_C),
        min(c["std"] for c in sheet_C) if sheet_C else 0.0,
        max(c["std"] for c in sheet_C) if sheet_C else 0.0))
    print()

    contact_sheet(sheet_A, FIG_H,
                  "A. 고 std 음성 cell 상위 24  (pinhole_px==0, std ≥ {:.3f}, N total={})".format(
                      threshold, len(high_neg)))
    print("저장:", FIG_H)

    contact_sheet(sheet_B, FIG_P,
                  "B. pinhole_px>0 cell 상위 24  (전체 pinhole_px>0 cell N={})".format(len(positives)))
    print("저장:", FIG_P)

    contact_sheet(sheet_C, FIG_N,
                  "C. pinhole_px==0 기준선 24  (std 중앙값 {:.3f} 근처)".format(neg_median))
    print("저장:", FIG_N)


main()
