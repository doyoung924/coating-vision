"""
/inspect 파이프라인 조립 (§2단계 (c)).

legacy app.py:run_inspection 을 여기로 이관. 계산 로직 미변경.
url_for 호출은 여기 남는다 — Flask blueprint 컨텍스트 안에서 실행되므로
url_for('static', ...) 은 정상 동작한다.

import 방향: services → ml → store → config
"""

import time

import numpy as np
from flask import url_for
from PIL import Image

from .. import config
from ..ml import anomaly as ml_anomaly
from ..ml import detect as ml_detect
from ..ml import segment as ml_segment
from ..ml import shape as ml_shape
from ..ml import advisor as ml_advisor
from .. import store


def run_inspection(upload):
    """업로드 파일을 저장하고 5 스테이지 파이프라인 실행 → 렌더링용 dict."""
    timestamp = int(time.time() * 1000)
    orig_name = "upload_{}.png".format(timestamp)
    anno_name = "annotated_{}.png".format(timestamp)
    heat_name = "heatmap_{}.png".format(timestamp)
    seg_name = "seg_{}.png".format(timestamp)
    orig_path = config.UPLOADS_DIR / orig_name
    anno_path = config.UPLOADS_DIR / anno_name
    heat_path = config.UPLOADS_DIR / heat_name
    seg_path = config.UPLOADS_DIR / seg_name

    upload.save(str(orig_path))

    image_pil = Image.open(str(orig_path)).convert("RGB")
    image_np = np.array(image_pil)

    # 1) 이상 탐지 (A3 밝기 표준편차)
    threshold = store.get_patch_threshold()
    anomaly_stage = ml_anomaly.run_anomaly_stage(image_np, threshold)
    ml_anomaly.save_heatmap(image_pil, anomaly_stage["positions"], anomaly_stage["scores"], threshold, heat_path)

    # 2) YOLO 검출
    detect_stage = ml_detect.run_detect_stage(image_np, anno_path, image_pil.copy())

    # 2') 시맨틱 세그 (crack·delam 픽셀 오버레이) — 요청당 1 회 (§21-5)
    seg_stage = ml_segment.run_seg_stage(image_np, image_pil, seg_path)

    # 3) 형태 분석 (defect_map 규칙 적용)
    shape_stage = ml_shape.run_shape_stage(detect_stage["detections"], image_np)

    # 4) Advisor 판정 (관측 / 해석 / 참조 3층)
    advisor_stage = ml_advisor.build_advisor(anomaly_stage, detect_stage, shape_stage, threshold)

    payload = {
        "orig_url": url_for("static", filename="uploads/" + orig_name),
        "anno_url": url_for("static", filename="uploads/" + anno_name),
        "heat_url": url_for("static", filename="uploads/" + heat_name),
        "seg_url": url_for("static", filename="uploads/" + seg_name) if seg_stage["available"] else None,
        # §3-4 저장용 파일명 (uploads/ 아래 basename). template 렌더는 위 *_url 사용.
        "file_names": {
            "orig": orig_name,
            "annotated": anno_name,
            "heatmap": heat_name,
            "seg": seg_name if seg_stage["available"] else None,
        },
        "image_size": {"w": image_pil.width, "h": image_pil.height},
        "anomaly": ml_anomaly.anomaly_stage_for_client(anomaly_stage, threshold),
        "detect": ml_detect.detect_stage_for_client(detect_stage),
        "seg": ml_segment.seg_stage_for_client(seg_stage),
        "shape": shape_stage,
        "advisor": advisor_stage,
    }
    return payload
