"""
41 (재작성). test 분할 이원화 — 시퀀스별 비례, 층화 없음

배경:
  이전 판(41 v1) 은 delam 포함 프레임 우선 층화로 test_in 을 만들었으나
  train delam patch 를 124 → 40 으로 감소시켰다 (68% 손실).
  사용자 결정: **희소 클래스로 층화하지 않는다.**
  splits_prev.csv (32 산출 원본, 3-split) 를 소스로 다시 시작한다.

방법:
  1. splits_prev.csv 로드 → train 253 프레임? 사실 274 프레임.
     (test_in 이 반영 안 된 원본 splits.csv 상태 = train 1,713 patch / 274 frame)
  2. 시퀀스별 프레임 수 × 21/274 로 비례 배분 (합이 21 이 되도록 잔여 처리)
  3. 각 시퀀스 안에서 seed 42 shuffle 후 앞에서 목표 개수만큼 프레임을 test_in 으로 지정
  4. val 은 건드리지 않음
  5. splits.csv 갱신 (train / val / test_in / test_out)

역할 (§2):
  - test_in: 같은 분포에서 crack IoU 를 본다. delam 이 적거나 없어도 문제없음
  - test_out (R7/700): 도메인 이전성. delam 은 여기서 평가 (delam 5.12%)
  - 이 역할 구분은 학습 스크립트 주석과 experiment_log 에도 반영

원칙:
  - 희소 클래스 층화 금지
  - val 건드리지 않음
  - 학습·업로드 시작 없음
"""

import csv
import os
import random
import shutil
import sys
import numpy as np
from PIL import Image
from collections import defaultdict


SPLITS_IN = "splits_prev.csv"     # 원본 3-split 복사본
SPLITS_OUT = "splits.csv"         # 새로 갱신 (test_in 포함)
MASK_DIR = "segmentation/masks"

SEED = 42
TARGET_TEST_IN_FRAMES = 21


def frame_key(row):
    return (row["run_id"], int(row["coating_gap"]), row["position"], int(row["frame_number"]))


def allocate_by_proportion(freq_by_seq, target_total, seq_order):
    """
    시퀀스별 float 비례 → floor + 잔여(fractional) 큰 순서로 +1.
    반환: 시퀀스 → 정수 개수.
    """
    total_frames = sum(freq_by_seq.values())
    exact = {s: target_total * freq_by_seq[s] / total_frames for s in seq_order}
    floors = {s: int(exact[s]) for s in seq_order}
    remainder = target_total - sum(floors.values())
    # fractional 큰 순서
    fracs = sorted(seq_order, key=lambda s: -(exact[s] - floors[s]))
    for i in range(remainder):
        floors[fracs[i]] += 1
    return floors, exact


