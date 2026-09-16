"""
27. 고 std 음성 cell 의 경계 가설 정량 검증

배경 (§16 후속):
  사용자 육안 판정: A 시트(고 std 음성 cell)의 패턴은 코팅 영역 경계.
  결함이 아니며 라벨 누락도 아니다. 넓고 부드러운 직선 밝기 전이이며
  같은 이미지의 같은 열/행에 연속 출현한다.
  B 시트(핀홀)는 작고 둥근 국소 반점.

이 스크립트는 그 판정을 세 가지 정량 지표로 검증한다.

지표:
  1) 연속성: 같은 stem 내에서 4-이웃 (row±1 같은 col, col±1 같은 row) 에
     같은 집합의 다른 cell 이 있는 비율.
     경계라면 연속 출현, 국소 결함이라면 연속 출현하지 않아야 한다.
  2) 이봉성 (bimodality): 4,096 픽셀을 밝기 정렬 후
     상위 절반 평균 - 하위 절반 평균 = Δ.
     Δ / std 로 정규화하면 이봉성이 강할수록 큰 값 (경계) ,
     균질 노이즈면 작은 값 (~1.13, 정규분포 근사).
  3) 갭 상관: H 집합 셀의 Δ 중앙값을 시퀀스별로 뽑아
     갭 조건(600→1100)과의 방향성만 관찰. 경계 대비가 갭에 따라
     체계적으로 변하는지 확인.

새 검출 기법·새 지표 도입 금지. std 는 24 가 저장한 값 그대로.
Δ 는 픽셀 정렬 후 절반씩 평균이므로 새 "검출" 이 아니라 분포 요약.

산출: stdout 표만. 그림은 §12 이후 필요 시 별도 지시.
"""

import csv
import os
import sys
import numpy as np
from PIL import Image
from collections import defaultdict


CELLS_CSV = "results_pinhole_cells.csv"
IMAGE_DIR = "segmentation/images"

CELL_SIZE = 64
STRIDE = 64
GRID_ROWS = 7
GRID_COLS = 10


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


def cell_gray(stem, row, col):
    path = os.path.join(IMAGE_DIR, stem + ".jpg")
    image = np.array(Image.open(path).convert("RGB"))
    gray = np.mean(image.astype(np.float64), axis=2)
    y0 = row * STRIDE
    x0 = col * STRIDE
    return gray[y0:y0 + CELL_SIZE, x0:x0 + CELL_SIZE]


def continuity(cells):
    """
    입력 리스트의 각 cell 에 대해:
      - vertical_neighbor: 같은 (stem, col) 에 row±1 인 다른 cell 이 있음
      - horizontal_neighbor: 같은 (stem, row) 에 col±1 인 다른 cell 이 있음
      - any_neighbor: 위 둘 중 하나 이상
    반환: dict of ratios and counts
    """
    key_set = set((c["stem"], c["row"], c["col"]) for c in cells)
    v_count = 0
    h_count = 0
    any_count = 0
    for c in cells:
        stem = c["stem"]
        r = c["row"]
        col = c["col"]
        v = ((stem, r - 1, col) in key_set) or ((stem, r + 1, col) in key_set)
        h = ((stem, r, col - 1) in key_set) or ((stem, r, col + 1) in key_set)
        if v: v_count = v_count + 1
        if h: h_count = h_count + 1
        if v or h: any_count = any_count + 1
    n = len(cells)
    return {
        "n": n,
        "vertical": v_count,
        "vertical_ratio": v_count / max(1, n),
        "horizontal": h_count,
        "horizontal_ratio": h_count / max(1, n),
        "any": any_count,
        "any_ratio": any_count / max(1, n),
    }


def bimodality(image_cell):
    """
    (H, W) 그레이 픽셀 배열 → 상위 절반과 하위 절반 평균 차이 Δ
    반환: Δ, Δ/std
    """
    flat = image_cell.ravel()
    order = np.sort(flat)
    n = len(order)
    half = n // 2
    lower_mean = float(order[:half].mean())
    upper_mean = float(order[half:].mean())
    delta = upper_mean - lower_mean
    sd = float(np.std(flat))
    if sd == 0:
        norm = float("nan")
    else:
        norm = delta / sd
    return delta, norm, lower_mean, upper_mean


def summarize(values, name):
    if len(values) == 0:
        print("  {:<40} N={}".format(name, 0))
        return
    a = np.array(values, dtype=float)
    q = np.percentile(a, [25, 50, 75])
    print("  {:<40} N={:>5}  p25={:>7.3f}  p50={:>7.3f}  p75={:>7.3f}  mean={:>7.3f}".format(
        name, len(a), q[0], q[1], q[2], float(a.mean())))


