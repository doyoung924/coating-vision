"""
A3 밝기 표준편차 기반 이상 탐지 (§2단계 (b)).

legacy app.py:135-490 중 이상 탐지 관련 함수를 그대로 옮겼다. 계산 로직 미변경.
  - score_patches_std_gray: 64×64 셀 grayscale 표준편차 스캔
  - run_anomaly_stage: 단일 patch 에 대해 A3 실행 + 시간 계측
  - save_heatmap: 임계 초과 셀만 붉게 오버레이 PNG 저장
  - anomaly_stage_for_client: 렌더링용 최소 dict
"""

import time

import numpy as np


# legacy: score_patches_std_gray 함수 지역 상수. 사용자 지시로 모듈 상수화.
PATCH_SIZE = 64
STRIDE = 64


def score_patches_std_gray(gray):
    height = gray.shape[0]
    width = gray.shape[1]
    scores = []
    positions = []
    y = 0
    while y + PATCH_SIZE <= height:
        x = 0
        while x + PATCH_SIZE <= width:
            patch = gray[y:y + PATCH_SIZE, x:x + PATCH_SIZE]
            scores.append(float(np.std(patch)))
            positions.append((x, y, PATCH_SIZE, PATCH_SIZE))
            x = x + STRIDE
        y = y + STRIDE
    return scores, positions


def run_anomaly_stage(image_np, threshold):
    start = time.time()
    gray = np.mean(image_np.astype(np.float64), axis=2)
    scores, positions = score_patches_std_gray(gray)
    anomalous_indices = []
    for i in range(len(scores)):
        if scores[i] >= threshold:
            anomalous_indices.append(i)
    elapsed_ms = (time.time() - start) * 1000.0
    total = len(scores)
    anomalous = len(anomalous_indices)
    if total > 0:
        ratio = anomalous / float(total)
    else:
        ratio = 0.0
    # elapsed_ms 는 patch (480x640) 하나에 대한 총 처리시간. total 은 그 안의 cell 수 (=70).
    # 따라서 elapsed_ms / total 은 실제로 per_cell_ms 다. §16-0 용어 확정.
    return {
        "scores": scores,
        "positions": positions,
        "anomalous_indices": anomalous_indices,
        "total_cells": total,
        "anomalous_cells": anomalous,
        "ratio": ratio,
        "elapsed_ms_per_patch": elapsed_ms,
        "per_cell_ms": elapsed_ms / max(1, total),
    }


def anomaly_stage_for_client(stage, threshold):
    return {
        "total_cells": stage["total_cells"],
        "anomalous_cells": stage["anomalous_cells"],
        "ratio": stage["ratio"],
        "threshold": threshold,
        "elapsed_ms_per_patch": round(stage["elapsed_ms_per_patch"], 2),
        "per_cell_ms": round(stage["per_cell_ms"], 3),
    }


def save_heatmap(image_pil, positions, scores, threshold, out_path):
    from PIL import Image, ImageDraw
    overlay = Image.new("RGBA", image_pil.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for i in range(len(positions)):
        if scores[i] < threshold:
            continue
        x, y, w, h = positions[i]
        draw.rectangle(
            [(x, y), (x + w, y + h)],
            fill=(220, 40, 40, 90),
            outline=(220, 40, 40, 200),
            width=1,
        )
    combined = Image.alpha_composite(image_pil.convert("RGBA"), overlay)
    combined.convert("RGB").save(str(out_path))
