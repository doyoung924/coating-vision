"""
48. 임계값 스윕 — 미검·과검 곡선 (§22-3)

프레임 단위 판정에서 관리한계 배수 L을 2.0~5.0 (0.1 간격) 스윕.
- 예측: L 을 A3/세그 patch 시계열에 적용해 알람 검출 후 프레임 OR
- 정답: L 을 프레임 시계열(max)에 적용해 알람 검출
- EWMA λ=0.1, min_gap=10, baseline_frac=0.3 은 그대로.

정답 지표는 §47 실측 결과 max 방식이 알람 수가 mean 방식보다 많고
프레임 판정에 더 관대(더 많은 정답 프레임 확보)라 max 기준으로 스윕.
mean 방식 결과는 §22-2 표에서 확인 가능.

산출:
  - results_threshold_sweep.csv (L × method × sequence 및 pooled)
  - figures/miss_overcall_tradeoff.png
      x: 과검율 FP/(FP+TN), y: 미검율 FN/(TP+FN)
      A3·세그 두 곡선. 현행 운영점 L=3.5 표시.
"""

import csv
from collections import defaultdict
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

_kf = None
for _f in fm.fontManager.ttflist:
    if _f.name == "Noto Sans CJK KR":
        _kf = _f.name
        break
if _kf:
    plt.rcParams["font.family"] = _kf
plt.rcParams["axes.unicode_minus"] = False

EWMA_LAMBDA = 0.1
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
    sequences = {}
    for key, stems_map in frame_data.items():
        rec_list = sorted(stems_map.values(), key=lambda r: (r["frame_number"], r["stem"]))
        sequences[key] = rec_list
    return sequences


def patch_alerts_by_frame(records, value_key, L):
    values = [r[value_key] for r in records]
    bc = max(20, int(len(values) * BASELINE_FRAC))
    limits = compute_limits(values[:bc], L)
    m = detect_alerts(values, limits, EWMA_LAMBDA, L, MIN_GAP)
    out = set()
    for idx in m:
        out.add(records[idx]["frame_number"])
    return out


def frame_truth_alerts(records, L, agg="max"):
    by_frame = defaultdict(list)
    for r in records:
        by_frame[r["frame_number"]].append(r["area_crack"])
    frame_nums = sorted(by_frame.keys())
    if agg == "max":
        values = [float(np.max(by_frame[fn])) for fn in frame_nums]
    else:
        values = [float(np.mean(by_frame[fn])) for fn in frame_nums]
    bc = max(20, int(len(values) * BASELINE_FRAC))
    if len(values) < bc + 5:
        return set()
    limits = compute_limits(values[:bc], L)
    m = detect_alerts(values, limits, EWMA_LAMBDA, L, MIN_GAP)
    out = set()
    for idx in m:
        out.add(frame_nums[idx])
    return out


def confusion(truth_set, pred_set, all_frames):
    tp = fp = fn = tn = 0
    for fn_num in all_frames:
        t = fn_num in truth_set
        p = fn_num in pred_set
        if t and p:
            tp += 1
        elif not t and p:
            fp += 1
        elif t and not p:
            fn += 1
        else:
            tn += 1
    return tp, fp, fn, tn


