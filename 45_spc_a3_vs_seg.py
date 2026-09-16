"""
45. SPC A3 vs 세그 면적비 비교 (기존 09/10 로직 재사용)

배경 (§3 지시):
  기존 09 SPC 로직을 그대로 쓰되 입력만 바꾼다.
  - 입력 A: A3 defect_ratio (기존, results_spc.csv)
  - 입력 B: 세그 pred_crack_ratio (신규, results_seg_area.csv, seed0 best.pt)

  새 관리도 규칙·파라미터 도입 없음. 10_spc_tuning "권장 설정" 그대로:
    sigma_limit = 3.5, EWMA λ = 0.1, min_gap = 10 (연속 알람 병합)
    baseline_fraction = 0.3 (앞 30% 를 관리한계 산정용)

정답 대조:
  각 프레임의 마스크 area_crack 을 정답으로 대조. 세 지표 (A3, 세그, 정답)
  의 시계열을 나란히 그린다. 알람 시점이 실제 면적 급증과 일치하는지 눈으로 확인.

주의:
  - 세그 train patch (1,595) 는 학습 노출. train 시퀀스 (R1 7 개) 는 그 사실 병기
  - 공정능력 판정 (GOOD/MARGINAL/INCAPABLE) 없음. §15-10 폐기
  - 시퀀스별 알람 수만 비교. 절대값이 아닌 상대 순위

산출:
  - figures/spc_a3_vs_seg.png (시퀀스별 2 단 격자, 4×2 배치)
"""

import csv
import os
import numpy as np
from collections import defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# 09/10 파라미터 그대로
SIGMA_LIMIT = 3.5
EWMA_LAMBDA = 0.1
MIN_GAP = 10
BASELINE_FRAC = 0.3
WINDOW_SIZE = 15


def compute_limits(baseline_values, sigma_limit):
    a = np.asarray(baseline_values, dtype=float)
    c = float(a.mean()); sd = float(a.std())
    u = c + sigma_limit * sd
    l = max(0.0, c - sigma_limit * sd)
    return {"center": c, "sigma": sd, "upper": u, "lower": l}


def compute_ewma(values, lam, init):
    out = []; prev = float(init)
    for v in values:
        cur = lam*float(v) + (1-lam)*prev
        out.append(cur); prev = cur
    return out


def compute_ewma_limits(c, sd, lam, s, n):
    up = []; lo = []
    for i in range(n):
        step = i + 1
        f = lam/(2-lam) * (1 - (1-lam)**(2*step))
        w = s * sd * np.sqrt(f)
        up.append(c + w); lo.append(max(0.0, c - w))
    return up, lo


def detect_alerts(values, limits, lam, sigma_limit, min_gap):
    raw = []
    for i, v in enumerate(values):
        if v > limits["upper"]:
            raw.append(i)
    if lam is not None:
        ev = compute_ewma(values, lam, limits["center"])
        up, lo = compute_ewma_limits(limits["center"], limits["sigma"], lam, sigma_limit, len(values))
        for i, v in enumerate(ev):
            if v > up[i]:
                raw.append(i)
    raw = sorted(set(raw))
    merged = []; last = -999
    for a in raw:
        if a - last >= min_gap:
            merged.append(a); last = a
    return merged, raw, ev if lam is not None else values


def load_series():
    """(run_id, gap, position) → 프레임 정렬된 records [{frame, a3, seg_crack, truth_crack}]"""
    frame_data = defaultdict(dict)

    # results_spc.csv 로부터 a3
    with open("results_spc.csv") as f:
        for r in csv.DictReader(f):
            key = (r["run_id"], int(r["coating_gap"]), r["position"])
            fn = int(r["frame_number"])
            stem = r["stem"]
            frame_data[key].setdefault(stem, {
                "frame_number": fn, "stem": stem,
                "a3": float(r["defect_ratio"]),
            })
    # 세그
    with open("results_seg_area.csv") as f:
        for r in csv.DictReader(f):
            key = (r["run_id"], int(r["coating_gap"]), r["position"])
            stem = r["stem"]
            if stem in frame_data[key]:
                frame_data[key][stem]["seg_crack"] = float(r["pred_crack_ratio"])
                frame_data[key][stem]["split"] = r["split"]
    # 정답
    with open("results_mask_area.csv") as f:
        for r in csv.DictReader(f):
            key = (r["run_id"], int(r["coating_gap"]), r["position"])
            stem = r["stem"]
            if stem in frame_data[key]:
                frame_data[key][stem]["truth_crack"] = float(r["area_crack"])

    # 시퀀스별로 stem 순서 (09 group_into_sequences 순: frame_number, stem 정렬)
    sequences = {}
    for key, stems_map in frame_data.items():
        rec_list = sorted(stems_map.values(), key=lambda r: (r["frame_number"], r["stem"]))
        sequences[key] = rec_list
    return sequences


def analyze_sequence(records, value_key):
    values = [r[value_key] for r in records if value_key in r]
    if len(values) < 30:
        return None
    bc = max(20, int(len(values) * BASELINE_FRAC))
    limits = compute_limits(values[:bc], SIGMA_LIMIT)
    merged, raw, ewma_series = detect_alerts(values, limits, EWMA_LAMBDA, SIGMA_LIMIT, MIN_GAP)
    return {
        "values": values, "limits": limits,
        "merged_alerts": merged, "raw_alerts_count": len(raw),
        "ewma": ewma_series,
    }


