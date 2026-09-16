"""
36. 프레임 단위 학습(seed 0~3)의 R7/700 test 평가 재추출

배경 (§18-12, §RunPod 회수 후):
  yolo detect val 은 stdout 만 출력하고 metric 파일을 저장하지 않는다.
  RunPod 학습 결과(runs/pinhole_frames_seed{0..3}) 는 회수했지만
  test 폴더에는 png 만 있다. 로컬 CPU 로 best.pt 를 다시 val 만 실행.
  학습은 아니다.

산출:
  - runs/pinhole_frames_seed{0..3}_test_local/  (기존 _test 는 유지)
  - results_pinhole_frames_test.csv (seed, mAP50, mAP50-95, P, R)
  - 각 seed stdout 로그: logs/eval_seed{i}.log
"""

import os
import sys
import csv
import time
import contextlib
import io

from ultralytics import YOLO


DATA_YAML = "data/pinhole_frames_balanced/data.yaml"
LOG_DIR = "logs"
RESULTS_CSV = "results_pinhole_frames_test.csv"


def main():
    os.makedirs(LOG_DIR, exist_ok=True)

    if os.path.exists(RESULTS_CSV):
        print("이미 존재:", RESULTS_CSV, "— 재실행 전 옮길 것.")
        sys.exit(1)

    rows = []
    for seed in range(4):
        weights = f"runs/pinhole_frames_seed{seed}/weights/best.pt"
        if not os.path.exists(weights):
            print("가중치 부재:", weights)
            continue

        print("=" * 82)
        print(f"seed {seed}: {weights}")
        print("=" * 82)

        run_name = f"pinhole_frames_seed{seed}_test_local"
        # 이미 존재하면 exist_ok=True 로 덮어쓰기 방지 위해 새 이름
        if os.path.exists(f"runs/{run_name}"):
            print(f"이미 존재: runs/{run_name} — 스킵하고 결과만 다시 뽑음")

        model = YOLO(weights)

        # stdout 캡처
        log_path = os.path.join(LOG_DIR, f"eval_seed{seed}.log")
        with open(log_path, "w") as log_f:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                start = time.time()
                metrics = model.val(
                    data=DATA_YAML,
                    split="test",
                    project="runs",
                    name=run_name,
                    exist_ok=True,
                    device="cpu",
                    verbose=True,
                )
                elapsed = time.time() - start
            log_f.write(buf.getvalue())
            log_f.write(f"\n\n실행 시간: {elapsed:.2f} s\n")

        # metric 추출
        box = metrics.box
        # ultralytics 5.0 기준 (박스 검출)
        map50 = float(box.map50)
        map50_95 = float(box.map)
        p = float(box.mp)
        r = float(box.mr)
        rows.append({
            "seed": seed,
            "mAP50": round(map50, 4),
            "mAP50_95": round(map50_95, 4),
            "P": round(p, 4),
            "R": round(r, 4),
            "elapsed_s": round(elapsed, 2),
            "log": log_path,
        })
        print(f"  mAP@0.5={map50:.4f}  mAP@0.5:0.95={map50_95:.4f}  P={p:.4f}  R={r:.4f}  ({elapsed:.1f}s)")
        print()

    if not rows:
        print("결과 없음.")
        return

    import numpy as np
    print("=" * 82)
    print("§1 test 평가 결과 (N=38, R7/700/middle 전체)")
    print("=" * 82)
    print(f"{'seed':<6}{'mAP@0.5':>10}{'mAP@0.5:0.95':>15}{'P':>10}{'R':>10}")
    for row in rows:
        print(f"{row['seed']:<6}{row['mAP50']:>10.4f}{row['mAP50_95']:>15.4f}{row['P']:>10.4f}{row['R']:>10.4f}")

    ms = [r['mAP50'] for r in rows]
    m95 = [r['mAP50_95'] for r in rows]
    ps = [r['P'] for r in rows]
    rs = [r['R'] for r in rows]
    print()
    print(f"평균±std:")
    print(f"  mAP@0.5     : {np.mean(ms):.4f} ± {np.std(ms, ddof=1):.4f}  (range {min(ms):.4f} ~ {max(ms):.4f})")
    print(f"  mAP@0.5:0.95: {np.mean(m95):.4f} ± {np.std(m95, ddof=1):.4f}")
    print(f"  Precision   : {np.mean(ps):.4f} ± {np.std(ps, ddof=1):.4f}")
    print(f"  Recall      : {np.mean(rs):.4f} ± {np.std(rs, ddof=1):.4f}")

    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print()
    print("저장:", RESULTS_CSV)


main()
