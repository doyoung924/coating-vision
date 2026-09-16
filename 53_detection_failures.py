"""
53. 핀홀 검출 실패 케이스 수집 및 축별 집계 (§22-DET, 2026-09-15)

새 학습 없음. runs/pinhole_frames_seed0/weights/best.pt 로 추론만.
대상: 프레임 단위 분할 데이터셋의 val (N=150) + test (N=38 R7/700 hold-out)

정의:
  마스크 pinhole 성분 = segmentation/masks/*.png 의 ch2 (파란) 8-연결 성분 (>=1px)
  YOLO 예측 박스 = YOLOv8n conf>=0.25 (학습 default)

  매칭: IoU 계산. 각 마스크 성분에 대해 max IoU 예측 박스와 짝지음
    - IoU >= 0.5 이면 TP (성분 관점)
    - IoU < 0.5 이면 FN (미검)
  각 예측 박스에 대해 max IoU 마스크 성분과 짝지음
    - IoU < 0.5 이면 FP (과검)

기록 (results_detection_failures.csv):
  category (FN/FP), sequence, stem, split (val/test),
  comp_area, comp_diameter, box_area, iou, conf (FP만),
  cy, cx (성분 or 박스 중심), row_band (top/mid/bot), col_band (l/mid/r),
  in_bottom_32px_band (y >= 448),
  bg_brightness (주변 128px 창 평균 밝기),
  cocurring_crack (같은 프레임에 crack 성분 존재), cocurring_delam
"""

import csv
import os
from collections import defaultdict
import numpy as np
from PIL import Image
import cv2
from ultralytics import YOLO


IMG_H, IMG_W = 480, 640
BAND_Y = 448
CROP_MARGIN = 64  # 콘택트 시트용 128px 영역 = ±64
WEIGHTS = "runs/pinhole_frames_seed0/weights/best.pt"

# §13 학습 라벨 관례: pinhole 선형 2배 확대 + 최소변 12px
BOX_SCALE_PINHOLE = 2.0
MIN_BOX_SIDE = 12

VAL_DIR = "data/pinhole_frames_balanced/images/val"
TEST_DIR = "data/pinhole_frames_balanced/images/test"
MASK_DIR = "segmentation/masks"
LABELS_CSV = "classification/labels.csv"


def load_metadata():
    """image_N.jpg -> {run, gap, position, frame_number}"""
    meta = {}
    with open(LABELS_CSV) as f:
        for r in csv.DictReader(f):
            # image_N.jpg 로 인덱스
            stem = r["file_name"].replace(".png", "").replace(".jpg", "")
            # original_file_name 파싱
            orig = r["original_file_name"]
            # R{n}-{gap}um-{position}_frame_{N}_patch_{k}.png
            base = orig.replace(".png", "")
            parts = base.split("_frame_")
            head = parts[0]  # R7-700um-middle
            frame_num = int(parts[1].split("_patch_")[0])
            run = head.split("-")[0]
            gap = int(head.split("-")[1].replace("um", ""))
            position = "-".join(head.split("-")[2:])
            meta[stem] = {"run": run, "gap": gap, "position": position, "frame_number": frame_num}
    return meta


def load_mask(stem):
    """mask 4채널 로드. 반환 dict {crack, delam, pinhole} 이진 마스크."""
    path = os.path.join(MASK_DIR, stem + ".png")
    if not os.path.exists(path):
        return None
    m = np.array(Image.open(path).convert("RGBA"))
    return {
        "crack": (m[:, :, 0] > 0).astype(np.uint8),
        "delam": (m[:, :, 1] > 0).astype(np.uint8),
        "pinhole": (m[:, :, 2] > 0).astype(np.uint8),
    }


def apply_scale_pinhole(y1, x1, y2, x2):
    """§13 apply_scale 재현. 선형 2배, 최소변 12px, 이미지 경계 클리핑."""
    cy = (y1 + y2) / 2.0
    cx = (x1 + x2) / 2.0
    h = (y2 - y1) * BOX_SCALE_PINHOLE
    w = (x2 - x1) * BOX_SCALE_PINHOLE
    if h < MIN_BOX_SIDE: h = MIN_BOX_SIDE
    if w < MIN_BOX_SIDE: w = MIN_BOX_SIDE
    ny1 = max(0, cy - h / 2.0); nx1 = max(0, cx - w / 2.0)
    ny2 = min(IMG_H, cy + h / 2.0); nx2 = min(IMG_W, cx + w / 2.0)
    return ny1, nx1, ny2, nx2


