"""
47. 프레임 단위 알람 혼동행렬 (§22-2)

판정 단위를 원본 프레임 367개로 올려 4칸(TP/FP/FN/TN) 모두 계산한다.
새 파라미터 추가 없음. 관리한계 산출은 §45와 동일 (baseline 30%, 3.5σ + EWMA λ=0.1, min_gap=10).

정답 지표 (프레임 지표) 두 방식:
  (a) mean : 프레임 내 patch area_crack 평균
  (b) max  : 프레임 내 patch area_crack 최대
두 방식 각각에 대해 시퀀스별 프레임 시계열을 만들고 관리도 알람 검출.

예측 알람:
  patch 단위 알람 결과(§45 로직)를 프레임 단위로 OR 집계.
  해당 프레임에 속한 patch 중 하나라도 patch merged_alert 시점에 포함되면 프레임 예측 알람.

주의:
  - min_gap=10 은 patch 시계열 위 병합. 프레임 단위로 올린 뒤에는 이미 프레임 이진 상태.
  - 이 스크립트는 새 병합을 하지 않는다. patch 병합 결과를 프레임에 매핑.
  - TN = 정답·예측 모두 없는 프레임 수. 프레임 시계열은 유한하므로 4칸 계산 가능.

혼동행렬 (프레임 단위):
  TP = 정답 알람 & 예측 알람 프레임
  FP = 정답 알람 아님 & 예측 알람 프레임
  FN = 정답 알람 & 예측 알람 아님
  TN = 정답 알람 아님 & 예측 알람 아님
  Recall = TP / (TP+FN), Precision = TP / (TP+FP), F1

산출:
  - results_alarm_confusion_frame.csv
  - stdout 표 (평균·최대 두 방식 각각)
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
    """patch 단위 데이터 로드"""
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


def get_patch_alerts_by_frame(records, value_key, sigma_limit=SIGMA_LIMIT, lam=EWMA_LAMBDA,
                              min_gap=MIN_GAP, baseline_frac=BASELINE_FRAC):
    """patch 시계열로 알람 검출 후 프레임 단위로 OR 집계. 반환: set(frame_number)"""
    values = [r[value_key] for r in records]
    bc = max(20, int(len(values) * baseline_frac))
    limits = compute_limits(values[:bc], sigma_limit)
    m = detect_alerts(values, limits, lam, sigma_limit, min_gap)
    alert_frames = set()
    for idx in m:
        alert_frames.add(records[idx]["frame_number"])
    return alert_frames


def build_frame_series(records, agg="mean"):
    """프레임 단위 area_crack 시계열. agg=mean 또는 max"""
    by_frame = defaultdict(list)
    for r in records:
        by_frame[r["frame_number"]].append(r["area_crack"])
    frame_nums = sorted(by_frame.keys())
    if agg == "mean":
        values = [float(np.mean(by_frame[fn])) for fn in frame_nums]
    else:
        values = [float(np.max(by_frame[fn])) for fn in frame_nums]
    return frame_nums, values


def frame_truth_alerts(records, agg, sigma_limit=SIGMA_LIMIT, lam=EWMA_LAMBDA,
                       min_gap=MIN_GAP, baseline_frac=BASELINE_FRAC):
    """프레임 시계열 위에서 정답 알람 검출. 반환: set(frame_number)"""
    frame_nums, values = build_frame_series(records, agg=agg)
    bc = max(20, int(len(values) * baseline_frac))
    if len(values) < bc + 5:
        return set(), frame_nums, values, None
    limits = compute_limits(values[:bc], sigma_limit)
    m = detect_alerts(values, limits, lam, sigma_limit, min_gap)
    alert_frames = set()
    for idx in m:
        alert_frames.add(frame_nums[idx])
    return alert_frames, frame_nums, values, limits


def safe_div(a, b):
    if b == 0:
        return 0.0
    return a / b


def confusion(truth_set, pred_set, all_frames):
    tp = 0
    fp = 0
    fn = 0
    tn = 0
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
    seq_keys = sorted(sequences.keys(), key=lambda k: (k[0], k[1], k[2]))

    rows = []

    for agg in ("mean", "max"):
        print("=" * 130)
        print(f"§22-2 프레임 단위 혼동행렬 (정답 지표 agg = {agg})")
        print("=" * 130)
        print(f"{'시퀀스':<40}{'N_frm':>6}{'truth':>7}{'pred':>7}{'TP':>4}{'FP':>4}{'FN':>4}{'TN':>5}"
              f"{'Recall':>8}{'Prec':>7}{'F1':>7}  method")
        print("-" * 130)

        for key in seq_keys:
            records = sequences[key]
            all_frames = sorted({r["frame_number"] for r in records})

            truth_set, _, _, _ = frame_truth_alerts(records, agg=agg)

            pred_a3 = get_patch_alerts_by_frame(records, "a3")
            pred_seg = get_patch_alerts_by_frame(records, "seg_crack")

            for method, pred in (("A3", pred_a3), ("seg_crack", pred_seg)):
                tp, fp, fn, tn = confusion(truth_set, pred, all_frames)
                rec = safe_div(tp, tp + fn)
                prec = safe_div(tp, tp + fp)
                f1 = safe_div(2 * rec * prec, rec + prec)
                key_str = f"{key[0]}/{key[1]}/{key[2][:20]}"
                print(f"  {key_str:<38}{len(all_frames):>6}{len(truth_set):>7}{len(pred):>7}"
                      f"{tp:>4}{fp:>4}{fn:>4}{tn:>5}{rec:>8.3f}{prec:>7.3f}{f1:>7.3f}  {method}")
                rows.append({
                    "sequence": f"{key[0]}/{key[1]}/{key[2]}",
                    "unit": "frame",
                    "truth_agg": agg,
                    "method": method,
                    "n_frame": len(all_frames),
                    "truth_alerts": len(truth_set),
                    "pred_alerts": len(pred),
                    "TP": tp, "FP": fp, "FN": fn, "TN": tn,
                    "recall": rec, "precision": prec, "F1": f1,
                })

        print()
        print(f"합계 (agg={agg})")
        print("-" * 130)
        for method in ("A3", "seg_crack"):
            sub = [r for r in rows if r["truth_agg"] == agg and r["method"] == method
                   and r["sequence"] != "TOTAL"]
            tp = sum(r["TP"] for r in sub)
            fp = sum(r["FP"] for r in sub)
            fn = sum(r["FN"] for r in sub)
            tn = sum(r["TN"] for r in sub)
            n_frm = sum(r["n_frame"] for r in sub)
            truth = sum(r["truth_alerts"] for r in sub)
            pred = sum(r["pred_alerts"] for r in sub)
            rec = safe_div(tp, tp + fn)
            prec = safe_div(tp, tp + fp)
            f1 = safe_div(2 * rec * prec, rec + prec)
            print(f"  {method:<38}{n_frm:>6}{truth:>7}{pred:>7}{tp:>4}{fp:>4}{fn:>4}{tn:>5}"
                  f"{rec:>8.3f}{prec:>7.3f}{f1:>7.3f}")
            rows.append({
                "sequence": "TOTAL",
                "unit": "frame",
                "truth_agg": agg,
                "method": method,
                "n_frame": n_frm,
                "truth_alerts": truth,
                "pred_alerts": pred,
                "TP": tp, "FP": fp, "FN": fn, "TN": tn,
                "recall": rec, "precision": prec, "F1": f1,
            })
        print()

    out = "results_alarm_confusion_frame.csv"
    fields = ["sequence", "unit", "truth_agg", "method", "n_frame",
              "truth_alerts", "pred_alerts", "TP", "FP", "FN", "TN",
              "recall", "precision", "F1"]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("저장:", out)


main()
