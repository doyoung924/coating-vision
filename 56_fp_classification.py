"""
56. 과검 20건 통계 비교 및 분류 (§22-DET-10)

기존 데이터 재사용. 재학습·재라벨링 없음.

TP 100건 vs FP 20건 통계 비교:
  - 박스 내부 밝기 mean/max/std
  - 박스 종횡비 (max_side / min_side)
  - 박스 면적
  - 가장 가까운 마스크 성분까지 중심 거리 (channel 별: crack/delam/pinhole)
  - 박스 내부 crack/delam 마스크 픽셀 비율

과검 분류 (자동 라벨링, 사용자 시트 최종 확인):
  (a) TP 와 통계적으로 구분 불가 — 5 지표 (밝기 mean/max/std, 면적, 종횡비)
      전부 TP mean ± 2·std 범위 안 → 라벨 누락 후보
  (b) 선형·대역 구조 — 박스 내부 crack 마스크 픽셀 비율 >= 0.05 (5%) 또는
      박스 종횡비 >= 2.5 → 크랙 또는 코팅 경계 오검출 후보
  (c) 그 외

우선순위: (a) 판정 먼저, 그 다음 (b), 나머지 (c). 겹치면 (a) 우선 (통계 구분 불가가 강한 신호).
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
    cy = (y1 + y2) / 2.0; cx = (x1 + x2) / 2.0
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


def load_mask(stem):
    path = os.path.join(MASK_DIR, stem + ".png")
    if not os.path.exists(path): return None
    m = np.array(Image.open(path).convert("RGBA"))
    return {"crack": (m[:, :, 0] > 0).astype(np.uint8),
            "delam": (m[:, :, 1] > 0).astype(np.uint8),
            "pinhole": (m[:, :, 2] > 0).astype(np.uint8)}


def component_centers(binary):
    n, _, stats, cent = cv2.connectedComponentsWithStats(binary, connectivity=8)
    out = []
    for i in range(1, n):
        cy, cx = cent[i][1], cent[i][0]
        out.append((float(cy), float(cx), int(stats[i][4])))  # (cy, cx, area)
    return out


def nearest_dist(cy, cx, comps):
    if not comps: return np.inf
    d = min(np.sqrt((cy - c[0]) ** 2 + (cx - c[1]) ** 2) for c in comps)
    return d


def box_stats(image_gray, y1, x1, y2, x2):
    y1, x1 = max(0, int(y1)), max(0, int(x1))
    y2, x2 = min(IMG_H, int(y2)), min(IMG_W, int(x2))
    if y2 <= y1 or x2 <= x1: return {"mean": 0, "max": 0, "std": 0}
    r = image_gray[y1:y2, x1:x2]
    return {"mean": float(r.mean()), "max": float(r.max()), "std": float(r.std())}


def mask_ratio(mask_bin, y1, x1, y2, x2):
    y1, x1 = max(0, int(y1)), max(0, int(x1))
    y2, x2 = min(IMG_H, int(y2)), min(IMG_W, int(x2))
    if y2 <= y1 or x2 <= x1: return 0.0
    a = (y2 - y1) * (x2 - x1)
    return float(mask_bin[y1:y2, x1:x2].sum()) / a if a else 0.0


def collect():
    """모든 val + test 프레임에서 TP·FP 항목 수집."""
    meta = load_meta()
    model = YOLO(WEIGHTS)
    tp_rows, fp_rows = [], []

    for split_name, image_dir in (("val", VAL_DIR), ("test", TEST_DIR)):
        for fname in sorted(os.listdir(image_dir)):
            if not fname.endswith(".jpg"): continue
            stem = fname.replace(".jpg", "")
            image_rgb = np.array(Image.open(os.path.join(image_dir, fname)).convert("RGB"))
            gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
            masks = load_mask(stem)
            if masks is None: continue

            n_p, _, stats_p, cent_p = cv2.connectedComponentsWithStats(masks["pinhole"], connectivity=8)
            comps = []
            for i in range(1, n_p):
                x, y, w, h, area = stats_p[i]
                cy, cx = cent_p[i][1], cent_p[i][0]
                sbox = apply_scale(int(y), int(x), int(y + h), int(x + w))
                comps.append({"sbox": sbox, "area": int(area), "cy": cy, "cx": cx})

            comps_crack = component_centers(masks["crack"])
            comps_delam = component_centers(masks["delam"])
            comps_pin = component_centers(masks["pinhole"])

            res = model.predict(source=image_rgb, conf=0.25, verbose=False)
            boxes = []
            if len(res) > 0 and res[0].boxes is not None:
                for i in range(len(res[0].boxes)):
                    xyxy = res[0].boxes.xyxy[i].cpu().numpy()
                    conf = float(res[0].boxes.conf[i].cpu().numpy())
                    x1, y1, x2, y2 = xyxy
                    boxes.append({"bbox": (float(y1), float(x1), float(y2), float(x2)),
                                    "conf": conf})

            mk = meta.get(stem, {})
            seq = f"{mk.get('run','?')}/{mk.get('gap','?')}/{mk.get('position','?')}"

            for b in boxes:
                by1, bx1, by2, bx2 = b["bbox"]
                bcy = (by1 + by2) / 2; bcx = (bx1 + bx2) / 2
                bw = bx2 - bx1; bh = by2 - by1
                area = bw * bh
                aspect = max(bw, bh) / max(1, min(bw, bh))
                s = box_stats(gray, by1, bx1, by2, bx2)
                r_crack = mask_ratio(masks["crack"], by1, bx1, by2, bx2)
                r_delam = mask_ratio(masks["delam"], by1, bx1, by2, bx2)
                d_crack = nearest_dist(bcy, bcx, comps_crack)
                d_delam = nearest_dist(bcy, bcx, comps_delam)
                d_pin = nearest_dist(bcy, bcx, comps_pin)
                best_iou = 0.0
                for c in comps:
                    v = iou_box(c["sbox"], b["bbox"]);
                    if v > best_iou: best_iou = v
                rec = {"split": split_name, "stem": stem, "sequence": seq,
                       "conf": round(b["conf"], 4),
                       "box_area": round(area, 1), "aspect": round(aspect, 3),
                       "bright_mean": round(s["mean"], 2),
                       "bright_max": round(s["max"], 2),
                       "bright_std": round(s["std"], 3),
                       "in_box_crack_ratio": round(r_crack, 4),
                       "in_box_delam_ratio": round(r_delam, 4),
                       "dist_crack": round(d_crack, 2),
                       "dist_delam": round(d_delam, 2),
                       "dist_pinhole": round(d_pin, 2),
                       "best_iou": round(best_iou, 4)}
                if best_iou >= 0.5:
                    tp_rows.append(rec)
                else:
                    fp_rows.append(rec)
    return tp_rows, fp_rows


def summary_stats(rows, keys):
    out = {}
    for k in keys:
        vals = np.array([r[k] for r in rows], dtype=float)
        out[k] = {"mean": float(vals.mean()), "std": float(vals.std()),
                   "p25": float(np.percentile(vals, 25)),
                   "p50": float(np.percentile(vals, 50)),
                   "p75": float(np.percentile(vals, 75))}
    return out


def z_score_in_range(rec, tp_stats, keys, k=2.0):
    """rec 의 모든 keys 지표가 TP mean ± k·std 안에 들면 True."""
    for key in keys:
        m = tp_stats[key]["mean"]; s = tp_stats[key]["std"]
        if s == 0:
            if abs(rec[key] - m) > 0: return False
            continue
        if abs(rec[key] - m) > k * s:
            return False
    return True


def main():
    print("[집계 진행 중...]")
    tp_rows, fp_rows = collect()
    print(f"TP: {len(tp_rows)}, FP: {len(fp_rows)}")

    keys = ["bright_mean", "bright_max", "bright_std", "box_area", "aspect"]
    tp_stats = summary_stats(tp_rows, keys)
    fp_stats = summary_stats(fp_rows, keys)

    print()
    print("=" * 110)
    print("§22-DET-10 TP vs FP 지표 통계 요약")
    print("=" * 110)
    print(f"{'지표':<18}{'TP mean':>10}{'TP std':>10}{'TP p50':>10}"
          f"{'FP mean':>10}{'FP std':>10}{'FP p50':>10}")
    print("-" * 110)
    for k in keys:
        print(f"{k:<18}{tp_stats[k]['mean']:>10.3f}{tp_stats[k]['std']:>10.3f}{tp_stats[k]['p50']:>10.3f}"
              f"{fp_stats[k]['mean']:>10.3f}{fp_stats[k]['std']:>10.3f}{fp_stats[k]['p50']:>10.3f}")

    # 근접 거리 별도
    print()
    print("[FP 의 가장 가까운 마스크 성분 거리 (중심-중심 px)]")
    print(f"{'stem':<14}{'conf':>7}{'d_pinhole':>12}{'d_crack':>10}{'d_delam':>10}"
          f"{'in_box_crack%':>16}")
    for r in sorted(fp_rows, key=lambda x: -x["conf"]):
        print(f"  {r['stem']:<12}{r['conf']:>7.3f}{r['dist_pinhole']:>12.2f}"
              f"{r['dist_crack']:>10.2f}{r['dist_delam']:>10.2f}{r['in_box_crack_ratio']*100:>15.2f}%")

    # 분류
    print()
    print("=" * 110)
    print("§22-DET-11 과검 20건 분류")
    print("=" * 110)
    print("규칙:")
    print("  (a) 라벨 누락 후보: TP 5지표 (밝기 mean/max/std, 면적, 종횡비) mean ± 2·std 안")
    print("  (b) 선형·대역 후보: 박스 내 crack 픽셀 비율 >= 0.05 (5%) 또는 종횡비 >= 2.5")
    print("  (c) 그 외")
    print("  우선순위: (a) > (b) > (c)")
    print()

    groups = {"a_label_missing_candidate": [], "b_linear_or_band_candidate": [], "c_other": []}
    for r in fp_rows:
        is_a = z_score_in_range(r, tp_stats, keys, k=2.0)
        is_b = r["in_box_crack_ratio"] >= 0.05 or r["aspect"] >= 2.5
        if is_a:
            groups["a_label_missing_candidate"].append(r)
        elif is_b:
            groups["b_linear_or_band_candidate"].append(r)
        else:
            groups["c_other"].append(r)

    for gname, items in groups.items():
        confs = [it["conf"] for it in items]
        if confs:
            print(f"[{gname}] n={len(items)}  conf mean={np.mean(confs):.3f}  "
                  f"median={np.median(confs):.3f}  min={min(confs):.3f}  max={max(confs):.3f}")
        else:
            print(f"[{gname}] n=0")
        for it in sorted(items, key=lambda x: -x["conf"]):
            reasons = []
            if it["in_box_crack_ratio"] >= 0.05:
                reasons.append(f"crack{it['in_box_crack_ratio']*100:.1f}%")
            if it["aspect"] >= 2.5:
                reasons.append(f"aspect{it['aspect']:.2f}")
            reasons_str = "; ".join(reasons) if reasons else ""
            print(f"    {it['stem']:<12} conf={it['conf']:.3f} area={it['box_area']:>6.1f} "
                  f"aspect={it['aspect']:.2f} bright(mean/max/std)="
                  f"{it['bright_mean']:.1f}/{it['bright_max']:.1f}/{it['bright_std']:.2f}  "
                  f"d_pin={it['dist_pinhole']:.1f} d_crack={it['dist_crack']:.1f}  {reasons_str}")

    # 저장
    fields = list(fp_rows[0].keys()) + ["group"]
    for gname, items in groups.items():
        for it in items:
            it["group"] = gname
    all_fp = groups["a_label_missing_candidate"] + groups["b_linear_or_band_candidate"] + groups["c_other"]
    with open("results_fp_classification.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); [w.writerow(r) for r in all_fp]
    with open("results_tp_stats.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["metric"] + list(next(iter(tp_stats.values())).keys()))
        w.writeheader()
        for k in keys:
            w.writerow({"metric": k, **tp_stats[k]})
    print()
    print("저장: results_fp_classification.csv, results_tp_stats.csv")


main()
