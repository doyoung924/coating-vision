"""
37. 기존 pinhole_v1 을 신규 R7/700 test 셋으로 평가

배경 (§2):
  기존 무작위 분할 학습(pinhole_v1)의 test 성능을 신규 R7/700 test 셋에서 확인.
  두 모델을 같은 test 셋에서 비교하기 위함.

주의:
  기존 pinhole_v1 학습은 patch 무작위 shuffle 로 R7/700 patch 를 흩뜨렸다.
  신규 R7/700 test 38 patch 중 기존 학습 train 19 · val 1 · test 2 ·
  (미포함) 16. 즉 기존 모델은 이 test 셋의 절반을 이미 봤다.
  이 값은 도메인 이전성 평가가 아니라 "부분 학습된 데이터의 재평가" 다.
  결과 해석 시 이 사실을 반드시 병기.
"""

import contextlib
import io
import os
import time
import csv

from ultralytics import YOLO


def main():
    weights = "runs/pinhole_v1/weights/best.pt"
    if not os.path.exists(weights):
        print("가중치 부재:", weights)
        return

    data_yaml = "data/pinhole_frames_balanced/data.yaml"
    if not os.path.exists(data_yaml):
        print("데이터 부재:", data_yaml)
        return

    print("=" * 82)
    print("기존 pinhole_v1 → 신규 R7/700 test (N=38) 평가")
    print("=" * 82)
    print("주의: test 38 중 기존 학습 train 19 · val 1 · test 2 · 미포함 16")
    print()

    os.makedirs("logs", exist_ok=True)
    log_path = "logs/eval_pinhole_v1_on_r7test.log"

    model = YOLO(weights)
    with open(log_path, "w") as log_f:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            start = time.time()
            metrics = model.val(
                data=data_yaml,
                split="test",
                project="runs",
                name="pinhole_v1_test_on_r7",
                exist_ok=True,
                device="cpu",
                verbose=True,
            )
            elapsed = time.time() - start
        log_f.write(buf.getvalue())
        log_f.write(f"\n실행 시간: {elapsed:.2f} s\n")

    box = metrics.box
    print(f"mAP@0.5     : {float(box.map50):.4f}")
    print(f"mAP@0.5:0.95: {float(box.map):.4f}")
    print(f"Precision   : {float(box.mp):.4f}")
    print(f"Recall      : {float(box.mr):.4f}")
    print(f"실행 시간   : {elapsed:.2f} s  (CPU)")
    print()

    # 저장
    out_csv = "results_pinhole_v1_on_r7test.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["model", "test_n", "mAP50", "mAP50_95", "P", "R",
                         "note"])
        writer.writerow(["pinhole_v1 (patch 무작위)", 38,
                         round(float(box.map50), 4),
                         round(float(box.map), 4),
                         round(float(box.mp), 4),
                         round(float(box.mr), 4),
                         "test 38 중 train 19·val 1·test 2·미포함 16 (기존 학습 기준)"])
    print("저장:", out_csv)


main()
