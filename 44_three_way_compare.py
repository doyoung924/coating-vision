"""
44. A3 vs 세그 vs 정답 3자 비교

세 지표를 같은 patch 위에서 비교한다.
- A3 defect_ratio (results_spc.csv, patch-level cell 이상 비율)
- 세그 pred_crack_ratio (results_seg_area.csv, 43 산출)
- 마스크 area_crack (results_mask_area.csv, 정답, 22 산출)

pearson / spearman 을 시퀀스별 및 pooled 로 계산.
train 포함 patch 는 별도로 표기 (§20 실적 부풀림 회피).

figures/three_way_area_comparison.png — 3-패널 산점도 (pooled)

주의:
  - 세그 train patch (1,595) 는 학습에서 이미 본 데이터. 결과 부풀림 가능
  - split 별 상관도 함께 산출해 val/test_in/test_out (미학습) 위주 해석
"""

import csv
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from collections import defaultdict


def pearson(x, y):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if len(x) < 3: return float("nan"), float("nan")
    dx, dy = x - x.mean(), y - y.mean()
    denom = float(np.sqrt((dx*dx).sum() * (dy*dy).sum()))
    if denom == 0: return float("nan"), float("nan")
    r = max(-1.0, min(1.0, float((dx*dy).sum() / denom)))
    return r, fisher_p(r, len(x))


def fisher_p(r, n):
    if abs(r) >= 0.999999 or n < 4: return 0.0
    z = 0.5*np.log((1+r)/(1-r)); se = 1/np.sqrt(n-3); zs = abs(z)/se
    t = 1/(1+0.2316419*zs)
    c = [0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429]
    poly = 0; pw = t
    for ci in c: poly += ci*pw; pw *= t
    return float(2 * np.exp(-0.5*zs*zs) / np.sqrt(2*np.pi) * poly)


def rankv(v):
    a = np.asarray(v, dtype=float); order = np.argsort(a, kind="mergesort")
    r = np.zeros(len(a)); i = 0
    while i < len(a):
        j = i
        while j+1 < len(a) and a[order[j+1]] == a[order[i]]: j += 1
        avg = (i+j)/2 + 1; p = i
        while p <= j: r[order[p]] = avg; p += 1
        i = j+1
    return r


def spearman(x, y):
    if len(x) < 3: return float("nan"), float("nan")
    return pearson(rankv(x).tolist(), rankv(y).tolist())


