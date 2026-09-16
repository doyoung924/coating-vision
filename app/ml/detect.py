"""
YOLO 핀홀 검출 (§2단계 (b)).

legacy app.py:492-573 를 그대로 옮겼다. 계산 로직 미변경.
차이: 전역 yolo_model 참조를 loader.get_yolo() 호출로 교체 (사용자 지시 2).
"""

import time

import numpy as np

from . import loader


def run_detect_stage(image_np, anno_path, image_pil_copy):
    """YOLO 검출 + 오버레이 저장.

    시간 계측:
    - infer_ms: ultralytics results.speed['inference'] (순수 모델 forward)
    - pre_post_ms: 모델 호출의 preprocess + postprocess + 우리쪽 파싱/그리기
    분리해서 반환한다. 발표에서 "0.11 ms/cell" 같은 A3 순수 STD 수치와
    구분되도록 표기하기 위함이다 (A3 는 patch 당 70 cell 이므로 patch 당 약 8 ms).
    """
    from PIL import ImageDraw

    yolo_model = loader.get_yolo()

    predict_start = time.time()
    predictions = yolo_model.predict(source=image_np, conf=0.25, verbose=False)
    predict_end = time.time()
    first = predictions[0]

    detections = []
    if first.boxes is not None:
        boxes_xyxy = first.boxes.xyxy.cpu().numpy()
        confidences = first.boxes.conf.cpu().numpy()
        for i in range(len(boxes_xyxy)):
            x1 = int(round(float(boxes_xyxy[i][0])))
            y1 = int(round(float(boxes_xyxy[i][1])))
            x2 = int(round(float(boxes_xyxy[i][2])))
            y2 = int(round(float(boxes_xyxy[i][3])))
            width = max(1, x2 - x1)
            height = max(1, y2 - y1)
            aspect = max(width, height) / max(1, min(width, height))
            detection_record = {
                "index": i + 1,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "w": width,
                "h": height,
                "conf": round(float(confidences[i]), 3),
                "aspect": round(float(aspect), 3),
            }
            detections.append(detection_record)

    draw = ImageDraw.Draw(image_pil_copy)
    for detection in detections:
        draw.rectangle(
            [(detection["x1"], detection["y1"]), (detection["x2"], detection["y2"])],
            outline=(220, 40, 40),
            width=2,
        )
        label_text = "#{} p={:.2f}".format(detection["index"], detection["conf"])
        draw.text(
            (detection["x1"] + 3, detection["y1"] + 3),
            label_text,
            fill=(220, 40, 40),
        )
    image_pil_copy.save(str(anno_path))
    stage_end = time.time()

    predict_bundle_ms = (predict_end - predict_start) * 1000.0
    draw_bundle_ms = (stage_end - predict_end) * 1000.0

    speed_dict = getattr(first, "speed", None) or {}
    infer_ms = float(speed_dict.get("inference", 0.0))
    # ultralytics 가 보고한 preprocess/postprocess 는 predict_bundle 에 포함되어 있다.
    # 나머지 (predict_bundle - infer) + draw_bundle 을 '전후처리' 로 묶는다.
    pre_post_ms = (predict_bundle_ms - infer_ms) + draw_bundle_ms

    return {
        "detections": detections,
        "infer_ms": infer_ms,
        "pre_post_ms": pre_post_ms,
        "total_ms": predict_bundle_ms + draw_bundle_ms,
    }


def detect_stage_for_client(stage):
    return {
        "n_detections": len(stage["detections"]),
        "detections": stage["detections"],
        "infer_ms": round(stage["infer_ms"], 2),
        "pre_post_ms": round(stage["pre_post_ms"], 2),
        "total_ms": round(stage["total_ms"], 2),
    }
