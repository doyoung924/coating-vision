"""
59. 프레임당 결함 수 분포 vs 포아송 (§보완 4, 1단계)

YOLO 박스 수 (0~6, N=367 프레임, 총 543 박스) 가 포아송 분포를 따르는지
확인. c 관리도의 UCL = c̄ + 3√c̄ 유효성 검토.

산출:
  - 관측/기대도수 표
  - 분산/평균 비 (포아송이면 1)
  - 카이제곱 적합도 (기대도수 5 미만 병합)
  - 시퀀스별 평균·분산 표
  - figures/defect_count_distribution.png
"""

import csv
import json
from collections import defaultdict, Counter
import math
import numpy as np
# scipy 없이 카이제곱 p-value 근사 (Wilson-Hilferty 변환)
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
    # detections_cache.json + results_mask_area.csv 로 프레임당 박스 수 산출
    with open("detections_cache.json") as f:
        cache = json.load(f)
    stem_to_seq = {}
    with open("results_mask_area.csv") as f:
        for r in csv.DictReader(f):
            stem_to_seq[r["stem"]] = (r["run_id"], r["coating_gap"], r["position"], r["frame_number"])

    frame_boxes = defaultdict(int)
    for stem_jpg, boxes in cache.items():
        stem = stem_jpg.replace(".jpg", "")
        if stem in stem_to_seq:
            frame_boxes[stem_to_seq[stem]] += len(boxes)

    counts = list(frame_boxes.values())
    N = len(counts)
    mean = np.mean(counts)
    var = np.var(counts, ddof=1)
    total = sum(counts)

    print("=" * 80)
    print("§보완4-1 프레임당 결함 수 분포 vs 포아송")
    print("=" * 80)
    print(f"N (프레임) = {N}")
    print(f"총 박스 수 = {total}")
    print(f"평균 c̄ = {mean:.4f}")
    print(f"분산 = {var:.4f}")
    print(f"분산/평균 비 (dispersion index) = {var/mean:.4f}")
    print(f"  → 포아송이면 1. > 1 이면 과분산 (overdispersion)")
    print()

    # 관측 vs 포아송 기대도수
    obs_hist = Counter(counts)
    max_k = max(obs_hist.keys())
    print("[관측 vs 포아송 기대도수]")
    print(f"{'k':>3}{'obs':>8}{'exp(λ={:.4f})'.format(mean):>16}")
    obs_arr = []
    exp_arr = []
    for k in range(0, max_k + 1):
        exp = N * (math.exp(-mean) * mean**k / math.factorial(k))
        obs = obs_hist.get(k, 0)
        obs_arr.append(obs)
        exp_arr.append(exp)
        print(f"{k:>3}{obs:>8}{exp:>16.2f}")
    # k >= max_k+1 확률 잔여를 마지막 bin 에 합산 (총합 보존)
    tail_exp = N - sum(exp_arr)
    print(f"  (기대 tail k >= {max_k+1}: {tail_exp:.2f})")

    # 카이제곱: 기대도수 5 미만 병합
    print()
    print("[카이제곱 적합도 (기대도수 >= 5 병합)]")
    merged_obs = []
    merged_exp = []
    merged_labels = []
    cur_obs = 0
    cur_exp = 0.0
    cur_labels = []
    for k in range(0, max_k + 1):
        cur_obs += obs_arr[k]
        cur_exp += exp_arr[k]
        cur_labels.append(str(k))
        if cur_exp >= 5:
            merged_obs.append(cur_obs)
            merged_exp.append(cur_exp)
            merged_labels.append(",".join(cur_labels))
            cur_obs = 0; cur_exp = 0.0; cur_labels = []
    # 잔여 병합
    if cur_labels:
        cur_exp += tail_exp
        cur_obs += 0
        if merged_exp:
            merged_obs[-1] += cur_obs
            merged_exp[-1] += cur_exp
            merged_labels[-1] += ",{}+".format(cur_labels[0]) if cur_labels else ""
        else:
            merged_obs.append(cur_obs); merged_exp.append(cur_exp); merged_labels.append(",".join(cur_labels)+"+")
    else:
        # 마지막 bin 에 tail exp 추가
        merged_exp[-1] += tail_exp

    print(f"{'구간':<20}{'obs':>8}{'exp':>10}")
    for lab, o, e in zip(merged_labels, merged_obs, merged_exp):
        print(f"{lab:<20}{o:>8}{e:>10.2f}")
    chi2 = sum((o - e)**2 / e for o, e in zip(merged_obs, merged_exp) if e > 0)
    dof = len(merged_obs) - 1 - 1  # 병합 후 구간 수 - 1 - 추정 파라미터 1개(λ)
    # Wilson-Hilferty 근사 (dof>=1 에서 정확도 충분)
    if dof > 0:
        z = ((chi2 / dof) ** (1/3) - (1 - 2/(9*dof))) / math.sqrt(2/(9*dof))
        # 표준정규 CDF 근사
        from math import erf
        cdf_z = 0.5 * (1 + erf(z / math.sqrt(2)))
        p_value = 1 - cdf_z
    else:
        p_value = float("nan")
    print(f"chi2 = {chi2:.4f}, dof = {dof}, p-value (Wilson-Hilferty) = {p_value:.4g}")
    print(f"  → p < 0.05 이면 포아송 가설 기각")
    # 참고: dof=1 인 임계값 chi2_0.05,1 = 3.841, chi2_0.01,1 = 6.635
    print(f"  참고: chi2 임계값 (dof={dof}): 0.05={3.841 if dof==1 else '?'}, 0.01={6.635 if dof==1 else '?'}")

    # 시퀀스별 평균/분산
    print()
    print("=" * 80)
    print("§보완4-2 시퀀스별 결함 수 평균·분산")
    print("=" * 80)
    by_seq = defaultdict(list)
    for (run, gap, pos, frame), n in frame_boxes.items():
        by_seq[(run, gap, pos)].append(n)
    print(f"{'시퀀스':<40}{'n_frame':>8}{'mean':>10}{'var':>10}{'var/mean':>10}")
    for key in sorted(by_seq.keys()):
        vals = by_seq[key]
        if len(vals) < 2:
            continue
        m = np.mean(vals); v = np.var(vals, ddof=1)
        ratio = v/m if m > 0 else float("nan")
        seq_str = f"{key[0]}/{key[1]}/{key[2][:20]}"
        print(f"{seq_str:<40}{len(vals):>8}{m:>10.4f}{v:>10.4f}{ratio:>10.4f}")

    # figure: 관측 히스토그램 + 포아송 곡선
    fig, ax = plt.subplots(figsize=(8, 5))
    ks = list(range(0, max_k + 1))
    obs_pmf = [obs_arr[k] / N for k in ks]
    exp_pmf = [exp_arr[k] / N for k in ks]
    x = np.arange(len(ks))
    width = 0.4
    ax.bar(x - width/2, obs_pmf, width, label="관측", color="#1f77b4", alpha=0.85)
    ax.bar(x + width/2, exp_pmf, width, label=f"포아송 (λ={mean:.3f})", color="#d62728", alpha=0.85)
    ax.set_xticks(x); ax.set_xticklabels([str(k) for k in ks])
    ax.set_xlabel("프레임당 YOLO 박스 수")
    ax.set_ylabel("빈도 (proportion)")
    ax.set_title(f"§보완4-1 관측 vs 포아송  (N={N}, c̄={mean:.3f}, 분산/평균={var/mean:.3f})")
    ax.grid(alpha=0.3, axis="y")
    ax.legend()
    fig.tight_layout()
    out = "figures/defect_count_distribution.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print()
    print(f"저장: {out}")


main()
