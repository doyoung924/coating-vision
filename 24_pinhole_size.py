"""
24. 핀홀 크기 대비 A3 STD 반응 — cell 단위 분석

배경:
  §15-8 에서 A3 (밝기 표준편차) 는 patch (480x640) 내 핀홀 존재를
  구분하지 못한다는 사실이 확인됐다 (pin=1/0 A3 중앙값 동일 또는 역전).
  가설(§15-8 마지막 문단): 64x64 cell 대비 핀홀 크기가 수 픽셀이라
  cell 단위 STD 에도 신호가 안 잡히는 공간 해상도 문제일 수 있다.

  이 스크립트는 그 가설의 실측 검증이다. patch 단위 defect_ratio 가
  70 cell 의 집계라 신호가 묻히므로, cell 단위로 내려가 핀홀 크기와
  cell STD 의 관계를 직접 측정한다.

용어 (§15 이후 고정):
  - patch: 저자 단위 480x640 이미지. results_spc.csv 의 한 행. image_N.jpg
  - cell : A3 단위 64x64. patch 당 70 개 (7 rows x 10 cols).
           cell_index = row * 10 + col
  두 용어 혼용 금지.

좌표계 (§16-0 확정):
  - 원점 (y=0, x=0), 격자 64x64, stride 64, 겹침 없음
  - 7 rows x 10 cols = 70 cell
  - 하단 y >= 448 (32 px 밴드) 는 A3 반응 영역 밖. band 로 별도 집계

대상:
  results_mask_area.csv 에서 area_crack == 0 AND area_delam == 0 인
  patch 259 개. 크랙·박리 STD 교란을 제거한 부분집합.

정의:
  - 8-연결 연결 성분 (cv2.connectedComponentsWithStats, connectivity=8)
  - 최소 크기 컷 없음 (1 px 성분도 포함)
  - cell std = np.std(gray[y0:y0+64, x0:x0+64]) with
    gray = np.mean(image.astype(np.float64), axis=2)
    → 09_spc_monitor.score_patches_std 및 app.score_patches_std_gray 와 동일

산출:
  - results_pinhole_components.csv : 성분 단위
  - results_pinhole_cells.csv      : cell 단위 (259 * 70 = 18,130 행)
  - figures/pinhole_size_distribution.png
  - figures/pinhole_size_vs_std.png

이 스크립트 담는 범위: 1-1, 1-2, 2, 3 (크기 분포, cell 테이블, size vs std).
이론값 대조(4)는 별도 지시 후 추가.
"""

import csv
import os
import sys
import numpy as np
from PIL import Image
import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as font_manager


MASK_AREA_CSV = "results_mask_area.csv"
MASK_DIR = "segmentation/masks"
IMAGE_DIR = "segmentation/images"

COMPONENTS_CSV = "results_pinhole_components.csv"
CELLS_CSV = "results_pinhole_cells.csv"
FIG_SIZE_DIST = "figures/pinhole_size_distribution.png"
FIG_SIZE_VS_STD = "figures/pinhole_size_vs_std.png"

CELL_SIZE = 64
STRIDE = 64
GRID_ROWS = 7           # (480 - 64) // 64 + 1
GRID_COLS = 10          # (640 - 64) // 64 + 1
CELL_PER_PATCH = GRID_ROWS * GRID_COLS   # 70
BAND_Y_MIN = GRID_ROWS * STRIDE          # 448. y >= 448 은 A3 반응 영역 밖


# ===============================================================
# 로딩
# ===============================================================

def load_clean_stems():
    """area_crack == 0 AND area_delam == 0 인 patch 의 stem 목록"""
    result = []
    file_handle = open(MASK_AREA_CSV, "r", encoding="utf-8")
    reader = csv.DictReader(file_handle)
    for row in reader:
        if float(row["area_crack"]) == 0.0 and float(row["area_delam"]) == 0.0:
            record = {}
            record["stem"] = row["stem"]
            record["run_id"] = row["run_id"]
            record["coating_gap"] = int(row["coating_gap"])
            record["position"] = row["position"]
            record["frame_number"] = int(row["frame_number"])
            result.append(record)
    file_handle.close()
    return result