def main():
    sequences = load_series()
    seq_keys = sorted(sequences.keys(), key=lambda k: (k[0], k[1], k[2]))

    print("=" * 96)
    print("§3 시퀀스별 SPC 알람 수 (A3 vs 세그, 정답 alerts 도 함께)")
    print("=" * 96)
    print("파라미터: sigma_limit 3.5, EWMA λ 0.1, min_gap 10 (10_spc_tuning 권장, 변경 없음)")
    print("baseline_fraction 0.3 (앞 30% 로 관리한계 산정)")
    print()
    print(f"{'시퀀스':<42}{'N':>5}"
          f"{'A3 alerts (raw/merge)':>26}"
          f"{'세그 alerts (raw/merge)':>28}"
          f"{'정답 alerts (raw/merge)':>28}")
    print("-" * 130)

    result_rows = []
    for key in seq_keys:
        records = sequences[key]
        res_a3 = analyze_sequence(records, "a3")
        res_seg = analyze_sequence(records, "seg_crack")
        res_tru = analyze_sequence(records, "truth_crack")
        if res_a3 is None or res_seg is None or res_tru is None:
            continue

        line = f"  {str(key):<40}{len(records):>5}"
        for res in (res_a3, res_seg, res_tru):
            line += f"  {res['raw_alerts_count']:>10} / {len(res['merged_alerts']):<5}"
        print(line)
        result_rows.append((key, records, res_a3, res_seg, res_tru))
    print()

    # 알람이 정답과 얼마나 일치하는지 (알람 시점 ±5 프레임 안 정답 alerts 포함 여부)
    print("=" * 96)
    print("§3 정답 alerts 대비 A3·세그 알람의 근접 일치율 (±5 프레임 창)")
    print("=" * 96)
    print(f"{'시퀀스':<42}{'A3 hit / 정답#':>18}{'세그 hit / 정답#':>20}")
    for key, records, res_a3, res_seg, res_tru in result_rows:
        truth_set = set(res_tru["merged_alerts"])
        def hit_rate(alerts):
            hit = 0
            for a in truth_set:
                for m in alerts:
                    if abs(m - a) <= 5:
                        hit += 1; break
            return hit
        h_a3 = hit_rate(res_a3["merged_alerts"])
        h_seg = hit_rate(res_seg["merged_alerts"])
        line = f"  {str(key):<40}{h_a3:>4} / {len(truth_set):<10}{h_seg:>4} / {len(truth_set):<10}"
        print(line)
    print()

    # 시각화: 시퀀스별 2단 (A3 + 세그) 관리도. 정답은 옅게 오버레이
    kf = None
    for f in fm.fontManager.ttflist:
        if f.name == "Noto Sans CJK KR": kf = f.name; break
    if kf: plt.rcParams["font.family"] = kf
    plt.rcParams["axes.unicode_minus"] = False

    n_seq = len(result_rows)
    n_cols = 2
    n_rows = (n_seq + 1) // 2   # per-seq 2 subplot rows (A3, seg)
    fig, axes = plt.subplots(n_seq, 2, figsize=(20, 3.4*n_seq),
                              sharex=False)
    if n_seq == 1:
        axes = axes.reshape(1, 2)

    for row_i, (key, records, res_a3, res_seg, res_tru) in enumerate(result_rows):
        for col_i, (title_prefix, res, color) in enumerate([
            ("A3", res_a3, "#1f77b4"),
            ("세그 (crack)", res_seg, "#2ca02c"),
        ]):
            ax = axes[row_i][col_i]
            vals = res["values"]
            x = np.arange(len(vals))
            # 원 시계열
            ax.plot(x, vals, color=color, linewidth=0.9, alpha=0.85, label=title_prefix)
            # EWMA
            ax.plot(x, res["ewma"], color=color, linestyle="--", linewidth=0.7, alpha=0.6, label="EWMA")
            # UCL
            ax.axhline(res["limits"]["upper"], color=color, linestyle=":", linewidth=1, alpha=0.6)
            # center
            ax.axhline(res["limits"]["center"], color="gray", linestyle=":", linewidth=0.8, alpha=0.5)
            # 정답 alerts
            for a in res_tru["merged_alerts"]:
                ax.axvline(a, color="#d62728", linestyle="-", linewidth=0.6, alpha=0.25)
            # 이 지표 alerts (점)
            for m in res["merged_alerts"]:
                ax.scatter([m], [vals[m]], color=color, s=45, marker="o",
                           edgecolor="white", linewidth=0.8, zorder=5)
            title = f"{title_prefix}  ·  {key[0]}/{key[1]}μm/{key[2]}"
            title += f"  (alert {len(res['merged_alerts'])} / 정답 {len(res_tru['merged_alerts'])})"
            ax.set_title(title, fontsize=9)
            ax.grid(alpha=0.25)
            if col_i == 0:
                ax.set_ylabel("ratio")
            if row_i == n_seq - 1:
                ax.set_xlabel("frame index (sorted)")

    fig.suptitle("§45 SPC 관리도 · A3 vs 세그 (붉은 세로줄 = 정답 마스크 면적 알람, 3.5σ + EWMA λ=0.1, min_gap 10)",
                  fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    out = "figures/spc_a3_vs_seg.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print("저장:", out)


main()
