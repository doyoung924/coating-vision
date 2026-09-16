"""
62. 갭 vs 결함 지표 (§25 논문 경향 재현 검증)

논문 (Sci Data 2026, DOI 10.1038/s41597-025-06419-1) 은 갭이 커질수록
결함이 증가한다고 서술. 이 저장소의 시퀀스별 지표로 세 축에서 대조:

1. 갭 vs c̄ (프레임당 YOLO 핀홀 검출 수, §24-5)
2. 갭 vs area_crack (patch 단위 마스크 크랙 면적비, results_mask_area.csv)
3. 갭 vs 결함 프레임 비율 (area_or > 0 patch 비율)

각 축에서 Spearman 상관 · 산점도 산출. figures/gap_vs_defect.png 저장.

R7/700 은 유일 R7 · 유일 middle 위치라 run·위치 교란 → R1 시퀀스 (n=7) 만
gap 효과의 주 분석 대상. R7 은 산점도에 별도 마커.

새 추론 없음. 기존 results_mask_area.csv + §24-5 표 사용.
"""

import csv
import math
from collections import defaultdict

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
    if n < 3:
        return float("nan")

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
    rx = rank(x)
    ry = rank(y)
    mx = sum(rx)/n
    my = sum(ry)/n
    num = sum((rx[i]-mx)*(ry[i]-my) for i in range(n))
    den = math.sqrt(
        sum((rx[i]-mx)**2 for i in range(n))
        * sum((ry[i]-my)**2 for i in range(n))
    )
    return num/den if den > 0 else float("nan")


# §24-5 c̄ 값 (하드코딩 · experiment_log 표 인용)
C_BAR = {
    ("R1", "600",  "top-to-bottom-center"):   1.735,
    ("R1", "700",  "top-to-bottom-center"):   1.558,
    ("R1", "800",  "top-to-bottom-center"):   1.526,
    ("R1", "900",  "top-to-bottom-center"):   1.077,
    ("R1", "1000", "top-to-bottom-center"):   1.514,
    ("R1", "1100", "top-to-bottom-center"):   1.759,
    ("R1", "1100", "top-to-bottom-center-1"): 1.216,
    ("R7", "700",  "middle"):                 0.880,
}


def load_area_stats():
    """results_mask_area.csv 에서 시퀀스별 area_crack · area_or·defect_ratio 집계."""
    rows_by_seq = defaultdict(list)
    with open("results_mask_area.csv") as f:
        for r in csv.DictReader(f):
            key = (r["run_id"], r["coating_gap"], r["position"])
            rows_by_seq[key].append({
                "area_crack": float(r["area_crack"]),
                "area_or":    float(r["area_or"]),
            })

    stats = {}
    for key, rows in rows_by_seq.items():
        n = len(rows)
        m_crack = sum(row["area_crack"] for row in rows) / n
        with_def = sum(1 for row in rows if row["area_or"] > 0) / n
        stats[key] = {
            "n_patch": n,
            "area_crack_mean": m_crack,
            "defect_ratio": with_def,
        }
    return stats


