"""
29. 캐스케이드 실측 (오프라인 시뮬레이션)

배경 (§17):
  /inspect 는 조건 없는 순차 실행이며 median 60.5 ms/patch. A3 → YOLO 캐스케이드로
  A3 통과분에만 YOLO 를 돌리면 절감 여지가 있다. 코드는 수정하지 말고 오프라인에서
  전량 데이터를 측정해 임계 스윕만 수행한다.

게이트 신호:
  - 주   : cell std max (patch 내 70 cell 의 std 최대값). §25 에서 AUROC 0.657 확인
  - 비교 : patch defect_ratio (results_spc.csv, 09 가 산출한 값). §25 AUROC 0.567

정답:
  (A) YOLO 기준 — 같은 conf 0.25 임계에서 검출 1 개 이상이면 positive.
      게이트의 본래 목적("YOLO 로 보낼 가치가 있는가")에 부합
  (B) 마스크 기준 — ch2(Pinhole)에서 8-연결 성분이 존재하면 positive.
      단 성분 전 픽셀이 y >= 448 밴드 안(A3 반응 영역 밖)에 있는 경우 제외

대상: results_spc.csv 전체 2,227 patch (clean 부분집합 아님)

시간 상수 (§17-5 재측정 median / p90):
  A3        : 10.6 / 12.7 ms/patch
  YOLO infer: 46.8 / 86.8 ms/patch
  YOLO pre_post: 3.1 / 6.5 ms/patch
  형태 분석 : 무시 (median 0.001)

  총 전수 처리 median = 10.6 + (46.8 + 3.1) = 60.5 ms/patch
  게이트 도입 후 median = 10.6 + 통과율 × (46.8 + 3.1) = 10.6 + p × 49.9
  p90 기준            = 12.7 + 통과율 × (86.8 + 6.5) = 12.7 + p × 93.3

산출:
  - results_yolo_cache.csv          : (stem, n_detections, max_conf, boxes JSON)
  - results_cascade_sweep.csv       : (gate, threshold_pct, threshold_val, pass_rate,
                                       miss_rate_yolo, miss_rate_mask,
                                       ms_per_patch_median, ms_per_patch_p90, saving_pct)
  - figures/cascade_tradeoff.png
  - figures/cascade_passrate_by_sequence.png

원칙:
  - YOLO 는 전량 1 회만 실행. 캐시 재사용 (파일 존재 시 로드)
  - /inspect 코드 미수정
  - 절감이 작아도 그대로 보고
"""

import csv
import json
import os
import sys
import time
import numpy as np
from PIL import Image
import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as font_manager
from collections import defaultdict


MASK_AREA_CSV = "results_mask_area.csv"
IMAGE_DIR = "segmentation/images"
MASK_DIR = "segmentation/masks"
YOLO_WEIGHTS = "runs/pinhole_v1/weights/best.pt"

YOLO_CACHE_CSV = "results_yolo_cache.csv"
SWEEP_CSV = "results_cascade_sweep.csv"
FIG_TRADEOFF = "figures/cascade_tradeoff.png"
FIG_PASSRATE = "figures/cascade_passrate_by_sequence.png"

CELL_SIZE = 64
STRIDE = 64
GRID_ROWS = 7
GRID_COLS = 10
BAND_Y_MIN = GRID_ROWS * STRIDE   # 448

# §17-5 재측정 median / p90
T_A3_MEDIAN = 10.6
T_A3_P90 = 12.7
T_YOLO_INFER_MEDIAN = 46.8
T_YOLO_INFER_P90 = 86.8
T_YOLO_PP_MEDIAN = 3.1
T_YOLO_PP_P90 = 6.5

FULL_MS_MEDIAN = T_A3_MEDIAN + T_YOLO_INFER_MEDIAN + T_YOLO_PP_MEDIAN  # 60.5
FULL_MS_P90 = T_A3_P90 + T_YOLO_INFER_P90 + T_YOLO_PP_P90              # 106.0

YOLO_CONF = 0.25


# ===============================================================
# 유틸
# ===============================================================

