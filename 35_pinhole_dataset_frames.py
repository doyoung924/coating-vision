"""
35. 핀홀 balanced 데이터셋 (프레임 단위 소스 위에서)

14_pinhole_dataset.py 의 로직을 그대로 재사용하되 소스/출력 경로만 변경.
원본 14 는 수정하지 않는다 (importlib 로 함수 재사용).

소스 : data/detection_auto_frames  (34 산출)
출력 : data/pinhole_frames_balanced (핀홀 단일 클래스, positive+background 균형)

원칙: 새 판단 넣지 않음. RANDOM_SEED, split 비율, 배경 선택 정책 모두 14 그대로.
"""

import importlib.util
import os
import sys


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
V14_PATH = os.path.join(PROJECT_ROOT, "14_pinhole_dataset.py")

SOURCE_ROOT = "data/detection_auto_frames"
OUTPUT_ROOT = "data/pinhole_frames_balanced"


def load_v14():
    spec = importlib.util.spec_from_file_location("v14_module", V14_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    if os.path.exists(OUTPUT_ROOT):
        print("이미 존재:", OUTPUT_ROOT, "— 재실행 전 옮길 것.")
        sys.exit(1)
    if os.path.exists(SOURCE_ROOT) is False:
        print(SOURCE_ROOT, "없음. 34 를 먼저 실행할 것.")
        sys.exit(1)

    print("=" * 82)
    print("35. 핀홀 balanced 데이터셋 (프레임 단위 소스)")
    print("=" * 82)
    print("소스 :", SOURCE_ROOT)
    print("출력 :", OUTPUT_ROOT)
    print()

    v14 = load_v14()

    # 소스 스캔 (14.scan_source)
    scanned = v14.scan_source(SOURCE_ROOT)
    v14.report_scan(scanned)

    # balanced 만 선택
    v14.report_selection(scanned, "balanced", v14.RANDOM_SEED)

    # 실제 생성 (14.build_dataset)
    v14.build_dataset(scanned, "balanced", OUTPUT_ROOT, SOURCE_ROOT, v14.RANDOM_SEED)

    print()
    print("완료.")
    print("학습 명령 (RunPod):")
    print("  yolo detect train \\")
    print("    data={}/data.yaml \\".format(os.path.abspath(OUTPUT_ROOT)))
    print("    model=yolov8n.pt \\")
    print("    epochs=100 imgsz=640 batch=16 patience=25 seed=0 \\")
    print("    project=/workspace/runs name=pinhole_frames_seed0")
    print("  # seed 1, 2, 3 도 동일 조건으로 반복")


main()
