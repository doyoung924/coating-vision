"""
55. IoU 문턱 민감도 + 성분 크기 구간별 IoU 분포 (§22-DET-9)

각 마스크 pinhole 성분에 대해 max IoU YOLO 박스를 짝짓고 IoU 값을 저장.
문턱을 0.3 ~ 0.7 로 바꿔가며 TP/FN, precision, recall 재산출.

각 YOLO 박스에 대해 max IoU 마스크 성분을 짝짓고 IoU 저장 → FP 판정용.

산출:
  - results_iou_all_matches.csv : 모든 (comp, best_box_iou) 쌍 + (box, best_comp_iou) 쌍
  - results_iou_threshold_sweep.csv : 문턱별 TP/FN/FP/precision/recall
"""

import csv
import os
import numpy as np
from PIL import Image
import cv2
from ultralytics import YOLO

IMG_H, IMG_W = 480, 640
BOX_SCALE = 2.0
MIN_BOX_SIDE = 12
WEIGHTS = "runs/pinhole_frames_seed0/weights/best.pt"
VAL_DIR = "data/pinhole_frames_balanced/images/val"
TEST_DIR = "data/pinhole_frames_balanced/images/test"
MASK_DIR = "segmentation/masks"
LABELS_CSV = "classification/labels.csv"


def load_meta():
    meta = {}
    with open(LABELS_CSV) as f:
        for r in csv.DictReader(f):
            stem = r["file_name"].replace(".jpg", "").replace(".png", "")
            base = r["original_file_name"].replace(".png", "")
            parts = base.split("_frame_")
            head = parts[0]
            fn = int(parts[1].split("_patch_")[0])
            run = head.split("-")[0]
            gap = int(head.split("-")[1].replace("um", ""))
            pos = "-".join(head.split("-")[2:])
            meta[stem] = {"run": run, "gap": gap, "position": pos, "frame_number": fn}
    return meta


def apply_scale(y1, x1, y2, x2):
    cy = (y1 + y2) / 2.0
    cx = (x1 + x2) / 2.0
    h = max((y2 - y1) * BOX_SCALE, MIN_BOX_SIDE)
    w = max((x2 - x1) * BOX_SCALE, MIN_BOX_SIDE)
    return (max(0, cy - h / 2), max(0, cx - w / 2),
            min(IMG_H, cy + h / 2), min(IMG_W, cx + w / 2))


def iou_box(a, b):
    ay1, ax1, ay2, ax2 = a; by1, bx1, by2, bx2 = b
    iy1 = max(ay1, by1); ix1 = max(ax1, bx1)
    iy2 = min(ay2, by2); ix2 = min(ax2, bx2)
    if iy2 <= iy1 or ix2 <= ix1: return 0.0
    inter = (iy2 - iy1) * (ix2 - ix1)
    aa = (ay2 - ay1) * (ax2 - ax1); bb = (by2 - by1) * (bx2 - bx1)
    return inter / (aa + bb - inter)


def load_mask_pinhole(stem):
    path = os.path.join(MASK_DIR, stem + ".png")
    if not os.path.exists(path): return None
    m = np.array(Image.open(path).convert("RGBA"))
    return (m[:, :, 2] > 0).astype(np.uint8)


