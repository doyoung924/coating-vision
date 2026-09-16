"""
49. 비용 기반 최적 임계값 (§22-4)

results_threshold_sweep.csv 재사용. 실제 비용을 모르므로 비용비 C = 미검비용/과검비용
을 가정하고 민감도를 본다.

- C ∈ {1, 5, 10, 30, 50, 100}
- 총비용 = C · FN + FP 최소화 L
- A3 · 세그 각각
- figures/cost_optimal_threshold.png : x=비용비(로그), y=최적 L
- results_cost_sensitivity.csv

한계 (반드시 명시):
- 비용비 C 는 라인·제품·시장 상황에 따른 값이며 이 프로젝트에서 확정 불가
- 여기서 얻는 결과는 "C가 이 정도라면 L은 이 근처" 라는 민감도 도표
- 실제 비용이 확보되면 그 값으로 L 을 다시 정해야 한다
"""

import csv
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


def main():
    rows = []
    with open("results_threshold_sweep.csv") as f:
        for r in csv.DictReader(f):
            rows.append({
                "L": float(r["L"]),
                "method": r["method"],
                "TP": int(r["TP"]),
                "FP": int(r["FP"]),
                "FN": int(r["FN"]),
                "TN": int(r["TN"]),
                "truth": int(r["truth_alerts"]),
                "pred": int(r["pred_alerts"]),
                "miss": float(r["miss_rate"]),
                "over": float(r["overcall_rate"]),
                "recall": float(r["recall"]),
                "precision": float(r["precision"]),
                "F1": float(r["F1"]),
            })

    C_values = [1, 5, 10, 30, 50, 100]

    print("=" * 110)
    print("§22-4 비용비 C = 미검비용/과검비용 별 최적 L")
    print("=" * 110)
    print(f"{'C':>5}  {'method':<10}{'best_L':>7}{'TP':>4}{'FP':>5}{'FN':>4}{'TN':>5}"
          f"{'miss':>7}{'over':>7}{'pred#':>7}{'total_cost':>12}")
    print("-" * 110)

    out_rows = []
    for C in C_values:
        for method in ("A3", "seg_crack"):
            sub = [r for r in rows if r["method"] == method]
            best = min(sub, key=lambda r: C * r["FN"] + r["FP"])
            total = C * best["FN"] + best["FP"]
            print(f"{C:>5}  {method:<10}{best['L']:>7.1f}"
                  f"{best['TP']:>4}{best['FP']:>5}{best['FN']:>4}{best['TN']:>5}"
                  f"{best['miss']:>7.3f}{best['over']:>7.3f}"
                  f"{best['pred']:>7}{total:>12}")
            out_rows.append({
                "cost_ratio": C, "method": method,
                "best_L": best["L"], "total_cost": total,
                "TP": best["TP"], "FP": best["FP"], "FN": best["FN"], "TN": best["TN"],
                "miss_rate": best["miss"], "overcall_rate": best["over"],
                "pred_alerts": best["pred"],
                "recall": best["recall"], "precision": best["precision"], "F1": best["F1"],
            })

    with open("results_cost_sensitivity.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        for r in out_rows:
            w.writerow(r)
    print("저장: results_cost_sensitivity.csv")

    # 조밀한 C 그리드로 최적 L 곡선 그리기
    C_grid = np.logspace(0, 2.5, 40)  # 1 ~ 316
    curves = {}
    for method in ("A3", "seg_crack"):
        sub = [r for r in rows if r["method"] == method]
        xs = []
        ys = []
        for C in C_grid:
            best = min(sub, key=lambda r: C * r["FN"] + r["FP"])
            xs.append(C)
            ys.append(best["L"])
        curves[method] = (xs, ys)

    fig, ax = plt.subplots(figsize=(9, 5.5))
    for method, color, marker in (("A3", "#1f77b4", "o"), ("seg_crack", "#2ca02c", "s")):
        xs, ys = curves[method]
        ax.plot(xs, ys, color=color, linewidth=1.2, alpha=0.85, label=method)
        # 지시 6개 지점 별표
        for C in C_values:
            sub = [r for r in rows if r["method"] == method]
            best = min(sub, key=lambda r: C * r["FN"] + r["FP"])
            ax.scatter([C], [best["L"]], color=color, s=90, marker=marker,
                       edgecolor="white", linewidth=1.2, zorder=5)
            ax.annotate(f"C={C} → L={best['L']:.1f}", (C, best["L"]),
                        xytext=(6, 6), textcoords="offset points",
                        fontsize=8, color=color)

    ax.axhline(3.5, color="gray", linestyle=":", linewidth=1, alpha=0.6,
               label="현행 L=3.5")
    ax.set_xscale("log")
    ax.set_xlabel("비용비 C = 미검비용 / 과검비용 (로그 스케일)")
    ax.set_ylabel("최적 L (관리한계 배수)")
    ax.set_title("§22-4 비용 민감도 — C 에 따른 최적 L (프레임 단위, 정답 agg=max)\n"
                 "총비용 = C · FN + FP 최소화")
    ax.grid(alpha=0.3, which="both")
    ax.legend(loc="best", fontsize=9)
    ax.set_ylim(1.9, 5.1)

    out_png = "figures/cost_optimal_threshold.png"
    fig.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    print("저장:", out_png)


main()
