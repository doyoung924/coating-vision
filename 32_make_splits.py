"""
32. train/val/test 분할 생성 (§18-9 확정안)

정책:
  - test: R7/700/middle 전체 (25 프레임 / 132 patch). §15-7 도메인 이전성 대리
  - train/val: 나머지 7 시퀀스 (342 프레임 / 2,095 patch) 를 시퀀스 stratify.
    각 시퀀스 안에서 프레임 단위 80:20 로 분할
  - 프레임 단위 분할이므로 형제 patch 는 자동으로 함께 묶임 (§15-5·§3 재발 방지)
  - seed 고정 (42)

산출: splits.csv (stem, run_id, coating_gap, position, frame_number, split)

원칙: 학습·업로드 시작 금지. 이 스크립트는 분할 CSV 만 만든다.
"""

import csv
import os
import random
import sys
from collections import defaultdict

LABELS_CSV = "classification/labels.csv"
OUT_CSV = "splits.csv"
SEED = 42
VAL_FRAC = 0.20   # train 0.8 : val 0.2


def parse_original_filename(name):
    import re
    m = re.match(r"^(R\d+)-?(\d+)um[-_](.+?)_frame_(\d+)", name)
    if not m: return None
    return {
        "run_id": m.group(1),
        "coating_gap": int(m.group(2)),
        "position": m.group(3),
        "frame_number": int(m.group(4)),
    }


def main():
    if os.path.exists(OUT_CSV):
        print("이미 존재:", OUT_CSV, "— 재실행 전 옮길 것.")
        sys.exit(1)

    print("=" * 92)
    print("32. train/val/test 분할 (프레임 단위 stratify)")
    print("=" * 92)

    # labels.csv 로드
    all_stems = []
    with open(LABELS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = parse_original_filename(row["original_file_name"])
            if parsed is None: continue
            stem = row["file_name"]
            if stem.endswith(".jpg"): stem = stem[:-4]
            parsed["stem"] = stem
            all_stems.append(parsed)

    print("총 patch:", len(all_stems))

    # 시퀀스별로 stem 그룹핑 후 프레임별로도 그룹핑
    by_seq = defaultdict(lambda: defaultdict(list))   # seq -> frame_number -> [stem_records]
    for rec in all_stems:
        key = (rec["run_id"], rec["coating_gap"], rec["position"])
        by_seq[key][rec["frame_number"]].append(rec)

    # 분할 규칙
    #   R7/700 → test
    #   나머지 시퀀스 → 프레임 리스트 정렬 후 shuffle 하고 앞 80% train, 뒤 20% val
    R7_KEY = ("R7", 700, "middle")
    rng = random.Random(SEED)

    assignment = {}   # stem -> split
    per_seq_report = {}   # seq -> {n_frames, n_patches, split_counts}

    for seq_key in sorted(by_seq.keys()):
        frames = sorted(by_seq[seq_key].keys())
        n_frames = len(frames)
        n_patches = sum(len(by_seq[seq_key][fn]) for fn in frames)

        if seq_key == R7_KEY:
            # 전량 test
            for fn in frames:
                for rec in by_seq[seq_key][fn]:
                    assignment[rec["stem"]] = "test"
            per_seq_report[seq_key] = {
                "n_frames": n_frames, "n_patches": n_patches,
                "train_f": 0, "val_f": 0, "test_f": n_frames,
                "train_p": 0, "val_p": 0, "test_p": n_patches,
            }
            continue

        # 프레임 순서 무작위 셔플 후 80:20
        frames_shuffled = list(frames)
        rng.shuffle(frames_shuffled)
        n_val = int(round(n_frames * VAL_FRAC))
        val_frames = set(frames_shuffled[:n_val])
        train_frames = set(frames_shuffled[n_val:])

        train_p = 0
        val_p = 0
        for fn in frames:
            split = "train" if fn in train_frames else "val"
            for rec in by_seq[seq_key][fn]:
                assignment[rec["stem"]] = split
                if split == "train": train_p += 1
                else: val_p += 1

        per_seq_report[seq_key] = {
            "n_frames": n_frames, "n_patches": n_patches,
            "train_f": len(train_frames), "val_f": len(val_frames), "test_f": 0,
            "train_p": train_p, "val_p": val_p, "test_p": 0,
        }

    # CSV 저장
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "stem", "run_id", "coating_gap", "position", "frame_number", "split"])
        writer.writeheader()
        for rec in all_stems:
            writer.writerow({
                "stem": rec["stem"],
                "run_id": rec["run_id"],
                "coating_gap": rec["coating_gap"],
                "position": rec["position"],
                "frame_number": rec["frame_number"],
                "split": assignment[rec["stem"]],
            })
    print("저장:", OUT_CSV)
    print()

    # 시퀀스별 표
    print("=" * 92)
    print("시퀀스별 분할 결과")
    print("=" * 92)
    header = ("  " + "시퀀스".ljust(40) + "frames".rjust(9) + "patches".rjust(10)
              + "  " + "train_f".rjust(8) + "val_f".rjust(7) + "test_f".rjust(8)
              + "  " + "train_p".rjust(9) + "val_p".rjust(8) + "test_p".rjust(9))
    print(header)
    print("  " + "-" * (len(header) - 2))
    totals = {"n_frames": 0, "n_patches": 0, "train_f": 0, "val_f": 0, "test_f": 0,
              "train_p": 0, "val_p": 0, "test_p": 0}
    for seq_key in sorted(per_seq_report.keys()):
        r = per_seq_report[seq_key]
        for k in totals: totals[k] += r[k]
        line = "  " + str(seq_key).ljust(40)
        line += str(r["n_frames"]).rjust(9) + str(r["n_patches"]).rjust(10)
        line += "  " + str(r["train_f"]).rjust(8) + str(r["val_f"]).rjust(7) + str(r["test_f"]).rjust(8)
        line += "  " + str(r["train_p"]).rjust(9) + str(r["val_p"]).rjust(8) + str(r["test_p"]).rjust(9)
        print(line)
    print("  " + "-" * (len(header) - 2))
    line = "  " + "전체".ljust(40) + str(totals["n_frames"]).rjust(9) + str(totals["n_patches"]).rjust(10)
    line += "  " + str(totals["train_f"]).rjust(8) + str(totals["val_f"]).rjust(7) + str(totals["test_f"]).rjust(8)
    line += "  " + str(totals["train_p"]).rjust(9) + str(totals["val_p"]).rjust(8) + str(totals["test_p"]).rjust(9)
    print(line)


main()
