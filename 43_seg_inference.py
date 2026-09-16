"""
43. 세그멘테이션 추론 — 전 시퀀스 crack·delam 면적비 산출

배경 (§20 후속):
  best.pt (seed 0) 로 2,227 patch 를 추론해 patch 별 세그 예측 면적비를
  산출한다. 학습 아님. train 에 쓰인 patch 도 포함되므로 split 컬럼을 남긴다.

주의:
  - train patch (1,595) 는 학습에서 이미 본 데이터. 이 patch 의 예측값은
    성능이 부풀려질 수 있음. 결과 인용 시 split 로 나눠서 봐야 함
  - splits.csv 는 §41 확정 4-split. train / val / test_in / test_out
  - 추론 시간도 patch 당 ms 로 기록 (A3 10.6 / YOLO 47 과 비교용)

산출:
  - results_seg_area.csv (2,227 행)
    stem, run_id, coating_gap, position, frame_number,
    split, pred_crack_ratio, pred_delam_ratio,
    infer_ms (batch=1 기준)
"""

import argparse
import csv
import os
import sys
import time
from collections import defaultdict

import numpy as np
import torch
from PIL import Image
import segmentation_models_pytorch as smp


BEST_PT = "runs/semantic/seed0/best.pt"
IMAGE_DIR = "segmentation/images"
SPLITS_CSV = "splits.csv"
OUT_CSV = "results_seg_area.csv"

NUM_CLASSES = 3
IMG_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMG_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def load_model(device):
    model = smp.Unet(
        encoder_name="resnet34",
        encoder_weights=None,
        in_channels=3,
        classes=NUM_CLASSES,
    )
    sd = torch.load(BEST_PT, map_location=device, weights_only=True)
    model.load_state_dict(sd)
    model.eval()
    return model.to(device)


def preprocess(img_path):
    img = np.asarray(Image.open(img_path).convert("RGB"), dtype=np.float32) / 255.0
    img = (img - IMG_MEAN) / IMG_STD
    img = img.transpose(2, 0, 1)   # HWC → CHW
    return torch.from_numpy(img).float().unsqueeze(0)   # 1,3,H,W


def main():
    if os.path.exists(OUT_CSV):
        print("이미 존재:", OUT_CSV, "— 재실행 전 옮길 것.")
        sys.exit(1)
    if not os.path.exists(BEST_PT):
        print("가중치 없음:", BEST_PT)
        sys.exit(1)

    device = torch.device("cpu")   # 로컬 GPU 사용 안 함
    print("device:", device)

    # splits.csv 로 stem → split, run_id/gap/pos/frame_number
    stems = []
    stem_meta = {}
    with open(SPLITS_CSV) as f:
        for r in csv.DictReader(f):
            stems.append(r["stem"])
            stem_meta[r["stem"]] = r

    print("총 patch:", len(stems))
    print()

    model = load_model(device)

    # 워밍업 10
    print("워밍업 10 회...")
    for i in range(10):
        stem = stems[i]
        img_path = os.path.join(IMAGE_DIR, stem + ".jpg")
        x = preprocess(img_path).to(device)
        with torch.no_grad():
            _ = model(x)

    # 실측
    print("추론 시작 (2,227 patch, batch=1)")
    rows = []
    infer_ms_list = []
    processed = 0
    for stem in stems:
        img_path = os.path.join(IMAGE_DIR, stem + ".jpg")
        x = preprocess(img_path).to(device)
        t0 = time.perf_counter()
        with torch.no_grad():
            logits = model(x)
        t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000.0
        infer_ms_list.append(elapsed_ms)

        preds = logits.argmax(dim=1).cpu().numpy()[0]   # (H, W)
        total = preds.size
        n_crack = int(np.sum(preds == 1))
        n_delam = int(np.sum(preds == 2))
        pred_crack = n_crack / total
        pred_delam = n_delam / total

        m = stem_meta[stem]
        rows.append({
            "stem": stem,
            "run_id": m["run_id"],
            "coating_gap": m["coating_gap"],
            "position": m["position"],
            "frame_number": m["frame_number"],
            "split": m["split"],
            "pred_crack_ratio": round(pred_crack, 6),
            "pred_delam_ratio": round(pred_delam, 6),
            "infer_ms": round(elapsed_ms, 2),
        })

        processed += 1
        if processed % 200 == 0:
            median_so_far = float(np.median(infer_ms_list))
            print(f"  {processed}/{len(stems)}  · median {median_so_far:.1f} ms/patch")

    print()
    a = np.array(infer_ms_list)
    print(f"추론 시간 (patch 당 ms, batch=1, CPU):")
    print(f"  median={np.median(a):.2f}  mean={a.mean():.2f}  p90={np.percentile(a, 90):.2f}  std={a.std(ddof=1):.2f}")
    print(f"  (참고) A3 median 10.6 ms/patch, YOLO infer median 46.8 ms/patch (§17-5)")
    print()

    # 저장
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    print("저장:", OUT_CSV, f"({len(rows)} 행)")

    # split 별 통계
    print()
    print("split 별 pred_crack_ratio 통계:")
    by_split = defaultdict(list)
    for r in rows:
        by_split[r["split"]].append(r["pred_crack_ratio"])
    for sp in ["train", "val", "test_in", "test_out"]:
        v = np.array(by_split[sp])
        if len(v) == 0: continue
        print(f"  {sp:<10}  N={len(v)}  median={np.median(v):.5f}  mean={v.mean():.5f}  max={v.max():.5f}")


main()