# ===============================================================
# cell 좌표 유틸
# ===============================================================

def cell_of(y, x):
    """(y, x) 픽셀 좌표 → cell_index. y >= BAND_Y_MIN 이면 -1 (band)."""
    if y >= BAND_Y_MIN:
        return -1
    row = y // CELL_SIZE
    col = x // CELL_SIZE
    return row * GRID_COLS + col


def cell_origin(cell_index):
    """cell_index → (y0, x0)"""
    row = cell_index // GRID_COLS
    col = cell_index % GRID_COLS
    return row * CELL_SIZE, col * CELL_SIZE


# ===============================================================
# 성분 추출 및 cell 할당
# ===============================================================

def process_patch(record, gray_std_by_cell):
    """
    한 patch 에 대해:
      - 마스크 로드 → ch2 이진 → 8-연결 성분
      - 성분별 요약 리스트 반환
      - 각 cell 의 pinhole_px, n_components, max_component_px 를 dict 로 반환
    """
    stem = record["stem"]
    mask_path = os.path.join(MASK_DIR, stem + ".png")
    image_path = os.path.join(IMAGE_DIR, stem + ".jpg")

    mask_array = np.array(Image.open(mask_path))
    ch2 = (mask_array[..., 2] > 0).astype(np.uint8)

    # 8-연결 연결 성분
    n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(ch2, connectivity=8)
    # label 0 은 배경

    # 성분별 정보
    components = []
    # cell 단위 카운트
    cell_pinhole_px = np.zeros(CELL_PER_PATCH, dtype=np.int32)
    cell_component_counts = [set() for _ in range(CELL_PER_PATCH)]  # cell → 걸친 성분 id 집합
    cell_max_piece = np.zeros(CELL_PER_PATCH, dtype=np.int32)

    label_index = 1
    while label_index < n_labels:
        area = int(stats[label_index, cv2.CC_STAT_AREA])
        bx = int(stats[label_index, cv2.CC_STAT_LEFT])
        by = int(stats[label_index, cv2.CC_STAT_TOP])
        bw = int(stats[label_index, cv2.CC_STAT_WIDTH])
        bh = int(stats[label_index, cv2.CC_STAT_HEIGHT])
        cx = float(centroids[label_index, 0])
        cy = float(centroids[label_index, 1])
        eq_diameter = 2.0 * float(np.sqrt(area / np.pi))

        # 이 성분의 픽셀 위치
        ys, xs = np.where(labels == label_index)

        band_pixels = 0
        # cell 별 조각 픽셀 수 계산
        per_cell = {}   # cell_index → pixel count
        i = 0
        while i < len(ys):
            y_val = int(ys[i])
            x_val = int(xs[i])
            if y_val >= BAND_Y_MIN:
                band_pixels = band_pixels + 1
            else:
                cell_idx = cell_of(y_val, x_val)
                if cell_idx not in per_cell:
                    per_cell[cell_idx] = 0
                per_cell[cell_idx] = per_cell[cell_idx] + 1
            i = i + 1

        # cell 단위 집계에 반영
        cells_touched = []
        max_piece = 0
        max_piece_cell = -1
        for cell_idx in per_cell:
            piece = per_cell[cell_idx]
            cell_pinhole_px[cell_idx] = cell_pinhole_px[cell_idx] + piece
            cell_component_counts[cell_idx].add(label_index)
            if piece > cell_max_piece[cell_idx]:
                cell_max_piece[cell_idx] = piece
            cells_touched.append(cell_idx)
            if piece > max_piece:
                max_piece = piece
                max_piece_cell = cell_idx

        component = {}
        component["stem"] = stem
        component["run_id"] = record["run_id"]
        component["coating_gap"] = record["coating_gap"]
        component["position"] = record["position"]
        component["frame_number"] = record["frame_number"]
        component["component_id"] = label_index
        component["area_pixels"] = area
        component["bbox_x"] = bx
        component["bbox_y"] = by
        component["bbox_w"] = bw
        component["bbox_h"] = bh
        component["center_x"] = round(cx, 3)
        component["center_y"] = round(cy, 3)
        component["equivalent_diameter"] = round(eq_diameter, 3)
        component["band_pixels"] = band_pixels
        component["n_cells_touched"] = len(cells_touched)
        component["crosses_cell_boundary"] = 1 if len(cells_touched) > 1 else 0
        component["max_piece_pixels"] = max_piece
        component["max_piece_cell_index"] = max_piece_cell
        components.append(component)

        label_index = label_index + 1

    # cell std 계산 (gray = mean over channels)
    image_array = np.array(Image.open(image_path).convert("RGB"))
    gray = np.mean(image_array.astype(np.float64), axis=2)

    cell_records = []
    idx = 0
    while idx < CELL_PER_PATCH:
        row = idx // GRID_COLS
        col = idx % GRID_COLS
        y0 = row * CELL_SIZE
        x0 = col * CELL_SIZE
        cell_gray = gray[y0:y0 + CELL_SIZE, x0:x0 + CELL_SIZE]
        std_val = float(np.std(cell_gray))

        cell_records.append({
            "stem": stem,
            "run_id": record["run_id"],
            "coating_gap": record["coating_gap"],
            "position": record["position"],
            "frame_number": record["frame_number"],
            "cell_index": idx,
            "row": row,
            "col": col,
            "std": round(std_val, 5),
            "pinhole_px": int(cell_pinhole_px[idx]),
            "n_components": len(cell_component_counts[idx]),
            "max_component_px": int(cell_max_piece[idx]),
        })
        idx = idx + 1

    return components, cell_records


