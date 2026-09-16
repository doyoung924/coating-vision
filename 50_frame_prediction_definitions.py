"""
50. 프레임 판정 예측 정의 재확인 (§22-9)

정답·예측 집계 방식 비대칭을 해소하기 위해 예측 집계 3방식을 각각 산출한다.

예측 집계 (patch → 프레임):
  (a) 하나라도 알람 (§47 현행): 프레임 내 patch 중 하나라도 patch merged_alert 인덱스에 포함되면 프레임 예측 알람
  (b) 조각의 과반이 알람: 프레임 내 patch 중 (알람 patch 수) > (전체 patch 수 / 2).
      **§22-13 정정**: min_gap 병합된 merged_alert 대신 raw alert 인덱스 (Shewhart+EWMA 이탈) 사용.
      병합은 시간축에서 인접 raw alert 를 뭉치는 연산이므로 프레임 내 조각 카운트에는
      부적합 (병합으로 프레임 안 여러 조각이 첫 하나로 축소됨).
  (c) 프레임 지표 직접 관리도 (대칭 방식):
       프레임 단위로 A3 defect_ratio 또는 세그 pred_crack_ratio 를 mean/max 로 집계한
       프레임 시계열을 만들고, 그 시계열 자체에 관리도(3.5σ + EWMA λ=0.1, min_gap=10, baseline 30%)
       적용. 예측 시계열의 집계 방식은 정답 시계열의 agg 와 동일하게 맞춘다.

정답 집계 (프레임 지표):
  (mean) 프레임 내 patch area_crack 평균
  (max)  프레임 내 patch area_crack 최대

조합: 정답 agg (mean/max) × 예측 (a/b/c) = 6 조합. A3·세그 각각.

원칙:
- 결과가 좋아 보이는 조합을 고르려 하지 말 것
- 정답·예측 집계가 일치하는 대칭 조합 = 정답 mean × 예측 c-mean, 정답 max × 예측 c-max
- 나머지 4 조합은 참고
"""

import csv
from collections import defaultdict
import numpy as np

SIGMA_LIMIT = 3.5
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


def detect_alert_indices(values, limits, lam, sigma_limit, min_gap):
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


def frame_alerts_from_series(records, value_key, agg):
    """프레임 지표 (mean 또는 max) 시계열에서 관리도 알람 검출. 반환 set(frame_number)."""
    by_frame = defaultdict(list)
    for r in records:
        by_frame[r["frame_number"]].append(r[value_key])
    frame_nums = sorted(by_frame.keys())
    if agg == "mean":
        values = [float(np.mean(by_frame[fn])) for fn in frame_nums]
    else:
        values = [float(np.max(by_frame[fn])) for fn in frame_nums]
    bc = max(20, int(len(values) * BASELINE_FRAC))
    if len(values) < bc + 5:
        return set()
    limits = compute_limits(values[:bc], SIGMA_LIMIT)
    idx = detect_alert_indices(values, limits, EWMA_LAMBDA, SIGMA_LIMIT, MIN_GAP)
    out = set()
    for i in idx:
        out.add(frame_nums[i])
    return out


def patch_alert_indices(records, value_key):
    """patch 시계열 위 merged alert 인덱스 목록 (min_gap=10 병합 후). a_any 용."""
    values = [r[value_key] for r in records]
    bc = max(20, int(len(values) * BASELINE_FRAC))
    limits = compute_limits(values[:bc], SIGMA_LIMIT)
    return detect_alert_indices(values, limits, EWMA_LAMBDA, SIGMA_LIMIT, MIN_GAP)


def patch_raw_alert_indices(records, value_key):
    """patch 시계열 위 raw alert 인덱스 (Shewhart + EWMA 이탈, 병합 없음). b_majority 용."""
    values = [r[value_key] for r in records]
    bc = max(20, int(len(values) * BASELINE_FRAC))
    limits = compute_limits(values[:bc], SIGMA_LIMIT)
    idx = set()
    for i, v in enumerate(values):
        if v > limits["upper"]:
            idx.add(i)
    ev = compute_ewma(values, EWMA_LAMBDA, limits["center"])
    up = compute_ewma_upper(limits["center"], limits["sigma"], EWMA_LAMBDA, SIGMA_LIMIT, len(values))
    for i, v in enumerate(ev):
        if v > up[i]:
            idx.add(i)
    return sorted(idx)


def pred_a_any(records, value_key):
    """(a) 하나라도 알람. patch merged_alert 인덱스 중 하나라도 있는 프레임."""
    idx = patch_alert_indices(records, value_key)
    alert_frames = set()
    for i in idx:
        alert_frames.add(records[i]["frame_number"])
    return alert_frames