def main():
    sequences = load_patch_data()

    L_values = np.round(np.arange(2.0, 5.05, 0.1), 2)
    rows = []
    pooled = defaultdict(lambda: {"TP": 0, "FP": 0, "FN": 0, "TN": 0,
                                   "truth": 0, "pred_a3": 0, "pred_seg": 0})

    for L in L_values:
        L = float(L)
        totals = {"A3": {"TP": 0, "FP": 0, "FN": 0, "TN": 0, "truth": 0, "pred": 0},
                  "seg_crack": {"TP": 0, "FP": 0, "FN": 0, "TN": 0, "truth": 0, "pred": 0}}
        for key, records in sequences.items():
            all_frames = sorted({r["frame_number"] for r in records})
            truth_set = frame_truth_alerts(records, L, agg="max")
            pred_a3 = patch_alerts_by_frame(records, "a3", L)
            pred_seg = patch_alerts_by_frame(records, "seg_crack", L)
            for method, pred in (("A3", pred_a3), ("seg_crack", pred_seg)):
                tp, fp, fn, tn = confusion(truth_set, pred, all_frames)
                totals[method]["TP"] += tp
                totals[method]["FP"] += fp
                totals[method]["FN"] += fn
                totals[method]["TN"] += tn
                totals[method]["truth"] += len(truth_set)
                totals[method]["pred"] += len(pred)
        for method in ("A3", "seg_crack"):
            t = totals[method]
            miss = t["FN"] / (t["TP"] + t["FN"]) if (t["TP"] + t["FN"]) > 0 else 0.0
            over = t["FP"] / (t["FP"] + t["TN"]) if (t["FP"] + t["TN"]) > 0 else 0.0
            recall = 1 - miss
            prec = t["TP"] / (t["TP"] + t["FP"]) if (t["TP"] + t["FP"]) > 0 else 0.0
            f1 = 2 * recall * prec / (recall + prec) if (recall + prec) > 0 else 0.0
            rows.append({
                "L": L, "method": method,
                "TP": t["TP"], "FP": t["FP"], "FN": t["FN"], "TN": t["TN"],
                "truth_alerts": t["truth"], "pred_alerts": t["pred"],
                "miss_rate": miss, "overcall_rate": over,
                "recall": recall, "precision": prec, "F1": f1,
            })

    out_csv = "results_threshold_sweep.csv"
    fields = ["L", "method", "TP", "FP", "FN", "TN", "truth_alerts", "pred_alerts",
              "miss_rate", "overcall_rate", "recall", "precision", "F1"]
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("저장:", out_csv)

    print()
    print(f"{'L':>4}  {'method':<10}{'TP':>4}{'FP':>5}{'FN':>4}{'TN':>5}"
          f"{'truth':>7}{'pred':>6}{'miss':>7}{'over':>7}{'F1':>7}")
    for r in rows[::2 * 5]:  # every 5th L for both methods
        pass
    for r in rows:
        if abs(r["L"] - round(r["L"] * 2) / 2) < 0.001:  # 0.5 간격만 표시
            print(f"{r['L']:>4.1f}  {r['method']:<10}{r['TP']:>4}{r['FP']:>5}{r['FN']:>4}"
                  f"{r['TN']:>5}{r['truth_alerts']:>7}{r['pred_alerts']:>6}"
                  f"{r['miss_rate']:>7.3f}{r['overcall_rate']:>7.3f}{r['F1']:>7.3f}")

    # figure
    fig, ax = plt.subplots(figsize=(8, 6))
    for method, color, marker in (("A3", "#1f77b4", "o"), ("seg_crack", "#2ca02c", "s")):
        sub = [r for r in rows if r["method"] == method]
        sub.sort(key=lambda x: x["L"])
        xs = [r["overcall_rate"] for r in sub]
        ys = [r["miss_rate"] for r in sub]
        ax.plot(xs, ys, color=color, marker=marker, markersize=3.5, linewidth=1, alpha=0.75,
                label=method)
        # L=3.5 (현행)
        pt = [r for r in sub if abs(r["L"] - 3.5) < 0.001]
        if pt:
            ax.scatter([pt[0]["overcall_rate"]], [pt[0]["miss_rate"]],
                       color=color, s=140, marker="*", edgecolor="black",
                       linewidth=1.2, zorder=5,
                       label=f"{method} L=3.5 (현행)")
        # L 라벨 (2.0, 3.0, 4.0, 5.0만)
        for L_lab in (2.0, 3.0, 4.0, 5.0):
            for r in sub:
                if abs(r["L"] - L_lab) < 0.001:
                    ax.annotate(f"L={L_lab}", (r["overcall_rate"], r["miss_rate"]),
                                xytext=(4, 4), textcoords="offset points",
                                fontsize=7, color=color)

    ax.set_xlabel("과검율 FP/(FP+TN)")
    ax.set_ylabel("미검율 FN/(TP+FN)")
    ax.set_title("§22-3 임계값 L 스윕 (프레임 단위, 정답 agg=max, L=2.0~5.0)\n"
                 "3.5σ + EWMA λ=0.1 + min_gap=10, baseline 30%")
    ax.grid(alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    ax.set_xlim(-0.02, max(0.6, ax.get_xlim()[1]))
    ax.set_ylim(-0.05, 1.05)

    out_png = "figures/miss_overcall_tradeoff.png"
    fig.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    print("저장:", out_png)


main()
