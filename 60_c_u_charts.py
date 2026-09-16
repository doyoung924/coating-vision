"""
60. c/u 관리도 · 시퀀스 층별 (§보완 4, 2단계)

- c 관리도: 프레임당 박스 수, UCL = c̄ + 3√c̄ (n 무관 · 수평선)
- u 관리도: u = c / n_patch, UCL = ū + 3√(ū/n_patch) (계단형)
- 시퀀스 층별: 각 시퀀스 내에서 c 관리도 산출

산출:
  - figures/control_chart_c_vs_u.png
  - figures/control_chart_by_sequence.png
  - 이탈점 비교 표 (stdout)
  - n_patch vs u Spearman
  - results_c_u_outliers.csv (프레임별 c, u, 이탈 여부)
"""

import csv
import json
from collections import defaultdict
import math
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

_kf = None
for _f in fm.fontManager.ttflist:
    if _f.name == "Noto Sans CJK KR":
        _kf = _f.name; break
if _kf: plt.rcParams["font.family"] = _kf
plt.rcParams["axes.unicode_minus"] = False


def spearman(x, y):
    """의존성 없이 Spearman. tie 는 평균 순위."""
    n = len(x)
    if n < 3: return float("nan")
    def rank(arr):
        idx = sorted(range(n), key=lambda i: arr[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and arr[idx[j+1]] == arr[idx[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j+1):
                r[idx[k]] = avg
            i = j + 1
        return r
    rx = rank(x); ry = rank(y)
    mx = sum(rx)/n; my = sum(ry)/n
    num = sum((rx[i]-mx)*(ry[i]-my) for i in range(n))
    den = math.sqrt(sum((rx[i]-mx)**2 for i in range(n)) * sum((ry[i]-my)**2 for i in range(n)))
    return num/den if den > 0 else float("nan")


def main():
    with open("detections_cache.json") as f:
        cache = json.load(f)
    stem_to_seq = {}
    with open("results_mask_area.csv") as f:
        for r in csv.DictReader(f):
            stem_to_seq[r["stem"]] = (r["run_id"], r["coating_gap"], r["position"], int(r["frame_number"]))

    # 프레임 단위 집계: c (박스 수), n_patch (patch 수)
    frame_c = defaultdict(int)
    frame_n = defaultdict(int)
    for stem_jpg, boxes in cache.items():
        stem = stem_jpg.replace(".jpg", "")
        if stem in stem_to_seq:
            key = stem_to_seq[stem]
            frame_c[key] += len(boxes)
            frame_n[key] += 1

    # frame_number 순으로 시퀀스별 정렬
    seq_frames = defaultdict(list)
    for key, c in frame_c.items():
        seq_frames[(key[0], key[1], key[2])].append((key[3], c, frame_n[key]))
    for k in seq_frames:
        seq_frames[k].sort(key=lambda x: x[0])

    # 통합 c̄
    total_c = sum(frame_c.values())
    N = len(frame_c)
    c_bar = total_c / N
    c_ucl = c_bar + 3 * math.sqrt(c_bar)
    # 통합 ū (총 c / 총 n_patch)
    total_n = sum(frame_n.values())
    u_bar = total_c / total_n
    print("=" * 90)
    print("§보완4-2 c/u 관리도")
    print("=" * 90)
    print(f"N 프레임 = {N}, 총 c = {total_c}, 총 patch = {total_n}")
    print(f"c̄ = {c_bar:.4f}, c UCL = {c_ucl:.4f}")
    print(f"ū = {u_bar:.4f} (= c/patch)")

    # 각 프레임 이탈 여부
    rows = []
    for seq, frames in sorted(seq_frames.items()):
        for (fn, c, n) in frames:
            u = c / n if n > 0 else 0
            u_ucl_frame = u_bar + 3 * math.sqrt(u_bar / n) if n > 0 else float("inf")
            c_out = c > c_ucl
            u_out = u > u_ucl_frame
            rows.append({
                "sequence": f"{seq[0]}/{seq[1]}/{seq[2]}",
                "frame": fn, "c": c, "n_patch": n, "u": round(u, 4),
                "c_ucl": round(c_ucl, 4), "u_ucl": round(u_ucl_frame, 4),
                "c_out": int(c_out), "u_out": int(u_out),
            })

    # 이탈점 비교
    c_outs = [r for r in rows if r["c_out"]]
    u_outs = [r for r in rows if r["u_out"]]
    c_set = {(r["sequence"], r["frame"]) for r in c_outs}
    u_set = {(r["sequence"], r["frame"]) for r in u_outs}
    both = c_set & u_set
    c_only = c_set - u_set
    u_only = u_set - c_set
    print()
    print(f"c 관리도 이탈: {len(c_outs)} 프레임")
    print(f"u 관리도 이탈: {len(u_outs)} 프레임")
    print(f"둘 다 이탈: {len(both)}")
    print(f"c 만: {len(c_only)} — 예: {list(c_only)[:5]}")
    print(f"u 만: {len(u_only)} — 예: {list(u_only)[:5]}")

    # n_patch vs u 상관
    ns = [r["n_patch"] for r in rows]
    us = [r["u"] for r in rows]
    cs = [r["c"] for r in rows]
    sp_n_u = spearman(ns, us)
    sp_n_c = spearman(ns, cs)
    print()
    print(f"§15-9 상관 재확인:")
    print(f"  Spearman(n_patch, c) = {sp_n_c:.4f}  (§15-9 는 0.65~0.69 근처였음)")
    print(f"  Spearman(n_patch, u) = {sp_n_u:.4f}  (u 관리도 정규화 후)")

    # figure 1: c vs u 나란히
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=False)
    xs = list(range(len(rows)))
    c_vals = [r["c"] for r in rows]
    u_vals = [r["u"] for r in rows]
    u_ucls = [r["u_ucl"] for r in rows]
    ax1.plot(xs, c_vals, color="#1f77b4", linewidth=0.8, alpha=0.85)
    ax1.axhline(c_bar, color="gray", linestyle=":", linewidth=0.8, label=f"c̄={c_bar:.3f}")
    ax1.axhline(c_ucl, color="#d62728", linestyle="--", linewidth=1, label=f"UCL={c_ucl:.3f}")
    for i, r in enumerate(rows):
        if r["c_out"]:
            ax1.scatter([i], [r["c"]], color="#d62728", s=25, zorder=5)
    ax1.set_title(f"c 관리도 (프레임당 결함 수, N={N}, 이탈 {len(c_outs)})")
    ax1.set_xlabel("프레임 순서 (시퀀스별 정렬)")
    ax1.set_ylabel("c (박스 수)")
    ax1.legend(loc="upper right", fontsize=9)
    ax1.grid(alpha=0.3)

    ax2.plot(xs, u_vals, color="#2ca02c", linewidth=0.8, alpha=0.85, label="u = c/n_patch")
    ax2.axhline(u_bar, color="gray", linestyle=":", linewidth=0.8, label=f"ū={u_bar:.3f}")
    ax2.plot(xs, u_ucls, color="#d62728", linestyle="--", linewidth=1, drawstyle="steps-mid",
             label="UCL (계단형)")
    for i, r in enumerate(rows):
        if r["u_out"]:
            ax2.scatter([i], [r["u"]], color="#d62728", s=25, zorder=5)
    ax2.set_title(f"u 관리도 (n_patch 정규화, 이탈 {len(u_outs)})")
    ax2.set_xlabel("프레임 순서")
    ax2.set_ylabel("u (patch당 박스 수)")
    ax2.legend(loc="upper right", fontsize=9)
    ax2.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig("figures/control_chart_c_vs_u.png", dpi=140)
    plt.close(fig)
    print("\n저장: figures/control_chart_c_vs_u.png")

    # figure 2: 시퀀스 층별 c 관리도
    seq_stats = {}
    for seq, frames in seq_frames.items():
        cs_s = [c for (_, c, _) in frames]
        m = np.mean(cs_s)
        ucl_s = m + 3 * math.sqrt(m) if m > 0 else 0
        outs = [(fn, c) for (fn, c, _) in frames if c > ucl_s]
        seq_stats[seq] = {"n": len(frames), "c_bar": m, "ucl": ucl_s, "outs": len(outs)}

    fig2, axes = plt.subplots(4, 2, figsize=(16, 12), sharey=False)
    axes = axes.flatten()
    for i, (seq, frames) in enumerate(sorted(seq_frames.items())):
        ax = axes[i]
        cs_s = [c for (_, c, _) in frames]
        idx = list(range(len(cs_s)))
        s = seq_stats[seq]
        ax.plot(idx, cs_s, color="#1f77b4", linewidth=0.9, alpha=0.85)
        ax.axhline(s["c_bar"], color="gray", linestyle=":", linewidth=0.8, label=f"c̄={s['c_bar']:.2f}")
        ax.axhline(s["ucl"], color="#d62728", linestyle="--", linewidth=1,
                   label=f"UCL={s['ucl']:.2f}")
        for j, c in enumerate(cs_s):
            if c > s["ucl"]:
                ax.scatter([j], [c], color="#d62728", s=25, zorder=5)
        title = f"{seq[0]}/{seq[1]}/{seq[2][:20]} (n={s['n']}, 이탈 {s['outs']})"
        ax.set_title(title, fontsize=9)
        ax.set_xlabel("프레임 순서")
        ax.set_ylabel("c")
        ax.legend(loc="upper right", fontsize=7)
        ax.grid(alpha=0.3)
    fig2.suptitle("§보완4-3 시퀀스 층별 c 관리도 (각 시퀀스 내 c̄·UCL 개별 산출)", fontsize=11)
    fig2.tight_layout(rect=[0, 0, 1, 0.97])
    fig2.savefig("figures/control_chart_by_sequence.png", dpi=140)
    plt.close(fig2)
    print("저장: figures/control_chart_by_sequence.png")

    # 시퀀스 층별 요약 + 통합 대비 이탈점
    print()
    print("[시퀀스 층별 관리도 요약]")
    print(f"{'시퀀스':<40}{'n':>4}{'c̄':>8}{'UCL':>8}{'층별 이탈':>10}{'통합 이탈':>10}")
    for seq in sorted(seq_stats.keys()):
        s = seq_stats[seq]
        # 통합 UCL 로 이탈점 카운트
        integ = sum(1 for (_, c, _) in seq_frames[seq] if c > c_ucl)
        seq_str = f"{seq[0]}/{seq[1]}/{seq[2][:20]}"
        print(f"{seq_str:<40}{s['n']:>4}{s['c_bar']:>8.3f}{s['ucl']:>8.3f}{s['outs']:>10}{integ:>10}")

    # 저장
    with open("results_c_u_outliers.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["sequence", "frame", "c", "n_patch", "u",
                                            "c_ucl", "u_ucl", "c_out", "u_out"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("\n저장: results_c_u_outliers.csv")


main()
