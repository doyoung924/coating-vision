"""
46. patch 단위 알람 혼동행렬 (§22-1)

§45 스크립트 로직을 재현하되 반대 방향 카운트(FP)를 추가한다. 새 파라미터 없음.
- 시계열 단위: patch (results_spc.csv의 (frame_number, stem) 정렬 순)
- 정답 알람: 프레임 mask area_crack 시계열 위 3.5σ + EWMA λ=0.1, min_gap=10
- 예측 알람: A3 defect_ratio / 세그 pred_crack_ratio 동일 로직
- 매칭 창: ±5 시계열 인덱스

±5 매칭은 다대다이므로 정답·예측 관점을 나눠 정의:
- matched_truth = 정답 알람 중 ±5 안에 예측이 있는 것 (recall 분자)
- FN = truth - matched_truth
- matched_pred = 예측 알람 중 ±5 안에 정답이 있는 것 (precision 분자)
- FP = pred - matched_pred
- Recall = matched_truth / truth, Precision = matched_pred / pred
- TN 은 정상 인덱스 수 정의가 애매(patch 시계열은 매 인덱스가 관측점). 산출 안 함
- F1 = 2·Rec·Prec / (Rec+Prec)

산출:
- results_alarm_confusion_patch.csv
- stdout 표
"""

import csv
from collections import defaultdict
import numpy as np

SIGMA_LIMIT = 3.5
EWMA_LAMBDA = 0.1
MIN_GAP = 10
BASELINE_FRAC = 0.3
MATCH_WINDOW = 5


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


def load_series():
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
                frame_data[key][stem]["truth_crack"] = float(r["area_crack"])
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


def confusion(pred, truth, window):
    matched_truth = 0
    for a in truth:
        for m in pred:
            if abs(m - a) <= window:
                matched_truth += 1
                break
    matched_pred = 0
    for m in pred:
        for a in truth:
            if abs(m - a) <= window:
                matched_pred += 1
                break
    fn = len(truth) - matched_truth
    fp = len(pred) - matched_pred
    return matched_truth, matched_pred, fp, fn


def safe_div(a, b):
    if b == 0:
        return 0.0
    return a / b


def main():
    sequences = load_series()
    seq_keys = sorted(sequences.keys(), key=lambda k: (k[0], k[1], k[2]))

    print("=" * 120)
    print("§22-1 patch 시계열 위 혼동행렬 (min_gap=10, ±5 매칭창, 3.5σ + EWMA λ=0.1)")
    print("=" * 120)
    header = (f"{'시퀀스':<40}{'N_patch':>8}{'truth':>7}{'pred':>7}"
              f"{'mT':>5}{'mP':>5}{'FP':>5}{'FN':>5}{'Recall':>8}{'Prec':>7}{'F1':>7}"
              f"  method")
    print(header)
    print("-" * 130)

    rows = []
    for key in seq_keys:
        records = sequences[key]
        vals_a3 = [r["a3"] for r in records]
        vals_seg = [r.get("seg_crack", 0.0) for r in records]
        vals_tru = [r.get("truth_crack", 0.0) for r in records]
        bc = max(20, int(len(records) * BASELINE_FRAC))
        lim_a3 = compute_limits(vals_a3[:bc], SIGMA_LIMIT)
        lim_seg = compute_limits(vals_seg[:bc], SIGMA_LIMIT)
        lim_tru = compute_limits(vals_tru[:bc], SIGMA_LIMIT)
        m_a3 = detect_alerts(vals_a3, lim_a3, EWMA_LAMBDA, SIGMA_LIMIT, MIN_GAP)
        m_seg = detect_alerts(vals_seg, lim_seg, EWMA_LAMBDA, SIGMA_LIMIT, MIN_GAP)
        m_tru = detect_alerts(vals_tru, lim_tru, EWMA_LAMBDA, SIGMA_LIMIT, MIN_GAP)

        for method, pred in (("A3", m_a3), ("seg_crack", m_seg)):
            mt, mp, fp, fn = confusion(pred, m_tru, MATCH_WINDOW)
            rec = safe_div(mt, len(m_tru))
            prec = safe_div(mp, len(pred))
            f1 = safe_div(2 * rec * prec, rec + prec)
            key_str = f"{key[0]}/{key[1]}/{key[2][:20]}"
            print(f"  {key_str:<38}{len(records):>8}{len(m_tru):>7}{len(pred):>7}"
                  f"{mt:>5}{mp:>5}{fp:>5}{fn:>5}{rec:>8.3f}{prec:>7.3f}{f1:>7.3f}  {method}")
            rows.append({
                "sequence": f"{key[0]}/{key[1]}/{key[2]}",
                "unit": "patch",
                "method": method,
                "n_patch": len(records),
                "truth_alerts": len(m_tru),
                "pred_alerts": len(pred),
                "matched_truth": mt,
                "matched_pred": mp,
                "FP": fp, "FN": fn,
                "recall": rec, "precision": prec, "F1": f1,
            })

    print()
    print("=" * 120)
    print("합계")
    print("=" * 120)
    for method in ("A3", "seg_crack"):
        sub = [r for r in rows if r["method"] == method]
        mt = sum(r["matched_truth"] for r in sub)
        mp = sum(r["matched_pred"] for r in sub)
        fp = sum(r["FP"] for r in sub)
        fn = sum(r["FN"] for r in sub)
        n_patch = sum(r["n_patch"] for r in sub)
        truth = sum(r["truth_alerts"] for r in sub)
        pred = sum(r["pred_alerts"] for r in sub)
        rec = safe_div(mt, truth)
        prec = safe_div(mp, pred)
        f1 = safe_div(2 * rec * prec, rec + prec)
        print(f"  {method:<38}{n_patch:>8}{truth:>7}{pred:>7}{mt:>5}{mp:>5}{fp:>5}{fn:>5}"
              f"{rec:>8.3f}{prec:>7.3f}{f1:>7.3f}")
        rows.append({
            "sequence": "TOTAL",
            "unit": "patch",
            "method": method,
            "n_patch": n_patch,
            "truth_alerts": truth,
            "pred_alerts": pred,
            "matched_truth": mt, "matched_pred": mp,
            "FP": fp, "FN": fn,
            "recall": rec, "precision": prec, "F1": f1,
        })

    out = "results_alarm_confusion_patch.csv"
    fields = ["sequence", "unit", "method", "n_patch", "truth_alerts", "pred_alerts",
              "matched_truth", "matched_pred", "FP", "FN",
              "recall", "precision", "F1"]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("저장:", out)


main()