def component_boxes(binary_mask):
    """8-연결 성분 -> (comp_id, area, cy, cx, y1, x1, y2, x2, box_scaled)
    tight bbox 는 comp_area 계산용, box_scaled 는 YOLO 학습 관례 매칭용 (§13 선형 2배)."""
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(binary_mask, connectivity=8)
    out = []
    for i in range(1, n):  # 0 = background
        x, y, w, h, area = stats[i]
        cy, cx = centroids[i][1], centroids[i][0]
        y1, x1, y2, x2 = int(y), int(x), int(y + h), int(x + w)
        sy1, sx1, sy2, sx2 = apply_scale_pinhole(y1, x1, y2, x2)
        out.append({
            "comp_id": i, "area": int(area), "cy": float(cy), "cx": float(cx),
            "y1": y1, "x1": x1, "y2": y2, "x2": x2,
            "sy1": sy1, "sx1": sx1, "sy2": sy2, "sx2": sx2,
            "diameter": float(2 * np.sqrt(area / np.pi)),
        })
    return out


def iou_box(a, b):
    """a=(y1,x1,y2,x2), b=(y1,x1,y2,x2) → IoU"""
    ay1, ax1, ay2, ax2 = a
    by1, bx1, by2, bx2 = b
    iy1 = max(ay1, by1); ix1 = max(ax1, bx1)
    iy2 = min(ay2, by2); ix2 = min(ax2, bx2)
    if iy2 <= iy1 or ix2 <= ix1:
        return 0.0
    inter = (iy2 - iy1) * (ix2 - ix1)
    area_a = (ay2 - ay1) * (ax2 - ax1)
    area_b = (by2 - by1) * (bx2 - bx1)
    return inter / (area_a + area_b - inter)


def bg_brightness(image_gray, cy, cx, r=64):
    y0 = max(0, int(cy) - r); y1 = min(IMG_H, int(cy) + r)
    x0 = max(0, int(cx) - r); x1 = min(IMG_W, int(cx) + r)
    return float(image_gray[y0:y1, x0:x1].mean())


def row_band(cy):
    if cy < IMG_H / 3: return "top"
    if cy < 2 * IMG_H / 3: return "mid"
    return "bot"


def col_band(cx):
    if cx < IMG_W / 3: return "l"
    if cx < 2 * IMG_W / 3: return "mid"
    return "r"