def pred_b_majority(records, value_key):
    """(b) 과반 알람. 프레임 내 patch 중 알람 patch 가 과반.
    §22-13 정정: raw alert 인덱스 (병합 없음) 사용."""
    idx = set(patch_raw_alert_indices(records, value_key))
    by_frame_total = defaultdict(int)
    by_frame_alert = defaultdict(int)
    for i, r in enumerate(records):
        by_frame_total[r["frame_number"]] += 1
        if i in idx:
            by_frame_alert[r["frame_number"]] += 1
    alert_frames = set()
    for fn, total in by_frame_total.items():
        if by_frame_alert[fn] > total / 2:
            alert_frames.add(fn)
    return alert_frames


def pred_c_frame_series(records, value_key, agg):
    """(c) 프레임 지표 (agg) 를 직접 관리도에 올림. 정답과 같은 agg."""
    return frame_alerts_from_series(records, value_key, agg)


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


def safe_div(a, b):
    if b == 0:
        return 0.0
    return a / b


def main():
    sequences = load_patch_data()
    seq_keys = sorted(sequences.keys(), key=lambda k: (k[0], k[1], k[2]))

    all_rows = []

    for truth_agg in ("mean", "max"):
        print("=" * 130)
        print(f"§22-9 예측 집계 3 방식 × 정답 agg={truth_agg}")
        print("=" * 130)
        print(f"{'method':<10}{'pred_def':<14}{'truth':>7}{'pred':>7}{'TP':>4}{'FP':>5}{'FN':>4}{'TN':>5}"
              f"{'Recall':>8}{'Prec':>7}{'F1':>7}  {'symmetric':>10}")
        print("-" * 130)

        # 시퀀스 합계
        totals = defaultdict(lambda: {"TP": 0, "FP": 0, "FN": 0, "TN": 0,
                                       "truth": 0, "pred": 0})

        for key in seq_keys:
            records = sequences[key]
            all_frames = sorted({r["frame_number"] for r in records})

            truth_set = frame_alerts_from_series(records, "area_crack", truth_agg)

            for method_name, value_key in (("A3", "a3"), ("seg_crack", "seg_crack")):
                pred_defs = {
                    "a_any": pred_a_any(records, value_key),
                    "b_majority": pred_b_majority(records, value_key),
                    "c_frame_" + truth_agg: pred_c_frame_series(records, value_key, truth_agg),
                }
                for pred_def, pred in pred_defs.items():
                    tp, fp, fn, tn = confusion(truth_set, pred, all_frames)
                    k = (method_name, pred_def)
                    totals[k]["TP"] += tp
                    totals[k]["FP"] += fp
                    totals[k]["FN"] += fn
                    totals[k]["TN"] += tn
                    totals[k]["truth"] += len(truth_set)
                    totals[k]["pred"] += len(pred)

        for method in ("A3", "seg_crack"):
            for pred_def in ("a_any", "b_majority", f"c_frame_{truth_agg}"):
                t = totals[(method, pred_def)]
                rec = safe_div(t["TP"], t["TP"] + t["FN"])
                prec = safe_div(t["TP"], t["TP"] + t["FP"])
                f1 = safe_div(2 * rec * prec, rec + prec)
                sym = "YES" if pred_def == f"c_frame_{truth_agg}" else "no"
                print(f"{method:<10}{pred_def:<14}{t['truth']:>7}{t['pred']:>7}"
                      f"{t['TP']:>4}{t['FP']:>5}{t['FN']:>4}{t['TN']:>5}"
                      f"{rec:>8.3f}{prec:>7.3f}{f1:>7.3f}  {sym:>10}")
                all_rows.append({
                    "truth_agg": truth_agg,
                    "method": method,
                    "pred_def": pred_def,
                    "symmetric": (pred_def == f"c_frame_{truth_agg}"),
                    "truth_alerts": t["truth"],
                    "pred_alerts": t["pred"],
                    "TP": t["TP"], "FP": t["FP"], "FN": t["FN"], "TN": t["TN"],
                    "recall": rec, "precision": prec, "F1": f1,
                })
        print()

    out_csv = "results_frame_prediction_definitions.csv"
    fields = ["truth_agg", "method", "pred_def", "symmetric",
              "truth_alerts", "pred_alerts",
              "TP", "FP", "FN", "TN",
              "recall", "precision", "F1"]
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_rows:
            w.writerow(r)
    print("저장:", out_csv)


main()