# ===============================================================
# 상관 (자체 구현)
# ===============================================================

def pearson_r(x_values, y_values):
    x = np.array(x_values, dtype=float)
    y = np.array(y_values, dtype=float)
    if len(x) < 3:
        return float("nan")
    dx = x - x.mean()
    dy = y - y.mean()
    denom = float(np.sqrt((dx * dx).sum() * (dy * dy).sum()))
    if denom == 0.0:
        return float("nan")
    r = float((dx * dy).sum() / denom)
    if r > 1.0:
        r = 1.0
    if r < -1.0:
        r = -1.0
    return r


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


def spearman_r(x_values, y_values):
    if len(x_values) < 3:
        return float("nan")
    rx = rank_values(x_values).tolist()
    ry = rank_values(y_values).tolist()
    return pearson_r(rx, ry)


# ===============================================================
# 메인
# ===============================================================

def main():
    for path in [COMPONENTS_CSV, CELLS_CSV]:
        if os.path.exists(path):
            print("이미 존재:", path, "— 사용자가 옮긴 뒤 재실행할 것.")
            sys.exit(1)

    if os.path.exists(MASK_AREA_CSV) is False:
        print(MASK_AREA_CSV, "없음. 22 를 먼저 실행할 것.")
        sys.exit(1)

    print("=" * 88)
    print("24. 핀홀 크기 대비 A3 STD 반응 — cell 단위")
    print("=" * 88)
    print("좌표계: 480x640 이미지, 64x64 cell, stride 64, 7 rows x 10 cols = 70 cell")
    print("       y >= {} 는 A3 반응 영역 밖 (band). cell 할당 제외.".format(BAND_Y_MIN))
    print("대상 : results_mask_area.csv 의 area_crack==0 AND area_delam==0 부분집합")
    print()

    clean = load_clean_stems()
    print("clean patch 수:", len(clean))
    print("예상 cell 수 :", len(clean) * CELL_PER_PATCH)
    print()

    all_components = []
    all_cells = []
    band_component_count = 0
    band_pixel_total = 0
    band_only_component_count = 0

    gray_std_by_cell = {}  # 안 씀
    processed = 0
    for record in clean:
        components, cell_records = process_patch(record, gray_std_by_cell)
        all_components.extend(components)
        all_cells.extend(cell_records)

        for c in components:
            if c["band_pixels"] > 0:
                band_component_count = band_component_count + 1
                band_pixel_total = band_pixel_total + c["band_pixels"]
                if c["band_pixels"] == c["area_pixels"]:
                    band_only_component_count = band_only_component_count + 1

        processed = processed + 1
        if processed % 50 == 0:
            print("  processed {}/{}".format(processed, len(clean)))

    print()
    print("처리 patch :", processed)
    print("성분 총합  :", len(all_components))
    print("cell 총합  :", len(all_cells))
    print("band 픽셀을 포함한 성분 수:", band_component_count,
          "(그중 band 픽셀만 있는 성분:", str(band_only_component_count) + ")")
    print("band 픽셀 총합:", band_pixel_total)
    print()

    # 저장
    if len(all_components) > 0:
        comp_fields = list(all_components[0].keys())
        with open(COMPONENTS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=comp_fields)
            writer.writeheader()
            for row in all_components:
                writer.writerow(row)
        print("저장:", COMPONENTS_CSV, "(", len(all_components), "행 )")
    else:
        print("성분 0 — CSV 저장 생략")

    cell_fields = list(all_cells[0].keys())
    with open(CELLS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=cell_fields)
        writer.writeheader()
        for row in all_cells:
            writer.writerow(row)
    print("저장:", CELLS_CSV, "(", len(all_cells), "행 )")
    print()

    # =====================================================
    # 2. 크기 분포
    # =====================================================
    print("=" * 88)
    print("2. 성분 크기 분포")
    print("=" * 88)

    if len(all_components) > 0:
        areas = np.array([c["area_pixels"] for c in all_components], dtype=int)
        diams = np.array([c["equivalent_diameter"] for c in all_components], dtype=float)

        def summary_line(name, values):
            v = np.asarray(values)
            q = np.percentile(v, [0, 25, 50, 75, 90, 100])
            print("  {:<22} N={:>5}  min={:>7.2f}  p25={:>7.2f}  p50={:>7.2f}  "
                  "p75={:>7.2f}  p90={:>7.2f}  max={:>7.2f}".format(
                name, len(v), q[0], q[1], q[2], q[3], q[4], q[5]))

        summary_line("area_pixels", areas)
        summary_line("equivalent_diameter", diams)

        crossers = sum(1 for c in all_components if c["crosses_cell_boundary"] == 1)
        print()
        print("  경계 교차 성분: {} / {}  ({:.3f})".format(
            crossers, len(all_components), crossers / len(all_components)))
        band_ratio = band_component_count / len(all_components)
        print("  band 픽셀 포함 성분: {} / {}  ({:.3f})".format(
            band_component_count, len(all_components), band_ratio))
    else:
        print("  성분 없음")

    print()

    # =====================================================
    # 3. 크기 대비 STD
    # =====================================================
    print("=" * 88)
    print("3. pinhole_px 구간별 cell STD 반응")
    print("=" * 88)

    cells_pin = np.array([c["pinhole_px"] for c in all_cells], dtype=int)
    cells_std = np.array([c["std"] for c in all_cells], dtype=float)

    zero_mask = (cells_pin == 0)
    zero_std_median = float(np.median(cells_std[zero_mask]))
    zero_std_p25 = float(np.percentile(cells_std[zero_mask], 25))
    zero_std_p75 = float(np.percentile(cells_std[zero_mask], 75))
    print("  기준선 (pinhole_px == 0): N={},  std p25/p50/p75 = {:.4f} / {:.4f} / {:.4f}".format(
        int(zero_mask.sum()), zero_std_p25, zero_std_median, zero_std_p75))
    print()

    bins = [
        ("0", lambda v: v == 0),
        ("1-5", lambda v: (v >= 1) & (v <= 5)),
        ("6-20", lambda v: (v >= 6) & (v <= 20)),
        ("21-100", lambda v: (v >= 21) & (v <= 100)),
        ("101-500", lambda v: (v >= 101) & (v <= 500)),
        ("500+", lambda v: v > 500),
    ]

    print("  {:<10}{:>7}{:>10}{:>10}{:>10}{:>16}".format(
        "구간", "N", "p25", "p50", "p75", "기준선 초과 비율"))
    print("  " + "-" * 65)
    for name, cond in bins:
        mask = cond(cells_pin)
        n_in = int(mask.sum())
        if n_in == 0:
            print("  {:<10}{:>7}   -".format(name, 0))
            continue
        sub = cells_std[mask]
        q = np.percentile(sub, [25, 50, 75])
        above = float((sub > zero_std_median).mean())
        print("  {:<10}{:>7}{:>10.4f}{:>10.4f}{:>10.4f}{:>16.3f}".format(
            name, n_in, q[0], q[1], q[2], above))
    print()

    # Spearman
    positive_mask = cells_pin > 0
    n_pos = int(positive_mask.sum())
    if n_pos >= 3:
        rho_pos = spearman_r(cells_pin[positive_mask].tolist(), cells_std[positive_mask].tolist())
    else:
        rho_pos = float("nan")
    rho_all = spearman_r(cells_pin.tolist(), cells_std.tolist())
    print("  Spearman(pinhole_px, std)")
    print("    pinhole_px > 0 cell 만 (N={}): {:.4f}".format(n_pos, rho_pos))
    print("    전체 cell (N={})           : {:.4f}".format(len(cells_pin), rho_all))
    print()

    # =====================================================
    # 그림 1: 성분 크기 분포
    # =====================================================
    korean = None
    for f in font_manager.fontManager.ttflist:
        if f.name == "Noto Sans CJK KR":
            korean = f.name
            break
    if korean is not None:
        plt.rcParams["font.family"] = korean
    plt.rcParams["axes.unicode_minus"] = False

    if len(all_components) > 0:
        fig, ax = plt.subplots(figsize=(9, 5))
        # 로그 스케일용 최소값 1 로 보정 (area는 최소 1)
        area_vals = np.clip(areas, 1, None)
        bins_edges = np.logspace(0, np.log10(max(area_vals.max(), 2)), 40)
        ax.hist(area_vals, bins=bins_edges, color="#1f77b4", edgecolor="white", alpha=0.85)
        ax.set_xscale("log")
        ax.set_xlabel("성분 면적 (픽셀, 로그)")
        ax.set_ylabel("성분 수")
        ax.set_title("핀홀 연결 성분 크기 분포 (crack=0 AND delam=0 부분집합, N={} 성분, {} patches)".format(
            len(all_components), len(clean)))
        ax.grid(alpha=0.25)
        fig.tight_layout()
        fig.savefig(FIG_SIZE_DIST, dpi=140)
        plt.close(fig)
        print("저장:", FIG_SIZE_DIST)

    # =====================================================
    # 그림 2: pinhole_px vs std 산점도 (x 로그)
    # =====================================================
    fig, ax = plt.subplots(figsize=(9, 5.5))
    pos_x = cells_pin[cells_pin > 0]
    pos_y = cells_std[cells_pin > 0]
    zero_y = cells_std[cells_pin == 0]

    # x=0 은 로그 스케일에 안 그려지므로 좌측 마진에 표시 (jitter 없이 dashed line 기준선)
    ax.scatter(pos_x, pos_y, s=10, alpha=0.5, color="#d62728", edgecolors="none",
               label="pinhole_px > 0  (N={})".format(len(pos_x)))

    # 기준선: pinhole_px==0 median/quartile 을 수평 밴드로
    ax.axhspan(zero_std_p25, zero_std_p75, alpha=0.15, color="#1f77b4",
               label="pinhole_px == 0  p25~p75  (N={})".format(len(zero_y)))
    ax.axhline(zero_std_median, color="#1f77b4", linestyle="--", linewidth=1.2,
               label="pinhole_px == 0  median = {:.3f}".format(zero_std_median))

    ax.set_xscale("log")
    ax.set_xlabel("cell 안 핀홀 픽셀 수 (로그)")
    ax.set_ylabel("cell 밝기 표준편차 (STD)")
    ax.set_title("cell 안 핀홀 픽셀 수 vs cell STD  ·  crack=0 AND delam=0, N={} cell".format(len(all_cells)))
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    fig.savefig(FIG_SIZE_VS_STD, dpi=140)
    plt.close(fig)
    print("저장:", FIG_SIZE_VS_STD)


main()
