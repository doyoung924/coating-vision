"""
Advisor 판정 (§2단계 (b)).

legacy app.py:764-880 (build_advisor) 을 그대로 옮겼다. 계산 로직 미변경.
차이: defect_map[...][pinhole] 하드코딩을 store.get_pinhole_class() 로 통일 (사용자 지시 3).
"""

from .. import store


def build_advisor(anomaly_stage, detect_stage, shape_stage, threshold):
    """/inspect 결과의 3층 리포트 (관측 / 해석 / 참조)."""
    observation = {
        "anomaly_ratio": round(anomaly_stage["ratio"], 4),
        "anomaly_cells": "{}/{}".format(anomaly_stage["anomalous_cells"], anomaly_stage["total_cells"]),
        "patch_threshold": threshold,
        "n_detections": len(detect_stage["detections"]),
        "median_aspect": shape_stage.get("median_aspect"),
        "median_roundness": shape_stage.get("median_roundness"),
    }

    interpretations = []

    # 이상 탐지 프레임 판정
    ratio = anomaly_stage["ratio"]
    if ratio >= 0.5:
        interpretations.append({
            "confidence": "high",
            "message": "이상 패치 비율 {:.1%} → 프레임 다수가 결함성. 즉시 라인 정지 검토.".format(ratio),
        })
    elif ratio >= 0.2:
        interpretations.append({
            "confidence": "medium",
            "message": "이상 패치 비율 {:.1%} → 프레임 일부에 결함 신호. 관리도 임계 확인 필요.".format(ratio),
        })
    else:
        interpretations.append({
            "confidence": "medium",
            "message": "이상 패치 비율 {:.1%} → 특이 신호 없음.".format(ratio),
        })

    # 검출 결과 판정
    n_detections = len(detect_stage["detections"])
    if n_detections == 0:
        interpretations.append({
            "confidence": "reference",
            "message": "핀홀 검출 없음.",
        })
    else:
        interpretations.append({
            "confidence": "medium",
            "message": "핀홀 {}개 검출 (YOLO conf ≥ 0.25).".format(n_detections),
        })

    # 형태 판정
    median_aspect = shape_stage.get("median_aspect")
    median_roundness = shape_stage.get("median_roundness")
    if median_aspect is not None:
        if median_aspect > 1.2:
            interpretations.append({
                "confidence": "medium",
                "message": "종횡비 중앙값 {:.2f} > 1.20 → 응집체 걸림 / 과소 갭 의심.".format(median_aspect),
            })
        else:
            interpretations.append({
                "confidence": "medium",
                "message": "종횡비 중앙값 {:.2f} ≤ 1.20 → 특이 형태 신호 없음.".format(median_aspect),
            })
    if median_roundness is not None:
        if median_roundness < 0.72:
            interpretations.append({
                "confidence": "medium",
                "message": "roundness 중앙값 {:.3f} < 0.72 → 불규칙 형태, 기계적 원인 의심.".format(median_roundness),
            })
        elif median_roundness > 0.78:
            interpretations.append({
                "confidence": "medium",
                "message": "roundness 중앙값 {:.3f} > 0.78 → 둥근 형태, 기포/젖음성 원인 의심.".format(median_roundness),
            })
        else:
            interpretations.append({
                "confidence": "low",
                "message": "roundness 중앙값 {:.3f} 은 0.72~0.78 사이라 특이 원인 판정 유보.".format(median_roundness),
            })

    references = []
    pinhole = store.get_pinhole_class()
    root_ops = []
    for cause in pinhole.get("root_causes", []):
        if cause.get("category") == "operational":
            factor = cause.get("factor", "")
            direction = cause.get("direction", "")
            confidence = cause.get("confidence", "medium")
            if direction:
                line = "- {} ({}) [conf={}]".format(factor, direction, confidence)
            else:
                line = "- {} [conf={}]".format(factor, confidence)
            root_ops.append(line)
    if len(root_ops) > 0:
        references.append({
            "topic": "핀홀 root_causes | operational (라인 조정 가능)",
            "lines": root_ops,
        })

    check_actions = pinhole.get("check_actions", [])
    if len(check_actions) > 0:
        action_lines = []
        for action in check_actions:
            action_lines.append("- " + action)
        references.append({
            "topic": "핀홀 check_actions",
            "lines": action_lines,
        })

    references.append({
        "topic": "임계값 caveat",
        "lines": [
            "- A3 patch_threshold = {:.3f} (CoatingVision 결함 라벨 0 프레임의 STD 95pct)".format(threshold),
            "- 형태 임계값(1.20, 0.72, 0.78) 는 R1 단일 런 도출값. 다른 설비/조성 이식 시 재교정 필요.",
        ],
    })

    return {
        "observation": observation,
        "interpretations": interpretations,
        "references": references,
    }
