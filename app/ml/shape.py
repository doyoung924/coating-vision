"""
검출 박스 형태 분석 (§2단계 (b)).

legacy app.py:656-761 을 그대로 옮겼다. 계산 로직 미변경.
차이: defect_map[...][pinhole] 하드코딩을 store.get_pinhole_class() 로 통일 (사용자 지시 3).
"""

import time

import numpy as np

from .. import store


def run_shape_stage(detections, image_np):
    start = time.time()
    if len(detections) == 0:
        elapsed_ms = (time.time() - start) * 1000.0
        return {
            "per_detection": [],
            "median_aspect": None,
            "median_roundness": None,
            "elapsed_ms": round(elapsed_ms, 2),
            "reference": collect_shape_reference(),
        }

    per_detection = []
    aspects = []
    roundnesses = []
    for detection in detections:
        crop = image_np[detection["y1"]:detection["y2"], detection["x1"]:detection["x2"]]
        roundness = compute_roundness(crop)
        per_entry = {
            "index": detection["index"],
            "aspect": detection["aspect"],
            "roundness": roundness,
        }
        per_detection.append(per_entry)
        aspects.append(detection["aspect"])
        if roundness is not None:
            roundnesses.append(roundness)

    median_aspect = median_value(aspects)
    median_roundness = None
    if len(roundnesses) > 0:
        median_roundness = median_value(roundnesses)

    elapsed_ms = (time.time() - start) * 1000.0
    if median_roundness is None:
        median_roundness_out = None
    else:
        median_roundness_out = round(median_roundness, 3)
    return {
        "per_detection": per_detection,
        "median_aspect": round(median_aspect, 3),
        "median_roundness": median_roundness_out,
        "elapsed_ms": round(elapsed_ms, 2),
        "reference": collect_shape_reference(),
    }


def collect_shape_reference():
    pinhole = store.get_pinhole_class()
    rule = pinhole.get("diagnostic_rule", {})
    rules_summary = []
    for entry in rule.get("rules", []):
        rules_summary.append({
            "condition": entry.get("condition", ""),
            "inference": entry.get("inference", ""),
            "confidence": entry.get("confidence", "medium"),
        })
    return {
        "description": rule.get("description", ""),
        "threshold_caveat": rule.get("threshold_caveat", ""),
        "rules": rules_summary,
    }


def compute_roundness(crop_rgb):
    import cv2
    if crop_rgb is None:
        return None
    if crop_rgb.size == 0:
        return None
    if crop_rgb.ndim == 3:
        gray = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY)
    else:
        gray = crop_rgb
    threshold_value = 180
    _, binary = cv2.threshold(gray, threshold_value, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if len(contours) == 0:
        return None
    largest = None
    largest_area = 0.0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area > largest_area:
            largest_area = area
            largest = contour
    if largest is None:
        return None
    if largest_area <= 0:
        return None
    perimeter = cv2.arcLength(largest, True)
    if perimeter <= 0:
        return None
    roundness = 4.0 * np.pi * largest_area / (perimeter * perimeter)
    return round(float(roundness), 3)


def median_value(values):
    ordered = []
    for value in values:
        ordered.append(value)
    ordered.sort()
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0