def component_scaled_boxes(binary):
    n, labels, stats, cent = cv2.connectedComponentsWithStats(binary, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        cy, cx = cent[i][1], cent[i][0]
        y1, x1, y2, x2 = int(y), int(x), int(y + h), int(x + w)
        sy1, sx1, sy2, sx2 = apply_scale(y1, x1, y2, x2)
        out.append({"area": int(area), "cy": cy, "cx": cx,
                     "sbox": (sy1, sx1, sy2, sx2)})
    return out


def main():
    meta = load_meta()
    model = YOLO(WEIGHTS)

    comp_matches = []  # [{stem, split, area, best_iou, conf_of_best}]
    box_matches = []   # [{stem, split, box_area, conf, best_iou}]

    for split_name, image_dir in (("val", VAL_DIR), ("test", TEST_DIR)):
        for fname in sorted(os.listdir(image_dir)):
            if not fname.endswith(".jpg"): continue
            stem = fname.replace(".jpg", "")
            image = np.array(Image.open(os.path.join(image_dir, fname)).convert("RGB"))
            pin = load_mask_pinhole(stem)
            if pin is None: continue
            comps = component_scaled_boxes(pin)
            res = model.predict(source=image, conf=0.25, verbose=False)
            boxes = []
            if len(res) > 0 and res[0].boxes is not None:
                for i in range(len(res[0].boxes)):
                    xyxy = res[0].boxes.xyxy[i].cpu().numpy()
                    conf = float(res[0].boxes.conf[i].cpu().numpy())
                    x1, y1, x2, y2 = xyxy
                    boxes.append({"bbox": (float(y1), float(x1), float(y2), float(x2)),
                                    "conf": conf,
                                    "area": float((y2 - y1) * (x2 - x1))})

            mk = meta.get(stem, {})
            seq = f"{mk.get('run','?')}/{mk.get('gap','?')}/{mk.get('position','?')}"

            for c in comps:
                best_iou = 0.0; best_conf = None
                for b in boxes:
                    v = iou_box(c["sbox"], b["bbox"])
                    if v > best_iou:
                        best_iou = v; best_conf = b["conf"]
                comp_matches.append({
                    "split": split_name, "stem": stem, "sequence": seq,
                    "area": c["area"], "best_iou": round(best_iou, 4),
                    "conf_of_best": round(best_conf, 4) if best_conf else "",
                })
            for b in boxes:
                best_iou = 0.0
                for c in comps:
                    v = iou_box(c["sbox"], b["bbox"])
                    if v > best_iou:
                        best_iou = v
                box_matches.append({
                    "split": split_name, "stem": stem, "sequence": seq,
                    "box_area": round(b["area"], 1),
                    "conf": round(b["conf"], 4),
                    "best_iou": round(best_iou, 4),
                })

    # 저장
    with open("results_iou_all_matches_comp.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["split", "stem", "sequence", "area", "best_iou", "conf_of_best"])
        w.writeheader(); [w.writerow(r) for r in comp_matches]
    with open("results_iou_all_matches_box.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["split", "stem", "sequence", "box_area", "conf", "best_iou"])
        w.writeheader(); [w.writerow(r) for r in box_matches]

    N_comp = len(comp_matches)
    N_box = len(box_matches)
    print(f"총 마스크 성분: {N_comp} / 총 YOLO 박스: {N_box}")

    # 문턱 스윕
    thresholds = [0.3, 0.4, 0.5, 0.6, 0.7]
    print()
    print(f"{'IoU 문턱':<10}{'TP':>5}{'FN':>5}{'FP':>5}{'Recall':>10}{'Precision':>12}{'F1':>8}")
    print("-" * 60)
    sweep_rows = []
    for thr in thresholds:
        TP = sum(1 for r in comp_matches if r["best_iou"] >= thr)
        FN = N_comp - TP
        # FP: 예측 박스 중 정답 성분에 매칭되지 않는 것
        FP = sum(1 for r in box_matches if r["best_iou"] < thr)
        recall = TP / N_comp if N_comp else 0
        prec = TP / (TP + FP) if (TP + FP) else 0
        f1 = 2 * recall * prec / (recall + prec) if (recall + prec) else 0
        print(f"{thr:<10.2f}{TP:>5}{FN:>5}{FP:>5}{recall:>10.3f}{prec:>12.3f}{f1:>8.3f}")
        sweep_rows.append({"iou_threshold": thr, "TP": TP, "FN": FN, "FP": FP,
                            "recall": round(recall, 4), "precision": round(prec, 4),
                            "F1": round(f1, 4)})

    with open("results_iou_threshold_sweep.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(sweep_rows[0].keys()))
        w.writeheader(); [w.writerow(r) for r in sweep_rows]

    # 성분 크기 구간별 IoU 분포
    print()
    print("[성분 크기 구간별 best_iou 분포]")
    bins = [(1, 10), (11, 30), (31, 100), (101, 500), (501, 5000)]
    print(f"{'구간':<15}{'n':>5}{'iou p25':>10}{'p50':>8}{'p75':>8}{'max':>8}{'iou<0.5':>10}")
    for lo, hi in bins:
        sub = [r for r in comp_matches if lo <= r["area"] <= hi]
        if not sub:
            print(f"  area {lo:>3}~{hi:>4}: n=0")
            continue
        ious = sorted(r["best_iou"] for r in sub)
        p25 = ious[len(ious) // 4]
        p50 = ious[len(ious) // 2]
        p75 = ious[3 * len(ious) // 4]
        m = max(ious)
        under = sum(1 for v in ious if v < 0.5)
        print(f"  area {lo:>3}~{hi:>4}: n={len(sub):>3}  {p25:>7.3f}{p50:>8.3f}{p75:>8.3f}{m:>8.3f}{under:>10}")

    # FN 근접 문턱 (0.44~0.50 언급)
    print()
    print("[FN 성분의 best_iou 값 (문턱 0.5 미만)]")
    fns = sorted([r for r in comp_matches if r["best_iou"] < 0.5],
                  key=lambda r: -r["best_iou"])
    for r in fns:
        print(f"  {r['stem']} area={r['area']:>4} best_iou={r['best_iou']:.4f}  {r['sequence']}")


main()
