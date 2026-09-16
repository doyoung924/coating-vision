#!/bin/bash
# RunPod 학습 스크립트 — 데이터량 대조 실험 (§3, 프레임 단위 · train ~610)
#
# 목적: §17-5 결과 val 0.978 이 (a) 분할 누수 제거 효과인지 (b) 데이터량 증가 효과인지
# 분리하기 위한 대조. 프레임 단위 유지 · train 606 · val 123 · test 38.
# 기존 pinhole_balanced (610/124/140) 와 데이터량이 근사.
#
# 사용법:
#   1. /workspace 로 업로드: pinhole_frames_small_balanced.tar.gz
#   2. md5:  md5sum pinhole_frames_small_balanced.tar.gz
#           # 예상: 5fd0b1d4f67b1892a0429fad0882f1d9
#   3. tar -xzf pinhole_frames_small_balanced.tar.gz
#   4. sed -i "s|path:.*|path: /workspace/data/pinhole_frames_small_balanced|" \
#         /workspace/data/pinhole_frames_small_balanced/data.yaml
#   5. rm -f /workspace/data/pinhole_frames_small_balanced/labels/*.cache
#   6. bash runpod_pinhole_frames_small.sh

set -e

DATA_YAML="/workspace/data/pinhole_frames_small_balanced/data.yaml"
PROJECT="/workspace/runs"

if [ ! -f "$DATA_YAML" ]; then
    echo "데이터 없음: $DATA_YAML"
    exit 1
fi

for SEED in 0 1 2 3; do
    NAME="pinhole_frames_small_seed${SEED}"
    echo "=================================================="
    echo "학습: seed=${SEED} → ${PROJECT}/${NAME}"
    echo "=================================================="

    yolo detect train \
        data="${DATA_YAML}" \
        model=yolov8n.pt \
        epochs=100 imgsz=640 batch=16 patience=25 \
        seed=${SEED} \
        project="${PROJECT}" name="${NAME}" 2>&1 | tee "${PROJECT}/${NAME}_train.log"

    echo ""
    echo "--- test 평가: seed=${SEED} ---"
    yolo detect val \
        model="${PROJECT}/${NAME}/weights/best.pt" \
        data="${DATA_YAML}" split=test \
        project="${PROJECT}" name="${NAME}_test" 2>&1 | tee "${PROJECT}/${NAME}_test.log"
    echo ""
done

echo "=================================================="
echo "완료. 회수할 파일:"
echo "  ${PROJECT}/pinhole_frames_small_seed{0,1,2,3}/results.csv"
echo "  ${PROJECT}/pinhole_frames_small_seed{0,1,2,3}/weights/best.pt"
echo "  ${PROJECT}/pinhole_frames_small_seed{0,1,2,3}_test.log   (test 값은 이 log 에)"
echo "=================================================="
