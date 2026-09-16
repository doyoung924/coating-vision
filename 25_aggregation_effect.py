"""
25. 집계 단위별 검출력 비교 — patch defect_ratio vs cell std 상위값

배경 (§16 후속):
  24 에서 cell 단위로 내려가면 A3 STD 가 핀홀 픽셀 수에 반응함을 확인했다
  (pinhole_px 1~5 픽셀 구간에서도 std 중앙값 3.78, 기준선 1.79 대비 2 배).
  §15-8 의 "공간 해상도 문제" 가설은 기각됐고, 남은 후보는 집계 희석이다.

  patch (480x640) 당 70 cell 을 평균낸 defect_ratio 로 판별하면
  핀홀 cell (patch 당 평균 0.90 개) 신호가 정상 cell 에 묻힐 것이라는
  가설을 이번 스크립트에서 직접 검증한다. 새 계산법을 만들지 말고
  기존 cell std 와 마스크 성분 정보만 사용한다.

정답:
  patch 에 핀홀 성분이 존재하는지 여부. 단, band-only 성분
  (성분 전체 픽셀이 y >= 448 밴드 안에 있는 것) 4 개는 A3 반응 영역 밖이라
  정답 계산에서 제외.

집계 방식 4 종 (같은 259 clean patch 위에서):
  A. patch defect_ratio  — a3_defect_ratio (09 가 계산해 둔 값). 70 cell 중
     std >= patch_threshold 인 비율
  B. cell std max         — patch 내 70 cell std 최대값
  C. cell std top-3 mean  — 상위 3 개 평균
  D. cell std top-5 mean  — 상위 5 개 평균

지표:
  - 각 방식의 값 분포 (positive/negative 각각 N, p25, p50, p75)
  - AUROC (Mann-Whitney U 랭크 방식, tie 는 평균 랭크)
  - ROC 곡선 4 방식 한 장

또한 §6 경계 교차 성분 분석:
  n_cells_touched 분포, 교차/비교차 면적 분포, cell 단위 과대계수 배수.

산출:
  - figures/aggregation_auroc.png
  - stdout: 표
"""

import csv
import os
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as font_manager
from collections import defaultdict


MASK_AREA_CSV = "results_mask_area.csv"
COMPONENTS_CSV = "results_pinhole_components.csv"
CELLS_CSV = "results_pinhole_cells.csv"
FIG_ROC = "figures/aggregation_auroc.png"


# ===============================================================
# 로딩
# ===============================================================

