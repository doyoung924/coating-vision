"""
58. EWMA λ leave-one-out (§23-4 근거 보강)

§23-3 격자에서 세그 (L=3.5, λ=0.2) 가 F1 0.80 으로 (L=3.5, λ=0.1) 의 0.60
대비 +0.20 우세. 이 개선이 시퀀스 단위 선택 편의인지 확인:

시퀀스 8개 (R1/600, R1/700, ..., R7/700) 각각을 hold-out 으로 빼고
나머지 7개에서 (L=3.5, λ) 값 λ ∈ {0.1, 0.2} 로 F1 산출.
8회 평균과 표준편차로 (3.5, 0.1) vs (3.5, 0.2) 비교.

leave-one-out 은 λ 를 데이터로부터 선택하지 않고 상수로 사용하므로
"parameter 선택 편의" 는 없음. 시퀀스 편의만 남는다.

한계: 시퀀스 하나 뺐을 때 나머지 7개의 정답 알람이 여전히 소량
(0~4개/시퀀스) 이라 F1 값 자체의 분산이 큼. 이 사실이 검증 대상.
"""

import csv
from collections import defaultdict
import numpy as np

MIN_GAP = 10
BASELINE_FRAC = 0.3


def compute_limits(baseline_values, sigma_limit):
    a = np.asarray(baseline_values, dtype=float)
    return {"center": float(a.mean()), "sigma": float(a.std()),
            "upper": float(a.mean()) + sigma_limit * float(a.std())}


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


def load_data():
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


def frame_series_max(records, key):
    by_frame = defaultdict(list)
    for r in records:
        by_frame[r["frame_number"]].append(r[key])
    frame_nums = sorted(by_frame.keys())
    values = [float(max(by_frame[fn])) for fn in frame_nums]
    return frame_nums, values


def alerts(records, value_key, L, lam):
    frame_nums, values = frame_series_max(records, value_key)
    bc = max(20, int(len(values) * BASELINE_FRAC))
    if len(values) < bc + 5:
        return set(), frame_nums
    limits = compute_limits(values[:bc], L)
    idx = detect_alerts(values, limits, lam, L, MIN_GAP)
    return {frame_nums[i] for i in idx}, frame_nums


def confusion_totals(sequences, method_key, L, lam):
    tp = fp = fn = tn = 0
    for key, records in sequences.items():
        all_frames = sorted({r["frame_number"] for r in records})
        truth, _ = alerts(records, "area_crack", L, lam)
        pred, _ = alerts(records, method_key, L, lam)
        for fnum in all_frames:
            t = fnum in truth
            p = fnum in pred
            if t and p: tp += 1
            elif not t and p: fp += 1
            elif t and not p: fn += 1
            else: tn += 1
    return tp, fp, fn, tn


def f1(tp, fp, fn):
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    return 2 * rec * prec / (rec + prec) if (rec + prec) else 0.0


def main():
    frame_data = load_data()
    sequences = {}
    for key, stems_map in frame_data.items():
        rec_list = sorted(stems_map.values(), key=lambda r: (r["frame_number"], r["stem"]))
        sequences[key] = rec_list

    L = 3.5
    lambda_pair = [0.1, 0.2]

    # 각 시퀀스를 hold-out (남은 7개 시퀀스로 F1 계산)
    print("=" * 88)
    print("§23-4 EWMA λ leave-one-out (L=3.5 고정, method=seg_crack, N=8 시퀀스)")
    print("=" * 88)
    print(f"{'held-out':<40}{'λ=0.1 F1':>12}{'λ=0.2 F1':>12}{'Δ (0.2−0.1)':>16}")
    print("-" * 88)

    seq_keys = sorted(sequences.keys(), key=lambda k: (k[0], k[1], k[2]))
    rows = []
    for held in seq_keys:
        train_seqs = {k: v for k, v in sequences.items() if k != held}
        f1s = {}
        for lam in lambda_pair:
            tp, fp, fn, tn = confusion_totals(train_seqs, "seg_crack", L, lam)
            f1s[lam] = f1(tp, fp, fn)
        held_str = "{}/{}/{}".format(held[0], held[1], held[2][:20])
        delta = f1s[0.2] - f1s[0.1]
        print(f"{held_str:<40}{f1s[0.1]:>12.4f}{f1s[0.2]:>12.4f}{delta:>+16.4f}")
        rows.append({"held_out": held_str, "L": L,
                     "f1_lam_0_1": round(f1s[0.1], 4),
                     "f1_lam_0_2": round(f1s[0.2], 4),
                     "delta": round(delta, 4)})

    # 평균·std
    print()
    for lam in lambda_pair:
        vals = np.array([r["f1_lam_0_1" if lam == 0.1 else "f1_lam_0_2"] for r in rows])
        print(f"λ={lam}: F1 mean = {vals.mean():.4f}, std = {vals.std(ddof=1):.4f} (N=8 hold-out)")

    deltas = np.array([r["delta"] for r in rows])
    print(f"Δ (0.2−0.1): mean = {deltas.mean():+.4f}, std = {deltas.std(ddof=1):.4f}, min = {deltas.min():+.4f}, max = {deltas.max():+.4f}")
    n_pos = int((deltas > 0).sum())
    n_neg = int((deltas < 0).sum())
    n_zero = int((deltas == 0).sum())
    print(f"Δ > 0 (λ=0.2 우세): {n_pos}/8 · Δ < 0: {n_neg}/8 · Δ = 0: {n_zero}/8")

    # 전 데이터 (LOO 없이) 재확인
    for lam in lambda_pair:
        tp, fp, fn, tn = confusion_totals(sequences, "seg_crack", L, lam)
        print(f"전 데이터 λ={lam}: TP={tp} FP={fp} FN={fn} TN={tn} F1={f1(tp,fp,fn):.4f}")

    # 저장
    with open("results_lambda_loo.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["held_out", "L", "f1_lam_0_1", "f1_lam_0_2", "delta"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("\n저장: results_lambda_loo.csv")


main()