def main():
    # Load data
    a3 = {}   # stem -> a3_defect_ratio
    with open("results_spc.csv") as f:
        for r in csv.DictReader(f):
            a3[r["stem"]] = float(r["defect_ratio"])

    seg = {}
    with open("results_seg_area.csv") as f:
        for r in csv.DictReader(f):
            seg[r["stem"]] = {
                "pred_crack": float(r["pred_crack_ratio"]),
                "pred_delam": float(r["pred_delam_ratio"]),
                "split": r["split"],
                "run_id": r["run_id"],
                "coating_gap": int(r["coating_gap"]),
                "position": r["position"],
            }

    truth = {}
    with open("results_mask_area.csv") as f:
        for r in csv.DictReader(f):
            truth[r["stem"]] = {
                "area_crack": float(r["area_crack"]),
                "area_or": float(r["area_or"]),
            }

    # 공통 stem
    common = sorted(set(a3) & set(seg) & set(truth))
    print(f"공통 stem: {len(common)}")

    a3_v = [a3[s] for s in common]
    seg_crack = [seg[s]["pred_crack"] for s in common]
    truth_crack = [truth[s]["area_crack"] for s in common]
    truth_or = [truth[s]["area_or"] for s in common]
    splits = [seg[s]["split"] for s in common]

    print()
    print("=" * 90)
    print("pooled (전체 2,227 patch) 상관")
    print("=" * 90)
    for name_a, name_b, va, vb in [
        ("A3", "세그 crack", a3_v, seg_crack),
        ("A3", "정답 area_crack", a3_v, truth_crack),
        ("세그 crack", "정답 area_crack", seg_crack, truth_crack),
        ("세그 crack", "정답 area_or",   seg_crack, truth_or),
    ]:
        sr, sp = spearman(va, vb)
        pr, pp = pearson(va, vb)
        print(f"  {name_a:<12} vs {name_b:<20}  Spearman={sr:.4f} (p={sp:.1e})  Pearson={pr:.4f}")
    print()

    print("=" * 90)
    print("split 별 · 세그 crack vs 정답 area_crack")
    print("=" * 90)
    by_split = defaultdict(list)
    for s, sp in zip(common, splits):
        by_split[sp].append(s)
    for sp in ["train", "val", "test_in", "test_out"]:
        stems_sp = by_split[sp]
        if len(stems_sp) < 3: continue
        v_seg = [seg[s]["pred_crack"] for s in stems_sp]
        v_tru = [truth[s]["area_crack"] for s in stems_sp]
        v_a3 = [a3[s] for s in stems_sp]
        sr_seg, _ = spearman(v_seg, v_tru)
        pr_seg, _ = pearson(v_seg, v_tru)
        sr_a3, _ = spearman(v_a3, v_tru)
        pr_a3, _ = pearson(v_a3, v_tru)
        print(f"  {sp:<10}  N={len(stems_sp):>4}  "
              f"세그 Spearman={sr_seg:.4f} Pearson={pr_seg:.4f}  |  "
              f"A3 Spearman={sr_a3:.4f} Pearson={pr_a3:.4f}")
    print()

    print("=" * 90)
    print("시퀀스별 · 세그 crack vs 정답 area_crack (§15-7 A3 참조값과 대조)")
    print("=" * 90)
    a3_ref = {
        ("R1", 600, "top-to-bottom-center"): 0.881,
        ("R1", 700, "top-to-bottom-center"): 0.838,
        ("R1", 800, "top-to-bottom-center"): 0.905,
        ("R1", 900, "top-to-bottom-center"): 0.758,
        ("R1", 1000, "top-to-bottom-center"): 0.836,
        ("R1", 1100, "top-to-bottom-center"): 0.673,
        ("R1", 1100, "top-to-bottom-center-1"): 0.799,
        ("R7", 700, "middle"): 0.153,
    }
    by_seq = defaultdict(list)
    for s in common:
        key = (seg[s]["run_id"], seg[s]["coating_gap"], seg[s]["position"])
        by_seq[key].append(s)
    print(f"  {'시퀀스':<40}{'N':>5}{'세그 Spearman':>16}{'세그 Pearson':>14}{'A3 Spearman':>14}")
    for seq in sorted(by_seq.keys()):
        stems_sq = by_seq[seq]
        v_seg = [seg[s]["pred_crack"] for s in stems_sq]
        v_tru = [truth[s]["area_crack"] for s in stems_sq]
        sr, _ = spearman(v_seg, v_tru)
        pr, _ = pearson(v_seg, v_tru)
        print(f"  {str(seq):<40}{len(stems_sq):>5}{sr:>16.4f}{pr:>14.4f}{a3_ref.get(seq, 0.0):>14.4f}")
    print()

    # 3-panel 산점도
    kf = None
    for f in fm.fontManager.ttflist:
        if f.name == "Noto Sans CJK KR": kf = f.name; break
    if kf: plt.rcParams["font.family"] = kf
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))

    # 색으로 split 구분
    split_colors = {"train": "#7a9ec8", "val": "#2ca02c", "test_in": "#ff7f0e", "test_out": "#d62728"}
    marker_size = {"train": 5, "val": 10, "test_in": 20, "test_out": 20}

    panels = [
        ("A3 defect_ratio", a3_v, "세그 pred_crack_ratio", seg_crack),
        ("A3 defect_ratio", a3_v, "정답 mask area_crack", truth_crack),
        ("세그 pred_crack_ratio", seg_crack, "정답 mask area_crack", truth_crack),
    ]
    for ax, (xn, xv, yn, yv) in zip(axes, panels):
        for sp in ["train", "val", "test_in", "test_out"]:
            xs = [xv[i] for i in range(len(splits)) if splits[i] == sp]
            ys = [yv[i] for i in range(len(splits)) if splits[i] == sp]
            ax.scatter(xs, ys, s=marker_size[sp], alpha=0.4, color=split_colors[sp],
                       edgecolors="none", label=f"{sp} (N={len(xs)})")
        ax.set_xlabel(xn)
        ax.set_ylabel(yn)
        sr, _ = spearman(xv, yv)
        pr, _ = pearson(xv, yv)
        ax.set_title(f"{xn} vs {yn}\nSpearman={sr:.3f}  Pearson={pr:.3f}", fontsize=10)
        ax.grid(alpha=0.25)
        ax.legend(loc="upper left", fontsize=8)

    fig.suptitle("A3 · 세그 · 정답 3자 비교 (N=2,227, split 색 구분)", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    out = "figures/three_way_area_comparison.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print("저장:", out)


main()
