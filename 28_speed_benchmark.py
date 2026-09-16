"""
28. 처리 속도 재측정 — patch (480x640) 당 단일 단위

배경 (§17):
  06 벤치마크의 "0.11 ms/patch" 는 실제로 ms/cell 이었다 (§17-1).
  §11-6 표의 "YOLOv8n 55 ms/image" 는 산출 스크립트가 확인되지 않아 폐기 후보.
  세 지표를 동일 조건·동일 단위(ms/patch)로 재측정한다.

측정 항목 (모두 patch = 480x640 이미지 당):
  a. A3 전체 — 그레이스케일 변환 + 70 cell STD 계산 (score_patches_std_gray 동등)
  b. YOLO 순수 추론 — ultralytics results.speed['inference']
  c. YOLO 전후처리 — 전체 predict wall 시간 − infer
  d. 형태 분석 — 각 검출 박스별 종횡비·roundness (app.py run_shape_stage 동등)

방법:
  - 대상: results_spc.csv 의 patch 중 무작위 200 (seed=42)
  - 워밍업 10 회 (통계에서 제외)
  - 각 항목 200 회 측정 후 median, p90, std, mean 산출
  - 실행 환경 (CPU 모델, 코어 수, torch/ultralytics 버전, 스레드 설정) 로그

원칙:
  - /inspect 실행 순서를 바꾸지 않는다 (조건 없는 순차 실행 유지)
  - 새 검출 기법 도입 없음. 기존 A3·YOLO·형태 로직만 사용
  - 계측 결과가 기존 55 ms 와 크게 다르면 그대로 보고

산출:
  - stdout: 통계 표 + 환경
  - results_speed_benchmark.csv
"""

import csv
import os
import random
import sys
import time
import platform
import numpy as np
from PIL import Image


RESULTS_CSV = "results_speed_benchmark.csv"
SPC_CSV = "results_spc.csv"
IMAGE_DIR = "segmentation/images"
YOLO_WEIGHTS = "runs/pinhole_v1/weights/best.pt"

WARMUP = 10
N_SAMPLES = 200
SEED = 42

CELL_SIZE = 64
STRIDE = 64
GRID_ROWS = 7
GRID_COLS = 10


# ===============================================================
# 유틸
# ===============================================================

def score_patches_std_gray(gray):
    """app.py:score_patches_std_gray 동등. 70 개 cell std 반환."""
    scores = []
    y = 0
    while y + CELL_SIZE <= gray.shape[0]:
        x = 0
        while x + CELL_SIZE <= gray.shape[1]:
            cell = gray[y:y + CELL_SIZE, x:x + CELL_SIZE]
            scores.append(float(np.std(cell)))
            x = x + STRIDE
        y = y + STRIDE
    return scores