def load_patch_index():
    """results_mask_area.csv 를 그대로 리스트로. stem 순서 유지."""
    rows = []
    with open(MASK_AREA_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "stem": row["stem"],
                "run_id": row["run_id"],
                "coating_gap": int(row["coating_gap"]),
                "position": row["position"],
                "frame_number": int(row["frame_number"]),
                "a3_defect_ratio": float(row["a3_defect_ratio"]),
            })
    return rows


def compute_cell_std_max(image_np):
    """patch 이미지에서 70 cell 의 std 를 계산하고 max 반환."""
    gray = np.mean(image_np.astype(np.float64), axis=2)
    max_std = 0.0
    y = 0
    while y + CELL_SIZE <= gray.shape[0]:
        x = 0
        while x + CELL_SIZE <= gray.shape[1]:
            v = float(np.std(gray[y:y + CELL_SIZE, x:x + CELL_SIZE]))
            if v > max_std:
                max_std = v
            x = x + STRIDE
        y = y + STRIDE
    return max_std


# ===============================================================
# YOLO 캐시
# ===============================================================

def build_yolo_cache(patches):
    """
    캐시 존재 시 로드. 없으면 전량 1 회 실행 후 저장.
    반환: {stem: {n_detections, max_conf, boxes}}
    """
    if os.path.exists(YOLO_CACHE_CSV):
        print("YOLO 캐시 발견 → 로드:", YOLO_CACHE_CSV)
        cache = {}
        with open(YOLO_CACHE_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cache[row["stem"]] = {
                    "n_detections": int(row["n_detections"]),
                    "max_conf": float(row["max_conf"]),
                    "boxes": json.loads(row["boxes"]),
                }
        return cache

    print("YOLO 캐시 없음 → 2,227 patch 전량 1 회 추론")
    from ultralytics import YOLO
    model = YOLO(YOLO_WEIGHTS)

    cache = {}
    total = len(patches)
    processed = 0
    start = time.time()
    for record in patches:
        stem = record["stem"]
        image_path = os.path.join(IMAGE_DIR, stem + ".jpg")
        image_np = np.array(Image.open(image_path).convert("RGB"))
        predictions = model.predict(source=image_np, conf=YOLO_CONF, verbose=False)
        first = predictions[0]
        boxes_list = []
        max_conf = 0.0
        if first.boxes is not None:
            boxes_xyxy = first.boxes.xyxy.cpu().numpy()
            confs = first.boxes.conf.cpu().numpy()
            i = 0
            while i < len(boxes_xyxy):
                bx = boxes_xyxy[i].tolist()
                cf = float(confs[i])
                boxes_list.append([
                    int(round(bx[0])), int(round(bx[1])),
                    int(round(bx[2])), int(round(bx[3])),
                    round(cf, 4)])
                if cf > max_conf:
                    max_conf = cf
                i = i + 1
        cache[stem] = {
            "n_detections": len(boxes_list),
            "max_conf": max_conf,
            "boxes": boxes_list,
        }
        processed = processed + 1
        if processed % 200 == 0:
            elapsed = time.time() - start
            rate = processed / elapsed
            eta = (total - processed) / max(0.001, rate)
            print("  진행 {}/{} ({:.1f}s, ETA {:.0f}s)".format(processed, total, elapsed, eta))

    print("YOLO 추론 완료: {:.1f}s".format(time.time() - start))

    # 저장
    with open(YOLO_CACHE_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["stem", "n_detections", "max_conf", "boxes"])
        writer.writeheader()
        for stem in cache:
            entry = cache[stem]
            writer.writerow({
                "stem": stem,
                "n_detections": entry["n_detections"],
                "max_conf": round(entry["max_conf"], 4),
                "boxes": json.dumps(entry["boxes"]),
            })
    print("저장:", YOLO_CACHE_CSV)
    return cache


# ===============================================================
# 마스크 정답 (B) — 8-연결, band-only 제외
# ===============================================================

def mask_positive(stem):
    mask_path = os.path.join(MASK_DIR, stem + ".png")
    mask_array = np.array(Image.open(mask_path))
    ch2 = (mask_array[..., 2] > 0).astype(np.uint8)
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(ch2, connectivity=8)
    idx = 1
    while idx < n_labels:
        area = int(stats[idx, cv2.CC_STAT_AREA])
        ys, xs = np.where(labels == idx)
        band_only = bool(np.all(ys >= BAND_Y_MIN))
        if band_only is False:
            return 1
        idx = idx + 1
    return 0


# ===============================================================
# 메인
# ===============================================================

def main():
    if os.path.exists(SWEEP_CSV):
        print("이미 존재:", SWEEP_CSV, "— 사용자가 옮긴 뒤 재실행할 것.")
        sys.exit(1)

    if os.path.exists(MASK_AREA_CSV) is False:
        print(MASK_AREA_CSV, "없음.")
        sys.exit(1)

    print("=" * 92)
    print("29. 캐스케이드 오프라인 시뮬레이션")
    print("=" * 92)
    print("전수 처리 median = {:.1f} ms/patch,  p90 = {:.1f} ms/patch".format(
        FULL_MS_MEDIAN, FULL_MS_P90))
    print()

    patches = load_patch_index()
    print("patch 수:", len(patches))

    # ---- cell std max ----
    print("cell std max 계산 중 (2,227 patch)...")
    stems_ordered = []
    cell_max_values = []
    defect_ratios = []
    seq_by_stem = {}
    start = time.time()
    for record in patches:
        stem = record["stem"]
        image_path = os.path.join(IMAGE_DIR, stem + ".jpg")
        image_np = np.array(Image.open(image_path).convert("RGB"))
        max_std = compute_cell_std_max(image_np)
        stems_ordered.append(stem)
        cell_max_values.append(max_std)
        defect_ratios.append(record["a3_defect_ratio"])
        seq_by_stem[stem] = (record["run_id"], record["coating_gap"], record["position"])
    print("  완료: {:.1f}s".format(time.time() - start))

    cell_max_values = np.array(cell_max_values, dtype=float)
    defect_ratios = np.array(defect_ratios, dtype=float)

    # ---- YOLO 캐시 ----
    yolo_cache = build_yolo_cache(patches)

    # ---- 정답 (A) YOLO ----
    label_yolo = np.array([1 if yolo_cache[s]["n_detections"] >= 1 else 0 for s in stems_ordered], dtype=int)
    print("정답 (A) YOLO positive:", int(label_yolo.sum()), "/", len(label_yolo))

    # ---- 정답 (B) 마스크 ----
    print("마스크 정답 (B) 계산 중...")
    start = time.time()
    label_mask = np.zeros(len(stems_ordered), dtype=int)
    for i, stem in enumerate(stems_ordered):
        label_mask[i] = mask_positive(stem)
    print("  완료: {:.1f}s".format(time.time() - start))
    print("정답 (B) 마스크 positive:", int(label_mask.sum()), "/", len(label_mask))
    print()

    # ---- 임계 스윕 ----
    print("=" * 92)
    print("임계 스윕 (p10 ~ p99, 20 단계)")
    print("=" * 92)

    percentiles = np.linspace(10, 99, 20)
    sweep_rows = []
    gate_map = {"cell_std_max": cell_max_values, "defect_ratio": defect_ratios}

    for gate_name in ["cell_std_max", "defect_ratio"]:
        gate = gate_map[gate_name]
        for pct in percentiles:
            thr = float(np.percentile(gate, pct))
            pass_mask = gate >= thr
            pass_rate = float(pass_mask.mean())
            # 미검률: 정답 positive 인데 게이트에서 막힌 비율
            miss_yolo = 0.0
            n_pos_y = int(label_yolo.sum())
            if n_pos_y > 0:
                miss_yolo = float(((label_yolo == 1) & (pass_mask == False)).sum()) / n_pos_y
            miss_mask = 0.0
            n_pos_m = int(label_mask.sum())
            if n_pos_m > 0:
                miss_mask = float(((label_mask == 1) & (pass_mask == False)).sum()) / n_pos_m
            ms_med = T_A3_MEDIAN + pass_rate * (T_YOLO_INFER_MEDIAN + T_YOLO_PP_MEDIAN)
            ms_p90 = T_A3_P90 + pass_rate * (T_YOLO_INFER_P90 + T_YOLO_PP_P90)
            saving = (FULL_MS_MEDIAN - ms_med) / FULL_MS_MEDIAN
            sweep_rows.append({
                "gate": gate_name,
                "threshold_pct": round(float(pct), 3),
                "threshold_val": round(thr, 4),
                "pass_rate": round(pass_rate, 4),
                "miss_rate_yolo": round(miss_yolo, 4),
                "miss_rate_mask": round(miss_mask, 4),
                "ms_per_patch_median": round(ms_med, 3),
                "ms_per_patch_p90": round(ms_p90, 3),
                "saving_pct": round(saving * 100, 2),
            })

    # 표 출력
    for gate_name in ["cell_std_max", "defect_ratio"]:
        print()
        print(gate_name)
        print("  {:>7}{:>10}{:>10}{:>12}{:>12}{:>12}{:>10}{:>10}".format(
            "pct", "thr", "pass%", "miss_YOLO%", "miss_MASK%", "ms_med", "ms_p90", "save%"))
        for row in sweep_rows:
            if row["gate"] != gate_name: continue
            print("  {:>7.1f}{:>10.4f}{:>10.2%}{:>12.2%}{:>12.2%}{:>12.2f}{:>10.2f}{:>10.2f}".format(
                row["threshold_pct"], row["threshold_val"],
                row["pass_rate"], row["miss_rate_yolo"], row["miss_rate_mask"],
                row["ms_per_patch_median"], row["ms_per_patch_p90"], row["saving_pct"]))

    with open(SWEEP_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(sweep_rows[0].keys()))
        writer.writeheader()
        for row in sweep_rows:
            writer.writerow(row)
    print()
    print("저장:", SWEEP_CSV)
    print()

    # ---- 핵심 지표 요약 ----
    print("=" * 92)
    print("핵심 지표 요약")
    print("=" * 92)

    for gate_name in ["cell_std_max", "defect_ratio"]:
        rows = [r for r in sweep_rows if r["gate"] == gate_name]
        # 미검률 0 유지 최대 임계 (YOLO 기준·마스크 기준 각각)
        for label_key, label_name in [("miss_rate_yolo", "YOLO"), ("miss_rate_mask", "MASK")]:
            zero = [r for r in rows if r[label_key] == 0.0]
            if len(zero) > 0:
                best = max(zero, key=lambda r: r["threshold_pct"])
                print("  {:<15} 미검률 0% ({}) 유지 최대 임계: pct={:.1f}, thr={:.3f}, pass_rate={:.1%}, save={:.1f}%".format(
                    gate_name, label_name, best["threshold_pct"], best["threshold_val"],
                    best["pass_rate"], best["saving_pct"]))
            else:
                print("  {:<15} 미검률 0% ({}) 유지 임계 없음".format(gate_name, label_name))

            # 미검률 <= 5%
            allow = [r for r in rows if r[label_key] <= 0.05]
            if len(allow) > 0:
                best5 = max(allow, key=lambda r: r["threshold_pct"])
                print("  {:<15} 미검률 ≤5% ({}) 유지 최대 임계: pct={:.1f}, thr={:.3f}, pass_rate={:.1%}, save={:.1f}%".format(
                    gate_name, label_name, best5["threshold_pct"], best5["threshold_val"],
                    best5["pass_rate"], best5["saving_pct"]))
        print()

    # ---- 시퀀스별 통과율 ----
    print("=" * 92)
    print("시퀀스별 통과율 (cell_std_max 게이트, 3 임계 예시)")
    print("=" * 92)

    picks = [25, 50, 75]
    seq_pass = defaultdict(lambda: {"total": 0, "by_pct": {}})
    for i, stem in enumerate(stems_ordered):
        key = seq_by_stem[stem]
        seq_pass[key]["total"] += 1
        for pct in picks:
            thr = float(np.percentile(cell_max_values, pct))
            if cell_max_values[i] >= thr:
                seq_pass[key]["by_pct"].setdefault(pct, 0)
                seq_pass[key]["by_pct"][pct] += 1

    print("  {:<40}{:>8}{:>10}{:>10}{:>10}".format("시퀀스", "N",
        "pass@p25", "pass@p50", "pass@p75"))
    for key in sorted(seq_pass.keys()):
        entry = seq_pass[key]
        line = "  {:<40}{:>8}".format(str(key), entry["total"])
        for pct in picks:
            got = entry["by_pct"].get(pct, 0)
            line += "{:>10.2%}".format(got / entry["total"])
        print(line)
    print()

    # ---- 그림 1: 트레이드오프 ----
    korean = None
    for f in font_manager.fontManager.ttflist:
        if f.name == "Noto Sans CJK KR":
            korean = f.name
            break
    if korean:
        plt.rcParams["font.family"] = korean
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(1, 2, figsize=(15, 6), sharey=True)
    for ax, ans_key, ans_label in [
        (axes[0], "miss_rate_yolo", "YOLO 기준 (A)"),
        (axes[1], "miss_rate_mask", "마스크 기준 (B)"),
    ]:
        for gate_name, color, marker in [
            ("cell_std_max", "#d62728", "o"),
            ("defect_ratio", "#1f77b4", "s"),
        ]:
            rows = [r for r in sweep_rows if r["gate"] == gate_name]
            xs = [r["ms_per_patch_median"] for r in rows]
            ys = [r[ans_key] * 100 for r in rows]
            ax.plot(xs, ys, color=color, marker=marker, markersize=4, linewidth=1.2,
                    label=gate_name)
        ax.scatter([FULL_MS_MEDIAN], [0], color="black", marker="*", s=90,
                   zorder=5, label="전수 처리 (60.5 ms, 0%)")
        ax.set_xlabel("평균 처리 시간 (ms/patch, median)")
        ax.set_title("미검률 vs 처리시간 · " + ans_label)
        ax.grid(alpha=0.25)
        ax.legend(loc="upper left", fontsize=9)
    axes[0].set_ylabel("미검률 (%)")
    fig.suptitle("캐스케이드 트레이드오프 (N={} patch, 게이트 2 종 × 정답 2 종)".format(len(patches)))
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(FIG_TRADEOFF, dpi=140)
    plt.close(fig)
    print("저장:", FIG_TRADEOFF)

    # ---- 그림 2: 시퀀스별 통과율 ----
    fig, ax = plt.subplots(figsize=(12, 6))
    seq_keys = sorted(seq_pass.keys())
    n_seq = len(seq_keys)
    percentiles_show = [10, 25, 50, 75, 90]
    x = np.arange(n_seq)
    bar_w = 0.14
    colors = ["#1f77b4", "#2ca02c", "#ff7f0e", "#d62728", "#9467bd"]
    for j, pct in enumerate(percentiles_show):
        thr = float(np.percentile(cell_max_values, pct))
        pass_rates = []
        for key in seq_keys:
            total = seq_pass[key]["total"]
            passed = 0
            for i, stem in enumerate(stems_ordered):
                if seq_by_stem[stem] != key: continue
                if cell_max_values[i] >= thr:
                    passed += 1
            pass_rates.append(passed / total)
        ax.bar(x + (j - 2) * bar_w, pass_rates, bar_w,
               color=colors[j], label="p{}".format(pct))
    ax.set_xticks(x)
    ax.set_xticklabels(["{}/{}".format(k[0], k[1]) for k in seq_keys], rotation=30, ha="right")
    ax.set_ylabel("통과율")
    ax.set_ylim(0, 1.05)
    ax.set_title("시퀀스별 게이트 통과율 (cell_std_max, p10/25/50/75/90)")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(loc="upper right", fontsize=9)
    fig.tight_layout()
    fig.savefig(FIG_PASSRATE, dpi=140)
    plt.close(fig)
    print("저장:", FIG_PASSRATE)


main()