def process_split(split_name, image_dir, model, meta):
    files = sorted([f for f in os.listdir(image_dir) if f.endswith(".jpg")])
    all_records = []
    per_frame_stats = {"tp_comp": 0, "fn_comp": 0, "fp_box": 0, "n_frames": 0,
                        "n_frames_pinhole": 0, "n_comps": 0, "n_boxes": 0}
    for fname in files:
        stem = fname.replace(".jpg", "")
        image_path = os.path.join(image_dir, fname)
        image = np.array(Image.open(image_path).convert("RGB"))
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        masks = load_mask(stem)
        if masks is None:
            continue

        comps = component_boxes(masks["pinhole"])
        # YOLO 추론
        results = model.predict(source=image, conf=0.25, verbose=False)
        boxes = []
        if len(results) > 0 and results[0].boxes is not None:
            for i in range(len(results[0].boxes)):
                xyxy = results[0].boxes.xyxy[i].cpu().numpy()
                conf = float(results[0].boxes.conf[i].cpu().numpy())
                x1, y1, x2, y2 = xyxy
                boxes.append({
                    "y1": float(y1), "x1": float(x1), "y2": float(y2), "x2": float(x2),
                    "conf": conf, "area": float((y2 - y1) * (x2 - x1)),
                    "cy": float((y1 + y2) / 2), "cx": float((x1 + x2) / 2),
                })

        per_frame_stats["n_frames"] += 1
        if comps:
            per_frame_stats["n_frames_pinhole"] += 1
        per_frame_stats["n_comps"] += len(comps)
        per_frame_stats["n_boxes"] += len(boxes)

        # 매칭: 각 comp 마다 max IoU box
        comp_max_iou = []
        for c in comps:
            best = 0.0
            for b in boxes:
                v = iou_box((c["sy1"], c["sx1"], c["sy2"], c["sx2"]),
                             (b["y1"], b["x1"], b["y2"], b["x2"]))
                if v > best:
                    best = v
            comp_max_iou.append(best)
            if best >= 0.5:
                per_frame_stats["tp_comp"] += 1
            else:
                per_frame_stats["fn_comp"] += 1
                mkey = meta.get(stem, {})
                all_records.append({
                    "category": "FN",
                    "sequence": f"{mkey.get('run','?')}/{mkey.get('gap','?')}/{mkey.get('position','?')}",
                    "stem": stem,
                    "split": split_name,
                    "comp_area": c["area"],
                    "comp_diameter": round(c["diameter"], 2),
                    "box_area": "",
                    "iou": round(best, 4),
                    "conf": "",
                    "cy": round(c["cy"], 1),
                    "cx": round(c["cx"], 1),
                    "row_band": row_band(c["cy"]),
                    "col_band": col_band(c["cx"]),
                    "in_bottom_32px_band": int(c["cy"] >= BAND_Y),
                    "bg_brightness": round(bg_brightness(gray, c["cy"], c["cx"]), 2),
                    "cocurring_crack": int(masks["crack"].sum() > 0),
                    "cocurring_delam": int(masks["delam"].sum() > 0),
                })

        # 각 box 마다 max IoU comp
        for b in boxes:
            best = 0.0
            for c in comps:
                v = iou_box((c["sy1"], c["sx1"], c["sy2"], c["sx2"]),
                             (b["y1"], b["x1"], b["y2"], b["x2"]))
                if v > best:
                    best = v
            if best < 0.5:
                per_frame_stats["fp_box"] += 1
                mkey = meta.get(stem, {})
                all_records.append({
                    "category": "FP",
                    "sequence": f"{mkey.get('run','?')}/{mkey.get('gap','?')}/{mkey.get('position','?')}",
                    "stem": stem,
                    "split": split_name,
                    "comp_area": "",
                    "comp_diameter": "",
                    "box_area": round(b["area"], 1),
                    "iou": round(best, 4),
                    "conf": round(b["conf"], 4),
                    "cy": round(b["cy"], 1),
                    "cx": round(b["cx"], 1),
                    "row_band": row_band(b["cy"]),
                    "col_band": col_band(b["cx"]),
                    "in_bottom_32px_band": int(b["cy"] >= BAND_Y),
                    "bg_brightness": round(bg_brightness(gray, b["cy"], b["cx"]), 2),
                    "cocurring_crack": int(masks["crack"].sum() > 0),
                    "cocurring_delam": int(masks["delam"].sum() > 0),
                })

    return all_records, per_frame_stats


def main():
    meta = load_metadata()
    print(f"모델: {WEIGHTS}")
    model = YOLO(WEIGHTS)

    all_records = []
    stats_val, stats_test = None, None
    for split_name, image_dir in (("val", VAL_DIR), ("test", TEST_DIR)):
        print(f"\n[{split_name}] {image_dir}")
        recs, stats = process_split(split_name, image_dir, model, meta)
        all_records.extend(recs)
        print(f"  frames={stats['n_frames']}  frames_with_pinhole={stats['n_frames_pinhole']}"
              f"  comps={stats['n_comps']}  boxes={stats['n_boxes']}")
        print(f"  TP_comp={stats['tp_comp']}  FN_comp={stats['fn_comp']}  FP_box={stats['fp_box']}")
        if split_name == "val":
            stats_val = stats
        else:
            stats_test = stats

    # CSV 저장
    out = "results_detection_failures.csv"
    fields = ["category", "sequence", "stem", "split",
              "comp_area", "comp_diameter", "box_area", "iou", "conf",
              "cy", "cx", "row_band", "col_band", "in_bottom_32px_band",
              "bg_brightness", "cocurring_crack", "cocurring_delam"]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_records:
            w.writerow(r)
    print(f"\n저장: {out}  총 {len(all_records)}건")

    return stats_val, stats_test


if __name__ == "__main__":
    main()