def main():
    if not os.path.exists(SPLITS_IN):
        print(SPLITS_IN, "없음. splits_prev.csv 가 필요하다.")
        sys.exit(1)

    print("=" * 82)
    print("41 (재작성). test-in 재분할 — 시퀀스별 비례, 층화 없음")
    print("=" * 82)

    # 1. splits_prev 로드
    rows = []
    with open(SPLITS_IN) as f:
        for r in csv.DictReader(f):
            rows.append(r)
    train_rows = [r for r in rows if r["split"] == "train"]
    val_rows = [r for r in rows if r["split"] == "val"]
    test_rows = [r for r in rows if r["split"] == "test"]
    print(f"splits_prev 로드: train {len(train_rows)}, val {len(val_rows)}, test {len(test_rows)}")

    # 2. train 프레임 그룹핑
    frames_by_seq = defaultdict(dict)   # (seq) -> {frame_num: [rows]}
    for r in train_rows:
        seq = (r["run_id"], int(r["coating_gap"]), r["position"])
        fn = int(r["frame_number"])
        frames_by_seq[seq].setdefault(fn, []).append(r)

    seq_order = sorted(frames_by_seq.keys(), key=lambda s: (s[0], s[1], s[2]))
    seq_frame_count = {s: len(frames_by_seq[s]) for s in seq_order}
    total_train_frames = sum(seq_frame_count.values())
    print(f"train unique 원본 프레임: {total_train_frames}")

    # 3. 시퀀스별 비례 배분
    alloc, exact = allocate_by_proportion(seq_frame_count, TARGET_TEST_IN_FRAMES, seq_order)
    print()
    print(f"목표 test_in 프레임: {TARGET_TEST_IN_FRAMES}")
    print(f"  {'시퀀스':<40}{'train frames':>15}{'exact':>10}{'배분':>8}")
    total_alloc = 0
    for s in seq_order:
        exact_v = exact[s]
        alloc_v = alloc[s]
        total_alloc += alloc_v
        print(f"  {str(s):<40}{seq_frame_count[s]:>15}{exact_v:>10.3f}{alloc_v:>8}")
    print(f"  {'합계':<40}{total_train_frames:>15}{sum(exact.values()):>10.3f}{total_alloc:>8}")
    print()

    # 4. 각 시퀀스 안에서 shuffle 후 선택
    rng = random.Random(SEED)
    test_in_stems = set()
    test_in_seq_dist = defaultdict(int)
    for s in seq_order:
        frame_nums = sorted(frames_by_seq[s].keys())
        rng.shuffle(frame_nums)
        chosen = frame_nums[:alloc[s]]
        for fn in chosen:
            for row in frames_by_seq[s][fn]:
                test_in_stems.add(row["stem"])
            test_in_seq_dist[s] += 1
    print(f"선택된 test_in patch 수: {len(test_in_stems)} (프레임 {sum(test_in_seq_dist.values())})")
    print()

    # 5. splits.csv 갱신 (train → 유지분/test_in 분리, test → test_out)
    new_rows = []
    for r in rows:
        new = dict(r)
        if new["split"] == "test":
            new["split"] = "test_out"
        elif new["split"] == "train" and new["stem"] in test_in_stems:
            new["split"] = "test_in"
        new_rows.append(new)

    with open(SPLITS_OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for r in new_rows:
            writer.writerow(r)
    print(f"갱신: {SPLITS_OUT}")
    print()

    # 6. 시퀀스별 배분 표
    print("=" * 82)
    print("시퀀스별 train 크기 변화 (before → after) + test_in 배분")
    print("=" * 82)

    seq_before = defaultdict(int)
    seq_after = defaultdict(int)
    test_in_patch_dist = defaultdict(int)
    for r in new_rows:
        seq = (r["run_id"], int(r["coating_gap"]), r["position"])
        if r["split"] == "train":
            seq_after[seq] += 1
        if r["split"] == "test_in":
            test_in_patch_dist[seq] += 1
    for r in train_rows:
        seq = (r["run_id"], int(r["coating_gap"]), r["position"])
        seq_before[seq] += 1

    print(f"  {'시퀀스':<40}{'before':>10}{'test_in':>10}{'after':>10}{'test_in frames':>18}")
    for s in seq_order:
        b = seq_before[s]
        moved = test_in_patch_dist[s]
        a = seq_after[s]
        n_f = test_in_seq_dist[s]
        print(f"  {str(s):<40}{b:>10}{moved:>10}{a:>10}{n_f:>18}")
    print()

    # 7. 프레임 누수 검증
    frame_to_splits = defaultdict(set)
    for r in new_rows:
        frame_to_splits[frame_key(r)].add(r["split"])
    shared = sum(1 for splits in frame_to_splits.values() if len(splits) > 1)
    print(f"프레임 누수 (여러 split 걸침): {shared}  [{'OK' if shared == 0 else '⚠'}]")
    print()

    # 8. 4 split 크기·마스크 픽셀 카운트
    print("=" * 82)
    print("4 split 크기 및 클래스 분포")
    print("=" * 82)

    pixel_counts = defaultdict(lambda: {0: 0, 1: 0, 2: 0})
    patch_with_class = defaultdict(lambda: {1: 0, 2: 0})
    split_stems = defaultdict(set)
    split_frames = defaultdict(set)

    for r in new_rows:
        split = r["split"]
        stem = r["stem"]
        split_stems[split].add(stem)
        split_frames[split].add(frame_key(r))

        mask_path = os.path.join(MASK_DIR, stem + ".png")
        if not os.path.exists(mask_path):
            continue
        mask = np.array(Image.open(mask_path))
        ch0 = mask[..., 0] > 0
        ch1 = mask[..., 1] > 0
        n1 = int(ch0.sum())
        n2 = int(ch1.sum())
        n0 = mask.shape[0] * mask.shape[1] - n1 - n2
        pixel_counts[split][0] += n0
        pixel_counts[split][1] += n1
        pixel_counts[split][2] += n2
        if n1 > 0:
            patch_with_class[split][1] += 1
        if n2 > 0:
            patch_with_class[split][2] += 1

    print("  {:<12}{:>10}{:>10}{:>13}{:>13}{:>13}{:>13}{:>13}".format(
        "split", "patches", "frames", "bg %", "crack %", "delam %",
        "crack patch", "delam patch"))
    for s in ["train", "val", "test_in", "test_out"]:
        pc = pixel_counts[s]
        total = pc[0] + pc[1] + pc[2]
        if total == 0:
            continue
        print(f"  {s:<12}{len(split_stems[s]):>10}{len(split_frames[s]):>10}"
              f"{pc[0]/total:>13.4%}{pc[1]/total:>13.4%}{pc[2]/total:>13.4%}"
              f"{patch_with_class[s][1]:>13}{patch_with_class[s][2]:>13}")
    print()

    # 9. 가중치 재계산 (§3)
    print("=" * 82)
    print("§3 손실 가중치 재계산 (train 픽셀 기준, sqrt 완화 + mean=1)")
    print("=" * 82)

    train_total = sum(pixel_counts["train"].values())
    freq = np.array([pixel_counts["train"][c] / train_total for c in (0, 1, 2)])
    print("  train 픽셀 빈도:")
    for c in (0, 1, 2):
        print(f"    class {c}: {freq[c]:.6f}")

    raw = 1.0 / freq
    sqrt_w = np.sqrt(raw)
    normalized = sqrt_w / sqrt_w.mean()

    print()
    print("  {:<10}{:>15}{:>15}{:>15}".format("class", "빈도 역수", "sqrt 완화", "mean=1 정규화"))
    for c in (0, 1, 2):
        print(f"  {c:<10}{raw[c]:>15.4f}{sqrt_w[c]:>15.4f}{normalized[c]:>15.4f}")
    print()
    print("  적용 값 (CrossEntropy weight):")
    print(f"    background (0): {normalized[0]:.4f}")
    print(f"    crack (1)     : {normalized[1]:.4f}")
    print(f"    delam (2)     : {normalized[2]:.4f}")


main()