def main():
    if os.path.exists(CELLS_CSV) is False:
        print(CELLS_CSV, "없음.")
        sys.exit(1)

    cells = load_cells()
    print("=" * 92)
    print("27. 고 std 음성 cell 의 경계 가설 정량 검증")
    print("=" * 92)

    negatives = [c for c in cells if c["pinhole_px"] == 0]
    positives = [c for c in cells if c["pinhole_px"] > 0]

    neg_stds = np.array([c["std"] for c in negatives], dtype=float)
    threshold = float(np.percentile(neg_stds, 99))
    H = [c for c in negatives if c["std"] >= threshold]
    P = positives

    # 기준선 (음성 중앙값 근처) - 이봉성 대조군
    neg_median = float(np.median(neg_stds))
    N_baseline = sorted(negatives, key=lambda x: abs(x["std"] - neg_median))[:200]

    print("H (pinhole_px==0, std >= p99 = {:.3f}): {} cell".format(threshold, len(H)))
    print("P (pinhole_px>0)                       : {} cell".format(len(P)))
    print("N (pinhole_px==0, std ≈ median {:.3f}): {} cell (nearest 200)".format(
        neg_median, len(N_baseline)))
    print()

    # =====================================================
    # 1. 연속성 지표
    # =====================================================
    print("=" * 92)
    print("11-1. 연속성 지표 (같은 stem 내 4-이웃 존재 비율)")
    print("=" * 92)
    print("  경계 가설이 맞다면 H 는 vertical/horizontal 인접 비율이 높을 것.")
    print("  국소 결함인 P 는 인접 비율이 낮을 것.")
    print()
    print("  {:<40}{:>6}{:>10}{:>10}{:>10}{:>10}{:>10}{:>10}".format(
        "집합", "N", "vert#", "vert%", "horz#", "horz%", "any#", "any%"))
    print("  " + "-" * 96)
    for name, group in [("H (고 std 음성)", H), ("P (pinhole_px>0)", P),
                         ("N (음성 기준선, std≈median)", N_baseline)]:
        r = continuity(group)
        print("  {:<40}{:>6}{:>10}{:>10.3f}{:>10}{:>10.3f}{:>10}{:>10.3f}".format(
            name, r["n"], r["vertical"], r["vertical_ratio"],
            r["horizontal"], r["horizontal_ratio"], r["any"], r["any_ratio"]))
    print()

    # =====================================================
    # 2. 이봉성 (bimodality)
    # =====================================================
    print("=" * 92)
    print("11-2. 이봉성 지표 (상위 절반 평균 - 하위 절반 평균 = Δ)")
    print("=" * 92)
    print("  경계면(두 밝기 대역이 나뉘어 있음)이면 Δ/std 가 크다 (~1.7 근처).")
    print("  균질 노이즈면 Δ/std ≈ 1.13 (반정규분포).")
    print()

    def collect_bimodality(group):
        deltas = []
        norms = []
        lows = []
        highs = []
        for c in group:
            img = cell_gray(c["stem"], c["row"], c["col"])
            d, n, lo, hi = bimodality(img)
            deltas.append(d)
            norms.append(n)
            lows.append(lo)
            highs.append(hi)
        return deltas, norms, lows, highs

    h_delta, h_norm, h_low, h_high = collect_bimodality(H)
    p_delta, p_norm, p_low, p_high = collect_bimodality(P)
    n_delta, n_norm, _, _ = collect_bimodality(N_baseline[:60])   # 시간 절약

    summarize(h_delta, "H  Δ (상위-하위 평균 차, 픽셀)")
    summarize(h_norm,  "H  Δ / std")
    print()
    summarize(p_delta, "P  Δ (상위-하위 평균 차)")
    summarize(p_norm,  "P  Δ / std")
    print()
    summarize(n_delta, "N  Δ (기준선, N=60 sample)")
    summarize(n_norm,  "N  Δ / std")
    print()

    # =====================================================
    # 3. 시퀀스별(갭별) Δ 중앙값 — H 집합
    # =====================================================
    print("=" * 92)
    print("11-3. H 집합 Δ 를 시퀀스별로 (경계 대비가 갭에 따라 커지는지)")
    print("=" * 92)
    print()

    by_seq_delta = defaultdict(list)
    by_seq_stds = defaultdict(list)
    by_seq_lo = defaultdict(list)
    by_seq_hi = defaultdict(list)
    idx = 0
    while idx < len(H):
        c = H[idx]
        key = (c["run_id"], c["coating_gap"], c["position"])
        by_seq_delta[key].append(h_delta[idx])
        by_seq_stds[key].append(c["std"])
        by_seq_lo[key].append(h_low[idx])
        by_seq_hi[key].append(h_high[idx])
        idx = idx + 1

    print("  {:<40}{:>6}{:>10}{:>10}{:>10}{:>10}{:>10}".format(
        "시퀀스", "N", "Δ p50", "Δ p75", "std p50", "밝은 평균 p50", "어두운 p50"))
    print("  " + "-" * 96)
    seq_keys = sorted(by_seq_delta.keys(), key=lambda k: (k[1], k[0], k[2]))
    for k in seq_keys:
        deltas = np.array(by_seq_delta[k])
        stds = np.array(by_seq_stds[k])
        lo = np.array(by_seq_lo[k])
        hi = np.array(by_seq_hi[k])
        q_d = np.percentile(deltas, [50, 75])
        q_s = np.percentile(stds, [50])
        q_lo = np.percentile(lo, [50])
        q_hi = np.percentile(hi, [50])
        print("  {:<40}{:>6}{:>10.3f}{:>10.3f}{:>10.3f}{:>10.2f}{:>10.2f}".format(
            str(k), len(deltas), q_d[0], q_d[1], q_s[0], q_hi[0], q_lo[0]))
    print()


main()
