"""
52. 측정 시스템 재현성 재프레이밍 (§8 → §22-MSA, 2026-09-15)

새 실험 없음. results_robustness.csv 재정리.

MSA 재현성 관점: "촬영 조건이 흔들릴 때 판정이 얼마나 재현되는가"

산출 가능:
  - 정상 그룹 marginal 재현성 = 1 - fixed_threshold_fpr
    (임계값을 기준 조건에서 fix 한 뒤 교란 조건에서 정상 표본의 오탐률로부터 근사)

산출 불가 (기존 데이터로):
  - 표본별 판정 일치율 (results_robustness.csv 는 집계 지표만 저장)
  - 결함 그룹 재현성 (임계값 고정 하 결함 판정 재현율 미저장)
  - AUROC 는 재현성 지표가 아님 (전체 순위 분리도)

용어 정리:
  - fixed_threshold_fpr = 정상 표본이 이상으로 판정된 비율 (임계값 고정)
  - 정상 재현성 (marginal) = 1 - fixed_threshold_fpr
"""

import csv
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


def load():
    rows = []
    with open("results_robustness.csv") as f:
        for r in csv.DictReader(f):
            rows.append({
                "perturbation": r["perturbation"],
                "a3_auroc": float(r["a3_auroc"]),
                "a3_fpr": float(r["a3_fpr_at_95tpr"]),
                "a3_fix": float(r["a3_fixed_threshold_fpr"]),
                "b2_auroc": float(r["b2_auroc"]),
                "b2_fpr": float(r["b2_fpr_at_95tpr"]),
                "b2_fix": float(r["b2_fixed_threshold_fpr"]),
            })
    return rows


def main():
    rows = load()
    print("=" * 110)
    print("§22-MSA 측정 시스템 재현성 (정상 그룹 marginal, 임계값 고정)")
    print("=" * 110)
    print(f"{'교란':<22}{'A3 정상재현':>14}{'B2 정상재현':>14}"
          f"{'A3 fixFPR':>13}{'B2 fixFPR':>13}{'A3 AUROC':>10}{'B2 AUROC':>10}")
    print("-" * 110)
    for r in rows:
        a3_repro = 1.0 - r["a3_fix"]
        b2_repro = 1.0 - r["b2_fix"]
        print(f"{r['perturbation']:<22}{a3_repro:>14.3f}{b2_repro:>14.3f}"
              f"{r['a3_fix']:>13.4f}{r['b2_fix']:>13.4f}"
              f"{r['a3_auroc']:>10.4f}{r['b2_auroc']:>10.4f}")

    # csv 재저장
    out_csv = "results_measurement_reproducibility.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["perturbation", "a3_normal_reproducibility", "b2_normal_reproducibility",
                    "a3_fixed_fpr", "b2_fixed_fpr", "a3_auroc", "b2_auroc",
                    "note"])
        for r in rows:
            w.writerow([r["perturbation"],
                        round(1.0 - r["a3_fix"], 4), round(1.0 - r["b2_fix"], 4),
                        r["a3_fix"], r["b2_fix"], r["a3_auroc"], r["b2_auroc"],
                        "정상 그룹 marginal 재현성 = 1 - fixed_threshold_fpr; 표본별 일치율 산출불가"])
    print("저장:", out_csv)

    # figure: 노이즈 sigma 만 뽑아서 재현성 곡선. 다른 교란은 별도 카테고리 막대.
    noise_rows = [r for r in rows if r["perturbation"] == "none" or r["perturbation"].startswith("noise_sigma_")]
    other_rows = [r for r in rows if not (r["perturbation"] == "none" or r["perturbation"].startswith("noise_sigma_"))]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5), gridspec_kw={"width_ratios": [1, 1.3]})

    # 좌: 노이즈 강도별 재현성 곡선
    sigmas = [0, 3, 8, 15]
    a3_repro = []
    b2_repro = []
    for sigma in sigmas:
        key = "none" if sigma == 0 else f"noise_sigma_{sigma}"
        r = next(row for row in rows if row["perturbation"] == key)
        a3_repro.append(1.0 - r["a3_fix"])
        b2_repro.append(1.0 - r["b2_fix"])
    ax1.plot(sigmas, a3_repro, color="#1f77b4", marker="o", linewidth=1.6, label="A3 밝기 표준편차")
    ax1.plot(sigmas, b2_repro, color="#d62728", marker="s", linewidth=1.6, label="B2 Mahalanobis")
    for x, ya, yb in zip(sigmas, a3_repro, b2_repro):
        ax1.annotate(f"{ya:.3f}", (x, ya), xytext=(4, 5), textcoords="offset points", fontsize=8, color="#1f77b4")
        ax1.annotate(f"{yb:.3f}", (x, yb), xytext=(4, -12), textcoords="offset points", fontsize=8, color="#d62728")
    ax1.set_xlabel("가우시안 노이즈 σ (센서 노이즈 강도)")
    ax1.set_ylabel("정상 그룹 재현성 (1 - fixFPR)")
    ax1.set_title("노이즈 강도별 정상 판정 재현성")
    ax1.grid(alpha=0.3)
    ax1.legend(loc="lower left", fontsize=9)
    ax1.set_ylim(-0.05, 1.05)
    ax1.axhline(0.95, color="gray", linestyle=":", linewidth=0.8, alpha=0.6)
    ax1.text(15, 0.955, "설계 기준 0.95", fontsize=7, color="gray", ha="right")

    # 우: 그 외 촬영 조건별 재현성 막대
    cats = [r["perturbation"] for r in other_rows]
    a3_bar = [1.0 - r["a3_fix"] for r in other_rows]
    b2_bar = [1.0 - r["b2_fix"] for r in other_rows]
    import numpy as np
    x = np.arange(len(cats))
    ax2.bar(x - 0.2, a3_bar, width=0.4, color="#1f77b4", label="A3")
    ax2.bar(x + 0.2, b2_bar, width=0.4, color="#d62728", label="B2")
    ax2.axhline(0.95, color="gray", linestyle=":", linewidth=0.8, alpha=0.6)
    ax2.set_xticks(x)
    ax2.set_xticklabels(cats, rotation=45, ha="right", fontsize=7.5)
    ax2.set_ylabel("정상 그룹 재현성 (1 - fixFPR)")
    ax2.set_title("기타 촬영 조건 변동별 정상 판정 재현성")
    ax2.set_ylim(-0.05, 1.05)
    ax2.grid(alpha=0.3, axis="y")
    ax2.legend(loc="lower left", fontsize=9)

    fig.suptitle("§22-MSA 측정 시스템 재현성 — 촬영 조건 변동 시 판정 일치율\n"
                 "정상 그룹 marginal (임계값 고정, N=3,600 정상 cell, 반복 없음)",
                 fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    out_png = "figures/measurement_reproducibility.png"
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    print("저장:", out_png)


main()