def compute_roundness(crop_rgb):
    """app.py:compute_roundness 동등. cv2.threshold 180 + findContours."""
    import cv2
    if crop_rgb is None or crop_rgb.size == 0:
        return None
    if crop_rgb.ndim == 3:
        gray = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY)
    else:
        gray = crop_rgb
    threshold_value = 180
    _, binary = cv2.threshold(gray, threshold_value, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if len(contours) == 0:
        return None
    biggest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(biggest)
    perimeter = cv2.arcLength(biggest, True)
    if perimeter <= 0:
        return None
    return 4.0 * np.pi * area / (perimeter * perimeter)


# ===============================================================
# 환경
# ===============================================================

def describe_environment():
    lines = []
    lines.append("--- 실행 환경 ---")
    lines.append("platform: " + platform.platform())
    lines.append("python  : " + platform.python_version())

    cpu_model = "unknown"
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu_model = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass
    lines.append("cpu     : " + cpu_model)
    lines.append("cores   : " + str(os.cpu_count()))

    try:
        import torch
        lines.append("torch   : " + torch.__version__)
        lines.append("torch threads: " + str(torch.get_num_threads()))
    except Exception as exc:
        lines.append("torch   : import fail — " + str(exc))

    try:
        import ultralytics
        lines.append("ultralytics: " + ultralytics.__version__)
    except Exception as exc:
        lines.append("ultralytics: import fail — " + str(exc))

    try:
        import cv2
        lines.append("cv2     : " + cv2.__version__)
    except Exception as exc:
        lines.append("cv2     : import fail — " + str(exc))

    return "\n".join(lines)


# ===============================================================
# 통계
# ===============================================================

def stats(values):
    if len(values) == 0:
        return {"n": 0, "median": None, "p90": None, "std": None, "mean": None}
    a = np.array(values, dtype=float)
    return {
        "n": len(a),
        "median": float(np.median(a)),
        "p90": float(np.percentile(a, 90)),
        "std": float(np.std(a, ddof=1)) if len(a) > 1 else 0.0,
        "mean": float(a.mean()),
    }


# ===============================================================
# 메인
# ===============================================================

def main():
    if os.path.exists(RESULTS_CSV):
        print("이미 존재:", RESULTS_CSV, "— 사용자가 옮긴 뒤 재실행.")
        sys.exit(1)
    if os.path.exists(SPC_CSV) is False:
        print(SPC_CSV, "없음.")
        sys.exit(1)
    if os.path.exists(YOLO_WEIGHTS) is False:
        print("YOLO 가중치 없음:", YOLO_WEIGHTS)
        sys.exit(1)

    print("=" * 88)
    print("28. 처리 속도 재측정 — patch(480x640) 당 ms")
    print("=" * 88)
    print(describe_environment())
    print()

    # 대상 stem 수집
    stems = []
    with open(SPC_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stems.append(row["stem"])
    random.seed(SEED)
    random.shuffle(stems)
    sample_stems = stems[:N_SAMPLES + WARMUP]
    print("샘플 stem 수: {} (워밍업 {} + 측정 {})".format(len(sample_stems), WARMUP, N_SAMPLES))

    # YOLO 로드
    print("YOLO 로드 중...")
    from ultralytics import YOLO
    yolo_model = YOLO(YOLO_WEIGHTS)
    print("  로드 완료.")
    print()

    a3_values = []
    yolo_infer_values = []
    yolo_pre_post_values = []
    shape_values = []

    total = len(sample_stems)
    idx = 0
    for stem in sample_stems:
        idx = idx + 1
        image_path = os.path.join(IMAGE_DIR, stem + ".jpg")
        if os.path.exists(image_path) is False:
            print("  스킵 (파일 없음):", stem)
            continue
        image_pil = Image.open(image_path).convert("RGB")
        image_np = np.array(image_pil)

        # ---- a. A3 ----
        t0 = time.perf_counter()
        gray = np.mean(image_np.astype(np.float64), axis=2)
        scores = score_patches_std_gray(gray)
        t1 = time.perf_counter()
        a3_ms = (t1 - t0) * 1000.0

        # ---- b, c. YOLO ----
        t0 = time.perf_counter()
        predictions = yolo_model.predict(source=image_np, conf=0.25, verbose=False)
        t1 = time.perf_counter()
        first = predictions[0]
        predict_bundle_ms = (t1 - t0) * 1000.0
        speed_dict = getattr(first, "speed", None) or {}
        infer_ms = float(speed_dict.get("inference", 0.0))
        pre_post_ms = predict_bundle_ms - infer_ms

        # ---- d. 형태 분석 ----
        detections = []
        if first.boxes is not None:
            boxes_xyxy = first.boxes.xyxy.cpu().numpy()
            for i in range(len(boxes_xyxy)):
                x1 = int(round(float(boxes_xyxy[i][0])))
                y1 = int(round(float(boxes_xyxy[i][1])))
                x2 = int(round(float(boxes_xyxy[i][2])))
                y2 = int(round(float(boxes_xyxy[i][3])))
                detections.append((x1, y1, x2, y2))

        t0 = time.perf_counter()
        shape_records = []
        for d in detections:
            x1, y1, x2, y2 = d
            width = max(1, x2 - x1)
            height = max(1, y2 - y1)
            aspect = max(width, height) / max(1, min(width, height))
            crop = image_np[y1:y2, x1:x2]
            roundness = compute_roundness(crop)
            shape_records.append((aspect, roundness))
        # median 계산까지 포함
        if len(shape_records) > 0:
            aspects = [r[0] for r in shape_records]
            roundnesses = [r[1] for r in shape_records if r[1] is not None]
            _ = float(np.median(aspects))
            if len(roundnesses) > 0:
                _ = float(np.median(roundnesses))
        t1 = time.perf_counter()
        shape_ms = (t1 - t0) * 1000.0

        # 워밍업은 제외
        if idx > WARMUP:
            a3_values.append(a3_ms)
            yolo_infer_values.append(infer_ms)
            yolo_pre_post_values.append(pre_post_ms)
            shape_values.append(shape_ms)

        if idx % 50 == 0:
            print("  진행 {}/{}".format(idx, total))

    print()

    # 통계
    def print_row(name, values, unit="ms/patch"):
        s = stats(values)
        print("  {:<32} N={:>4}  median={:>8.3f}  p90={:>8.3f}  std={:>8.3f}  mean={:>8.3f}  ({})".format(
            name, s["n"], s["median"] or 0.0, s["p90"] or 0.0, s["std"] or 0.0, s["mean"] or 0.0, unit))
        return s

    print("=" * 88)
    print("결과 (patch 당, 워밍업 {} 제외, N={})".format(WARMUP, len(a3_values)))
    print("=" * 88)
    s_a3       = print_row("a. A3 전체 (gray+70 cell std)", a3_values)
    s_infer    = print_row("b. YOLO 순수 추론 (infer)",   yolo_infer_values)
    s_pre_post = print_row("c. YOLO 전후처리 (bundle-infer)", yolo_pre_post_values)
    s_shape    = print_row("d. 형태 분석 (박스별 aspect+roundness)", shape_values)

    # A3 patch 당 값에서 cell 당 값으로도 계산
    n_cells = GRID_ROWS * GRID_COLS
    print()
    print("  참고: A3 patch 값 / {} = cell 당 (§16-0 용어)".format(n_cells))
    if s_a3["median"] is not None:
        print("        A3 median ms/cell = {:.4f}".format(s_a3["median"] / n_cells))
    print()

    # CSV 저장
    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "unit", "n", "median", "p90", "std", "mean"])
        writer.writerow(["a3_full",       "ms/patch", s_a3["n"], s_a3["median"], s_a3["p90"], s_a3["std"], s_a3["mean"]])
        writer.writerow(["yolo_infer",    "ms/patch", s_infer["n"], s_infer["median"], s_infer["p90"], s_infer["std"], s_infer["mean"]])
        writer.writerow(["yolo_pre_post", "ms/patch", s_pre_post["n"], s_pre_post["median"], s_pre_post["p90"], s_pre_post["std"], s_pre_post["mean"]])
        writer.writerow(["shape_stage",   "ms/patch", s_shape["n"], s_shape["median"], s_shape["p90"], s_shape["std"], s_shape["mean"]])
        if s_a3["median"] is not None:
            writer.writerow(["a3_per_cell", "ms/cell", s_a3["n"], s_a3["median"] / n_cells,
                             s_a3["p90"] / n_cells, s_a3["std"] / n_cells, s_a3["mean"] / n_cells])
    print("저장:", RESULTS_CSV)


main()