def load_clean_patches():
    result = []
    with open(MASK_AREA_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if float(row["area_crack"]) == 0.0 and float(row["area_delam"]) == 0.0:
                result.append({
                    "stem": row["stem"],
                    "a3_defect_ratio": float(row["a3_defect_ratio"]),
                })
    return result


def load_components():
    """성분 리스트. band-only 필터는 정답 산출 시점에 적용."""
    result = []
    with open(COMPONENTS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            result.append({
                "stem": row["stem"],
                "area_pixels": int(row["area_pixels"]),
                "band_pixels": int(row["band_pixels"]),
                "n_cells_touched": int(row["n_cells_touched"]),
                "crosses_cell_boundary": int(row["crosses_cell_boundary"]),
                "max_piece_pixels": int(row["max_piece_pixels"]),
            })
    return result


def load_cells():
    """{stem: [70 개 std]}"""
    by_stem = defaultdict(list)
    with open(CELLS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            by_stem[row["stem"]].append(float(row["std"]))
    return by_stem


# ===============================================================
# AUROC (Mann-Whitney U, tie 평균 랭크)
# ===============================================================

def rank_values(values):
    array = np.array(values, dtype=float)
    order = np.argsort(array, kind="mergesort")
    ranks = np.zeros(len(array), dtype=float)
    index = 0
    while index < len(array):
        end = index
        while end + 1 < len(array) and array[order[end + 1]] == array[order[index]]:
            end = end + 1
        avg = (index + end) / 2.0 + 1.0
        p = index
        while p <= end:
            ranks[order[p]] = avg
            p = p + 1
        index = end + 1
    return ranks


def auroc(scores, labels):
    """scores 큰 값이 positive 예측. labels 는 0/1."""
    scores = np.array(scores, dtype=float)
    labels = np.array(labels, dtype=int)
    if len(scores) < 2 or labels.sum() == 0 or labels.sum() == len(labels):
        return float("nan")
    ranks = rank_values(scores.tolist())
    n_pos = int(labels.sum())
    n_neg = int(len(labels) - n_pos)
    pos_rank_sum = float(ranks[labels == 1].sum())
    u = pos_rank_sum - n_pos * (n_pos + 1) / 2.0
    return float(u / (n_pos * n_neg))


def roc_curve(scores, labels):
    """단순 임계값 훑기. 각 임계 s 에서 tpr, fpr."""
    scores = np.array(scores, dtype=float)
    labels = np.array(labels, dtype=int)
    order = np.argsort(-scores, kind="mergesort")
    scores_sorted = scores[order]
    labels_sorted = labels[order]
    n_pos = int(labels.sum())
    n_neg = int(len(labels) - n_pos)
    tpr = []
    fpr = []
    tp = 0
    fp = 0
    i = 0
    tpr.append(0.0)
    fpr.append(0.0)
    while i < len(scores_sorted):
        j = i
        while j + 1 < len(scores_sorted) and scores_sorted[j + 1] == scores_sorted[i]:
            j = j + 1
        k = i
        while k <= j:
            if labels_sorted[k] == 1:
                tp = tp + 1
            else:
                fp = fp + 1
            k = k + 1
        tpr.append(tp / n_pos if n_pos > 0 else 0.0)
        fpr.append(fp / n_neg if n_neg > 0 else 0.0)
        i = j + 1
    return fpr, tpr


# ===============================================================
# 메인
# ===============================================================

def main():
    if os.path.exists(MASK_AREA_CSV) is False or os.path.exists(COMPONENTS_CSV) is False \
            or os.path.exists(CELLS_CSV) is False:
        print("전제 CSV 부재. 22, 24 를 먼저 실행할 것.")
        sys.exit(1)

    print("=" * 92)
    print("25. 집계 단위별 검출력 비교")
    print("=" * 92)

    clean = load_clean_patches()
    components = load_components()
    cells_by_stem = load_cells()

    print("clean patch: {},  성분: {},  cell 총합: {}".format(
        len(clean), len(components),
        sum(len(v) for v in cells_by_stem.values())))
    print()

    # ---------- patch positive 정답 ----------
    # band-only 성분 (전체 픽셀이 band 안) 은 정답 계산에서 제외
    positive_by_stem = defaultdict(int)
    band_only_count = 0
    for c in components:
        if c["area_pixels"] == c["band_pixels"]:
            band_only_count = band_only_count + 1
            continue
        positive_by_stem[c["stem"]] += 1
    print("band-only 성분 (정답에서 제외):", band_only_count)
    print()

    labels = []
    stems = []
    for row in clean:
        stems.append(row["stem"])
        labels.append(1 if positive_by_stem.get(row["stem"], 0) > 0 else 0)
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    print("정답: positive patch {} / negative patch {}".format(n_pos, n_neg))
    print()

    # ---------- 4 지표 산출 ----------
    method_A = [row["a3_defect_ratio"] for row in clean]
    method_B = []   # cell std max
    method_C = []   # top-3 mean
    method_D = []   # top-5 mean
    for stem in stems:
        stds = np.array(cells_by_stem[stem], dtype=float)
        stds_sorted = np.sort(stds)[::-1]   # 내림차순
        method_B.append(float(stds_sorted[0]))
        method_C.append(float(stds_sorted[:3].mean()))
        method_D.append(float(stds_sorted[:5].mean()))

    methods = [
        ("A: patch defect_ratio", method_A),
        ("B: cell std max",       method_B),
        ("C: cell std top-3 mean", method_C),
        ("D: cell std top-5 mean", method_D),
    ]

    # ---------- 5. 분포 및 AUROC ----------
    print("=" * 92)
    print("5. 각 지표의 분포 및 AUROC (Mann-Whitney U)")
    print("=" * 92)
    print("{:<28}{:>6}{:>10}{:>10}{:>10}{:>10}{:>10}{:>10}{:>10}".format(
        "방식", "AUROC", "P.N", "P.p25", "P.p50", "P.p75", "N.N", "N.p50", "N.p75"))
    print("-" * 106)

    aurocs = {}
    for name, scores in methods:
        arr = np.array(scores, dtype=float)
        lab = np.array(labels, dtype=int)
        pos_vals = arr[lab == 1]
        neg_vals = arr[lab == 0]
        pos_q = np.percentile(pos_vals, [25, 50, 75])
        neg_q = np.percentile(neg_vals, [25, 50, 75])
        a = auroc(scores, labels)
        aurocs[name] = a
        print("{:<28}{:>6.3f}{:>10}{:>10.4f}{:>10.4f}{:>10.4f}{:>10}{:>10.4f}{:>10.4f}".format(
            name, a, len(pos_vals), pos_q[0], pos_q[1], pos_q[2],
            len(neg_vals), neg_q[1], neg_q[2]))

    # ---------- ROC 곡선 ----------
    korean = None
    for f in font_manager.fontManager.ttflist:
        if f.name == "Noto Sans CJK KR":
            korean = f.name
            break
    if korean is not None:
        plt.rcParams["font.family"] = korean
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(7.5, 6))
    colors = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd"]
    for (name, scores), color in zip(methods, colors):
        fpr, tpr = roc_curve(scores, labels)
        ax.plot(fpr, tpr, color=color, linewidth=1.6,
                label="{}  AUROC={:.3f}".format(name, aurocs[name]))
    ax.plot([0, 1], [0, 1], "k--", linewidth=0.8, alpha=0.5, label="random")
    ax.set_xlabel("False Positive Rate")
    ax.set_ylabel("True Positive Rate")
    ax.set_title("핀홀 존재 patch 판별 ROC — 4 집계 방식 (clean subset N={}, pos={}, neg={})".format(
        len(labels), n_pos, n_neg))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    ax.grid(alpha=0.25)
    ax.legend(loc="lower right", fontsize=9)
    fig.tight_layout()
    fig.savefig(FIG_ROC, dpi=140)
    plt.close(fig)
    print()
    print("저장:", FIG_ROC)
    print()

    # ---------- 6. 경계 교차 성분 분석 ----------
    print("=" * 92)
    print("6. 경계 교차 성분 분석")
    print("=" * 92)

    all_areas = np.array([c["area_pixels"] for c in components], dtype=int)
    all_touched = np.array([c["n_cells_touched"] for c in components], dtype=int)
    crossers_mask = np.array([c["crosses_cell_boundary"] == 1 for c in components])

    n_touch_counts = defaultdict(int)
    for c in components:
        n_touch_counts[c["n_cells_touched"]] += 1

    print("성분 조각 수 (n_cells_touched) 분포:")
    for k in sorted(n_touch_counts.keys()):
        print("  n_cells_touched = {}: {} 성분".format(k, n_touch_counts[k]))
    print()

    cross_areas = all_areas[crossers_mask]
    solo_areas = all_areas[~crossers_mask]
    print("면적 분포 비교:")
    print("  {:<22}{:>6}{:>10}{:>10}{:>10}{:>10}".format(
        "그룹", "N", "min", "p25", "p50", "p75"))
    for name, arr in [("경계 교차 (>=2 cell)", cross_areas),
                       ("비교차 (1 cell)", solo_areas)]:
        if len(arr) == 0:
            print("  {:<22}{:>6}   -".format(name, 0))
            continue
        q = np.percentile(arr, [0, 25, 50, 75])
        print("  {:<22}{:>6}{:>10.1f}{:>10.1f}{:>10.1f}{:>10.1f}".format(
            name, len(arr), q[0], q[1], q[2], q[3]))
    print()

    total_pieces = int(all_touched.sum())
    total_components = len(components)
    print("cell 단위 과대계수 배수:")
    print("  실제 성분 수                : {}".format(total_components))
    print("  cell 조각 수 총합           : {}".format(total_pieces))
    if total_components > 0:
        print("  배수 (조각 총합 / 실제)     : {:.3f}".format(total_pieces / total_components))
    print()


main()
