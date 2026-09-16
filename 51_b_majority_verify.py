"""
51. b_majority 재현율 0 검증 (§22-13)

50_frame_prediction_definitions.py 의 pred_b_majority 가 6 조합 모두 recall 0.000 인
원인을 검증한다. 원인 후보:
  1) 코드 오류: patch_alert_indices 가 min_gap=10 병합 후 인덱스만 반환 →
     프레임 안 patch 9 개가 전부 raw alert 여도 병합으로 첫 하나만 남음
  2) 실제 데이터 특성: raw alert 기준에서도 과반 조각 알람인 프레임이 없다

두 경우를 판별하기 위해 세 가지 알람 정의로 프레임별 알람 조각 비율 산출:
  - raw_all   : Shewhart(3.5σ) + EWMA 이탈 이상 원시 인덱스 (병합 없음)
  - merged    : min_gap=10 병합 후 (현행 b_majority 입력)
  - shewhart  : 원시값 > UCL 만 (참고)
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
    return {"center": float(a.mean()), "sigma": float(a.std()),
            "upper": float(a.mean()) + sigma_limit * float(a.std())}


def compute_ewma(values, lam, init):
    out = []; prev = float(init)
    for v in values:
        cur = lam * float(v) + (1 - lam) * prev
        out.append(cur); prev = cur
    return out


def compute_ewma_upper(c, sd, lam, s, n):
    up = []
    for i in range(n):
        step = i + 1
        f = lam / (2 - lam) * (1 - (1 - lam) ** (2 * step))
        w = s * sd * np.sqrt(f)
        up.append(c + w)
    return up


def raw_alert_indices(values, limits, lam, sigma_limit):
    """Shewhart + EWMA 이탈 원시 인덱스. 병합 없음."""
    idx = set()
    for i, v in enumerate(values):
        if v > limits["upper"]:
            idx.add(i)
    ev = compute_ewma(values, lam, limits["center"])
    up = compute_ewma_upper(limits["center"], limits["sigma"], lam, sigma_limit, len(values))
    for i, v in enumerate(ev):
        if v > up[i]:
            idx.add(i)
    return sorted(idx)


def merged_alert_indices(raw, min_gap):
    merged = []; last = -999
    for a in raw:
        if a - last >= min_gap:
            merged.append(a); last = a
    return merged


def shewhart_only_indices(values, limits):
    return sorted([i for i, v in enumerate(values) if v > limits["upper"]])


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


def per_frame_ratio(records, alert_indices):
    """프레임별 (알람 조각 수, 전체 조각 수, 비율)"""
    idx_set = set(alert_indices)
    total = defaultdict(int)
    hit = defaultdict(int)
    for i, r in enumerate(records):
        total[r["frame_number"]] += 1
        if i in idx_set:
            hit[r["frame_number"]] += 1
    rows = []
    for fn in sorted(total.keys()):
        rows.append((fn, hit[fn], total[fn], hit[fn] / total[fn] if total[fn] else 0.0))
    return rows


def summarize(rows_by_frame, label):
    ratios = [r[3] for r in rows_by_frame]
    n_frames = len(ratios)
    n_over_half = sum(1 for r in ratios if r > 0.5)
    n_eq_or_over_half = sum(1 for r in ratios if r >= 0.5)
    n_100 = sum(1 for r in ratios if r >= 1.0 - 1e-9)
    max_ratio = max(ratios) if ratios else 0.0
    # 프레임 크기별 breakdown
    return {
        "label": label,
        "n_frames": n_frames,
        "max_ratio": max_ratio,
        "n_ratio_gt_0.5": n_over_half,
        "n_ratio_ge_0.5": n_eq_or_over_half,
        "n_ratio_100": n_100,
    }


def main():
    sequences = load_patch_data()

    print("=" * 130)
    print("§22-13 프레임별 알람 조각 비율 분포 (세 알람 정의)")
    print("=" * 130)

    # 시퀀스별로 각 방법의 alert index 집합을 얻고 프레임 비율 산출
    for method_name, value_key in (("A3", "a3"), ("seg_crack", "seg_crack")):
        print(f"\n[method = {method_name}]")
        agg_raw = []
        agg_merged = []
        agg_shew = []
        detail_rows = []

        for key in sorted(sequences.keys()):
            records = sequences[key]
            values = [r[value_key] for r in records]
            bc = max(20, int(len(values) * BASELINE_FRAC))
            limits = compute_limits(values[:bc], SIGMA_LIMIT)

            raw_idx = raw_alert_indices(values, limits, EWMA_LAMBDA, SIGMA_LIMIT)
            merged_idx = merged_alert_indices(raw_idx, MIN_GAP)
            shew_idx = shewhart_only_indices(values, limits)

            rows_raw = per_frame_ratio(records, raw_idx)
            rows_merged = per_frame_ratio(records, merged_idx)
            rows_shew = per_frame_ratio(records, shew_idx)

            agg_raw.extend(rows_raw)
            agg_merged.extend(rows_merged)
            agg_shew.extend(rows_shew)

            # 세부: 이 시퀀스 raw 기준 max ratio
            max_raw = max((r[3] for r in rows_raw), default=0.0)
            n_over = sum(1 for r in rows_raw if r[3] > 0.5)
            n_100 = sum(1 for r in rows_raw if r[3] >= 1.0 - 1e-9)
            print(f"  {str(key):<45} raw max_ratio={max_raw:.3f}  "
                  f"raw프레임 >0.5:{n_over:>3}  =1.0:{n_100:>3}  "
                  f"raw|merged|shew idx수={len(raw_idx):>3}|{len(merged_idx):>3}|{len(shew_idx):>3}")

        # 전체 요약
        print(f"\n[{method_name} 합계 (367 프레임)]")
        for label, rows in (("raw_all", agg_raw), ("merged (b_majority 현행 입력)", agg_merged),
                             ("shewhart_only", agg_shew)):
            summary = summarize(rows, label)
            print(f"  {label:<40} 프레임>0.5: {summary['n_ratio_gt_0.5']:>3}  "
                  f"프레임=1.0: {summary['n_ratio_100']:>3}  "
                  f"max 비율: {summary['max_ratio']:.3f}")

        # 프레임 크기별 breakdown (raw 기준)
        print(f"\n  raw 기준, 프레임 크기별 (조각 수 = 1~9)")
        by_size = defaultdict(lambda: {"n_frames": 0, "n_over": 0, "n_100": 0, "max_r": 0.0})
        for (fn, hit, total, ratio) in agg_raw:
            by_size[total]["n_frames"] += 1
            if ratio > 0.5:
                by_size[total]["n_over"] += 1
            if ratio >= 1.0 - 1e-9:
                by_size[total]["n_100"] += 1
            if ratio > by_size[total]["max_r"]:
                by_size[total]["max_r"] = ratio
        for sz in sorted(by_size.keys()):
            d = by_size[sz]
            print(f"    size={sz:>2}  n={d['n_frames']:>3}  >0.5:{d['n_over']:>3}  "
                  f"=1.0:{d['n_100']:>3}  max_ratio={d['max_r']:.3f}")

    print()
    print("=" * 130)
    print("판정")
    print("=" * 130)


main()
