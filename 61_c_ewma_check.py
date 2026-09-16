"""
61. c 관리도에 Poisson EWMA 적용 시 이탈점 비교 (§보완4 설계 자료)

새 추론 없이 기존 detections_cache.json 재집계.
- (가) Shewhart 단독 c 관리도: UCL = c̄ + 3√c̄
- (나) Shewhart + Poisson EWMA 이중 판정 UCL_ewma = c̄ + 3·√(c̄·λ/(2−λ))
  λ ∈ {0.1, 0.2, 0.3} 스윕 (근거로 채택된 값 없음, 관찰용)
- 정답은 없음 (계수형에는 실측된 "정답 알람" 이 없다).
  두 방식이 잡는 이탈점 수·시퀀스 위치만 비교

또한 c=0 연속 구간 (검출기 침묵) 산출.
"""

import csv
import json
import math
from collections import defaultdict, Counter
import numpy as np


def main():
    with open("detections_cache.json") as f:
        cache = json.load(f)
    stem_to_seq = {}
    with open("results_mask_area.csv") as f:
        for r in csv.DictReader(f):
            stem_to_seq[r["stem"]] = (r["run_id"], r["coating_gap"], r["position"], int(r["frame_number"]))

    frame_c = defaultdict(int)
    for stem_jpg, boxes in cache.items():
        stem = stem_jpg.replace(".jpg", "")
        if stem in stem_to_seq:
            frame_c[stem_to_seq[stem]] += len(boxes)

    # 시퀀스별 프레임 순 정렬
    seq_frames = defaultdict(list)
    for key, c in frame_c.items():
        seq_frames[(key[0], key[1], key[2])].append((key[3], c))
    for k in seq_frames:
        seq_frames[k].sort(key=lambda x: x[0])

    total_c = sum(frame_c.values())
    N = len(frame_c)
    c_bar = total_c / N
    c_ucl_shewhart = c_bar + 3 * math.sqrt(c_bar)

    print("=" * 90)
    print(f"§보완4-설계 c 관리도 EWMA 스윕 (통합, c̄={c_bar:.4f}, N={N})")
    print("=" * 90)
    print(f"Shewhart UCL = c̄ + 3√c̄ = {c_ucl_shewhart:.4f}")
    print()

    # 시퀀스별 시계열로 EWMA 계산. 통합 c̄ 를 CENTER 로.
    all_frames_sorted = []
    for seq in sorted(seq_frames.keys()):
        for (fn, c) in seq_frames[seq]:
            all_frames_sorted.append((seq, fn, c))

    # Shewhart 이탈
    shew_out = [(s, fn, c) for (s, fn, c) in all_frames_sorted if c > c_ucl_shewhart]
    print(f"Shewhart 이탈: {len(shew_out)} 프레임 (UCL={c_ucl_shewhart:.3f})")
    for s, fn, c in shew_out:
        print(f"  {s[0]}/{s[1]}/{s[2]:<25} frame={fn}  c={c}")

    # Poisson EWMA (시퀀스 단위 재귀; CENTER 를 c̄ 로)
    print()
    print(f"{'λ':>6}{'UCL (steady-state)':>25}{'EWMA 이탈':>12}{'추가 잡음 (Shew 밖)':>25}")
    for lam in [0.1, 0.2, 0.3]:
        # steady-state UCL: c̄ + 3·√(c̄·λ/(2−λ))
        ucl_ss = c_bar + 3 * math.sqrt(c_bar * lam / (2 - lam))
        ewma_out_all = []
        for seq in sorted(seq_frames.keys()):
            prev = c_bar
            for (fn, c) in seq_frames[seq]:
                cur = lam * c + (1 - lam) * prev
                # exact time-varying UCL: c̄ + 3·√(c̄·λ/(2−λ)·(1−(1−λ)^(2t)))
                # steady-state 로 근사 (충분한 t 이후). 실제로는 t 별 UCL 이 자연스러움
                if cur > ucl_ss:
                    ewma_out_all.append((seq, fn, c, cur))
                prev = cur
        # Shewhart 이탈 밖의 추가 검출
        shew_set = {(s, fn) for (s, fn, _) in shew_out}
        additional = [(s, fn, c, e) for (s, fn, c, e) in ewma_out_all if (s, fn) not in shew_set]
        print(f"{lam:>6.2f}{ucl_ss:>25.4f}{len(ewma_out_all):>12}{len(additional):>25}")
        if additional and lam == 0.2:
            print(f"    λ=0.2 추가 이탈 예시 (Shewhart 놓친 것):")
            for s, fn, c, e in additional[:5]:
                print(f"      {s[0]}/{s[1]}/{s[2]:<25} frame={fn}  c={c} EWMA={e:.3f}")

    # ================================
    # c=0 연속 구간 (검출기 침묵 감시)
    # ================================
    print()
    print("=" * 90)
    print("§보완4-설계 c=0 연속 구간 (검출기 침묵 감시)")
    print("=" * 90)
    max_run = 0
    max_seq = None
    all_runs = []
    for seq in sorted(seq_frames.keys()):
        cur = 0
        for (fn, c) in seq_frames[seq]:
            if c == 0:
                cur += 1
                if cur > max_run:
                    max_run = cur
                    max_seq = seq
            else:
                if cur > 0:
                    all_runs.append(cur)
                cur = 0
        if cur > 0:
            all_runs.append(cur)
    print(f"c=0 연속 최장: {max_run} 프레임 ({max_seq[0]}/{max_seq[1]}/{max_seq[2] if max_seq else ''})")
    print(f"연속 길이 분포: {dict(Counter(all_runs))}")
    print()

    # 포아송 가정에서 P(c=0) = e^-c̄, k 연속 확률 = P^k
    p0 = math.exp(-c_bar)
    print(f"포아송 가정 P(c=0) = e^-{c_bar:.3f} = {p0:.4f}")
    print(f"연속 길이별 확률 (독립 가정):")
    for k in range(1, 11):
        pk = p0 ** k
        # N=367 프레임에서 k 연속 0 이 최소 한 번 발생할 근사 확률
        # 정확한 run 확률은 복잡하므로 boundary 근사: 시작점 개수 ≈ N·pk
        exp_starts = N * pk
        print(f"  k={k:>2}  P(0 연속 {k}) = {pk:.6f}  기대 시작점 수 (N·P) = {exp_starts:.3f}")
        if exp_starts < 0.05:  # 20:1 이상 드묾
            print(f"    ↑ N=367 기준 20:1 이상 드문 사건. k={k} 이상 연속은 이상 신호 후보")
            break


main()
