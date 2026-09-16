"""
57. σ 배수 · EWMA λ 격자 재산출 (§23)

새 추론 없음. 기존 시계열 (results_mask_area.csv, results_seg_area.csv)
로 프레임 단위 대칭 조합 (c_frame_max, agg=max) 위 관리도 F1 을
L × λ 격자로 산출.

목적: SIGMA_LIMIT=3.5, EWMA_LAMBDA=0.1 의 정량 근거 부재 (§9-3 lift 폐기,
§22-DET-1 참조) 를 대체할 정당화 자료 마련.

- 판정 단위: 원본 프레임 (367)
- 정답: max(area_crack) per frame, 프레임 시계열 위 관리도 알람
- 예측 (대칭 c_frame_max): A3 defect_ratio · 세그 pred_crack_ratio 를
  프레임 max 로 집계 후 자체 관리도 알람
- 관리도: baseline 30% 앞구간, min_gap=10, Shewhart + EWMA 이중 판정
  (§45 로직 그대로)
- L ∈ {2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0}
- λ ∈ {0.05, 0.1, 0.2, 0.3}

산출: results_sigma_lambda_grid.csv (L × λ × method 격자)
"""

import csv
from collections import defaultdict
import numpy as np


MIN_GAP = 10
BASELINE_FRAC = 0.3


def compute_limits(baseline_values, sigma_limit):
    a = np.asarray(baseline_values, dtype=float)
    c = float(a.mean())
    sd = float(a.std())
    return {"center": c, "sigma": sd, "upper": c + sigma_limit * sd}


def compute_ewma(values, lam, init):
    out = []
    prev = float(init)
    for v in values:
        cur = lam * float(v) + (1 - lam) * prev
        out.append(cur)
        prev = cur
    return out


def compute_ewma_upper(c, sd, lam, s, n):
    up = []
    for i in range(n):
        step = i + 1
        f = lam / (2 - lam) * (1 - (1 - lam) ** (2 * step))
        w = s * sd * np.sqrt(f)
        up.append(c + w)
    return up


def detect_alerts(values, limits, lam, sigma_limit, min_gap):
    raw = []
    for i, v in enumerate(values):
        if v > limits["upper"]:
            raw.append(i)
    ev = compute_ewma(values, lam, limits["center"])
    up = compute_ewma_upper(limits["center"], limits["sigma"], lam, sigma_limit, len(values))
    for i, v in enumerate(ev):
        if v > up[i]:
            raw.append(i)
    raw = sorted(set(raw))
    merged = []
    last = -999
    for a in raw:
        if a - last >= min_gap:
            merged.append(a)
            last = a
    return merged


def load_patch_data():
    frame_data = defaultdict(dict)
    with open("results_spc.csv") as f:
        for r in csv.DictReader(f):
            key = (r["run_id"], int(r["coating_gap"]), r["position"])
            stem = r["stem"]
            frame_data[key][stem] = {
                "frame_number": int(r["frame_number"]),
                "stem": stem,
                "a3": float(r["defect_ratio"]),
            }
    with open("results_mask_area.csv") as f:
        for r in csv.DictReader(f):
            key = (r["run_id"], int(r["coating_gap"]), r["position"])
            stem = r["stem"]
            if stem in frame_data[key]:
                frame_data[key][stem]["area_crack"] = float(r["area_crack"])
    with open("results_seg_area.csv") as f:
        for r in csv.DictReader(f):
            key = (r["run_id"], int(r["coating_gap"]), r["position"])
            stem = r["stem"]
            if stem in frame_data[key]:
                frame_data[key][stem]["seg_crack"] = float(r["pred_crack_ratio"])
    return frame_data


def frame_series_max(seq_records, key):
    """시퀀스 records → 프레임 단위 max 집계 시계열. (frame_nums, values)"""
    by_frame = defaultdict(list)
    for r in seq_records:
        by_frame[r["frame_number"]].append(r[key])
    frame_nums = sorted(by_frame.keys())
    values = [float(max(by_frame[fn])) for fn in frame_nums]
    return frame_nums, values