def main():
    area_stats = load_area_stats()

    # 시퀀스별 표
    print("=" * 100)
    print(f"§25 갭 vs 결함 지표 (n=8 시퀀스; R1 만 n=7)")
    print("=" * 100)
    print(f"{'seq':<40}{'gap(µm)':>10}{'c_bar':>10}{'area_crack':>14}{'defect_ratio':>16}")
    r1_gap = []
    r1_c = []
    r1_ac = []
    r1_df = []
    r7_row = None
    for key in sorted(C_BAR.keys(), key=lambda k: (k[0], int(k[1]), k[2])):
        gap = int(key[1])
        stats = area_stats[key]
        c = C_BAR[key]
        seq_label = f"{key[0]}/{key[1]}/{key[2][:20]}"
        print(f"{seq_label:<40}{gap:>10}{c:>10.3f}{stats['area_crack_mean']:>14.4f}{stats['defect_ratio']:>16.4f}")
        if key[0] == "R1":
            r1_gap.append(gap)
            r1_c.append(c)
            r1_ac.append(stats["area_crack_mean"])
            r1_df.append(stats["defect_ratio"])
        else:
            r7_row = (gap, c, stats["area_crack_mean"], stats["defect_ratio"])

    # R1 only Spearman
    sp_c    = spearman(r1_gap, r1_c)
    sp_ac   = spearman(r1_gap, r1_ac)
    sp_df   = spearman(r1_gap, r1_df)
    print()
    print(f"R1 only (n=7) Spearman(gap, X):")
    print(f"  vs c_bar         = {sp_c:+.4f}")
    print(f"  vs area_crack    = {sp_ac:+.4f}")
    print(f"  vs defect_ratio  = {sp_df:+.4f}")

    # All 8 Spearman (R7 포함, 교란 병기)
    all_gap = r1_gap + [r7_row[0]]
    all_c   = r1_c   + [r7_row[1]]
    all_ac  = r1_ac  + [r7_row[2]]
    all_df  = r1_df  + [r7_row[3]]
    print()
    print(f"All 8 (R1+R7) Spearman(gap, X)  [R7 는 run·position 교란]:")
    print(f"  vs c_bar         = {spearman(all_gap, all_c):+.4f}")
    print(f"  vs area_crack    = {spearman(all_gap, all_ac):+.4f}")
    print(f"  vs defect_ratio  = {spearman(all_gap, all_df):+.4f}")

    # 논문 경향 (갭↑ → 결함↑) 재현 판정
    print()
    print("논문 경향 (Sci Data 2026, 갭 증가 → 결함 증가) 재현 판정 (R1 n=7):")
    def verdict(sp):
        if sp > 0.5: return "재현"
        if sp > 0.0: return "약한 재현"
        if sp > -0.3: return "미재현"
        return "역방향"
    print(f"  c_bar:        Spearman={sp_c:+.3f}  → {verdict(sp_c)}")
    print(f"  area_crack:   Spearman={sp_ac:+.3f}  → {verdict(sp_ac)}")
    print(f"  defect_ratio: Spearman={sp_df:+.3f}  → {verdict(sp_df)}")

    # 산점도 3 패널
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    for ax, y_r1, y_r7, sp, ylabel, title in [
        (axes[0], r1_c,  r7_row[1], sp_c,  "c̄ (프레임당 YOLO 핀홀 수)", f"갭 vs 핀홀 c̄"),
        (axes[1], r1_ac, r7_row[2], sp_ac, "area_crack (patch mean)",    f"갭 vs 크랙 면적비"),
        (axes[2], r1_df, r7_row[3], sp_df, "결함 프레임 비율",             f"갭 vs 결함 patch 비율"),
    ]:
        ax.scatter(r1_gap, y_r1, s=80, color="#1f77b4", label="R1 시퀀스")
        # R1/1100 두 점 분리 표시 (같은 갭)
        ax.scatter(r7_row[0], y_r7, s=100, color="#d62728", marker="^", label="R7/700 (교란: run·position)")
        # 라벨
        for gap, y in zip(r1_gap, y_r1):
            ax.annotate(f"{gap}", (gap, y), xytext=(4, 4), textcoords="offset points", fontsize=8)
        ax.set_xlabel("코팅 갭 (µm)")
        ax.set_ylabel(ylabel)
        ax.set_title(f"{title}\nSpearman (R1 n=7) = {sp:+.3f}")
        ax.grid(alpha=0.3)
        ax.legend(loc="lower right" if sp > 0 else "upper right", fontsize=8)

    fig.suptitle("§25 갭 vs 결함 (논문 경향 재현 검증)", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig("figures/gap_vs_defect.png", dpi=140)
    plt.close(fig)
    print()
    print("저장: figures/gap_vs_defect.png")


main()