def frame_alerts(seq_records, value_key, L, lam):
    frame_nums, values = frame_series_max(seq_records, value_key)
    bc = max(20, int(len(values) * BASELINE_FRAC))
    if len(values) < bc + 5:
        return set()
    limits = compute_limits(values[:bc], L)
    idx = detect_alerts(values, limits, lam, L, MIN_GAP)
    out = set()
    for i in idx:
        out.add(frame_nums[i])
    return out


def confusion(truth_set, pred_set, all_frames):
    tp = fp = fn = tn = 0
    for fn_num in all_frames:
        t = fn_num in truth_set
        p = fn_num in pred_set
        if t and p: tp += 1
        elif not t and p: fp += 1
        elif t and not p: fn += 1
        else: tn += 1
    return tp, fp, fn, tn


def safe_div(a, b):
    return a / b if b > 0 else 0.0


def main():
    frame_data = load_patch_data()
    # 시퀀스별 프레임 리스트
    sequences = {}
    for key, stems_map in frame_data.items():
        rec_list = sorted(stems_map.values(), key=lambda r: (r["frame_number"], r["stem"]))
        sequences[key] = rec_list

    L_values = [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
    lam_values = [0.05, 0.10, 0.20, 0.30]

    rows = []
    for L in L_values:
        for lam in lam_values:
            totals = {"A3": {"TP": 0, "FP": 0, "FN": 0, "TN": 0},
                      "seg_crack": {"TP": 0, "FP": 0, "FN": 0, "TN": 0}}
            for key, records in sequences.items():
                all_frames = sorted({r["frame_number"] for r in records})
                truth_set = frame_alerts(records, "area_crack", L, lam)
                pred_a3 = frame_alerts(records, "a3", L, lam)
                pred_seg = frame_alerts(records, "seg_crack", L, lam)
                for method, pred in (("A3", pred_a3), ("seg_crack", pred_seg)):
                    tp, fp, fn, tn = confusion(truth_set, pred, all_frames)
                    totals[method]["TP"] += tp
                    totals[method]["FP"] += fp
                    totals[method]["FN"] += fn
                    totals[method]["TN"] += tn
            for method in ("A3", "seg_crack"):
                t = totals[method]
                rec = safe_div(t["TP"], t["TP"] + t["FN"])
                prec = safe_div(t["TP"], t["TP"] + t["FP"])
                f1 = safe_div(2 * rec * prec, rec + prec)
                rows.append({
                    "L": L, "lambda": lam, "method": method,
                    "TP": t["TP"], "FP": t["FP"], "FN": t["FN"], "TN": t["TN"],
                    "recall": round(rec, 4), "precision": round(prec, 4),
                    "F1": round(f1, 4),
                })

    # 출력 (F1 격자표)
    print("=" * 90)
    print("§23 L × λ 격자 F1 (프레임 단위 c_frame_max 대칭 조합, N=367)")
    print("=" * 90)
    for method in ("A3", "seg_crack"):
        print(f"\n[{method}]")
        print(f"{'':>6}" + "".join(f"{'λ='+str(lam):>10}" for lam in lam_values))
        for L in L_values:
            line = f"L={L:.1f}"
            best_for_L = max((r for r in rows if r["method"] == method and r["L"] == L),
                              key=lambda r: r["F1"])
            for lam in lam_values:
                r = next(r for r in rows if r["method"] == method and r["L"] == L and r["lambda"] == lam)
                marker = "*" if r is best_for_L else " "
                line += f"{marker}{r['F1']:>9.4f}"
            print(line)
        # 전체 최적
        sub = [r for r in rows if r["method"] == method]
        best = max(sub, key=lambda r: r["F1"])
        r35 = next(r for r in sub if abs(r["L"] - 3.5) < 0.001 and abs(r["lambda"] - 0.1) < 0.001)
        print(f"  최적: L={best['L']} λ={best['lambda']} F1={best['F1']:.4f}")
        print(f"  현행: L=3.5 λ=0.1 F1={r35['F1']:.4f}")
        print(f"  차이: {best['F1'] - r35['F1']:.4f}")

    # 저장
    with open("results_sigma_lambda_grid.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["L", "lambda", "method", "TP", "FP", "FN", "TN",
                                            "recall", "precision", "F1"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("\n저장: results_sigma_lambda_grid.csv")


main()
