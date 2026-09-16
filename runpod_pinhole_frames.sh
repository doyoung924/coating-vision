#!/bin/bash
# RunPod 학습 스크립트 — 핀홀 프레임 단위 분할 재학습 (§18-12)
#
# 사용법 (RunPod 컨테이너 안에서):
#   1. pinhole_frames_balanced.tar.gz 를 /workspace 에 업로드
#   2. md5sum 대조:
#        md5sum pinhole_frames_balanced.tar.gz
#        # 예상: a4204bd3d813f98ae09044fa4a609736
#   3. 압축 해제:
#        cd /workspace
#        tar -xzf pinhole_frames_balanced.tar.gz
#        # data/pinhole_frames_balanced/ 가 만들어짐
#   4. data.yaml 의 path 를 RunPod 경로로 교체 (CLAUDE.md §RunPod 주의):
#        sed -i "s|path:.*|path: /workspace/data/pinhole_frames_balanced|" \
#          /workspace/data/pinhole_frames_balanced/data.yaml
#   5. labels/*.cache 삭제 (재사용 방지):
#        rm -f /workspace/data/pinhole_frames_balanced/labels/*.cache
#   6. 이 스크립트 실행:
#        bash runpod_pinhole_frames.sh
#
# 학습 조건은 pinhole_train.log 원본 args 와 동일 (분할만 다르다):
#   YOLOv8n · epochs 100 · imgsz 640 · batch 16 · patience 25
#   나머지 하이퍼는 ultralytics 기본값 (원 log 의 값과 일치 확인)
#
# 실행 후 로컬로 회수할 것:
#   /workspace/runs/pinhole_frames_seed{0,1,2,3}/results.csv
#   /workspace/runs/pinhole_frames_seed{0,1,2,3}/weights/best.pt
#   /workspace/runs/pinhole_frames_seed{0,1,2,3}_test/results.csv
#   (val 은 학습 로그·results.csv, test 는 별도 val 실행 결과)

set -e

DATA_YAML="/workspace/data/pinhole_frames_balanced/data.yaml"
PROJECT="/workspace/runs"

if [ ! -f "$DATA_YAML" ]; then
    echo "데이터 없음: $DATA_YAML"
    exit 1
fi

for SEED in 0 1 2 3; do
    NAME="pinhole_frames_seed${SEED}"
    echo "=================================================="
    echo "학습 시작: seed=${SEED} → ${PROJECT}/${NAME}"
    echo "=================================================="

    yolo detect train \
        data="${DATA_YAML}" \
        model=yolov8n.pt \
        epochs=100 \
        imgsz=640 \
        batch=16 \
        patience=25 \
        seed=${SEED} \
        project="${PROJECT}" \
        name="${NAME}"

    echo ""
    echo "--- test 평가 (R7/700 hold-out): seed=${SEED} ---"
    yolo detect val \
        model="${PROJECT}/${NAME}/weights/best.pt" \
        data="${DATA_YAML}" \
        split=test \
        project="${PROJECT}" \
        name="${NAME}_test"
    echo ""
done

echo "=================================================="
echo "완료. 로컬로 회수할 파일:"
echo "  ${PROJECT}/pinhole_frames_seed{0,1,2,3}/results.csv"
echo "  ${PROJECT}/pinhole_frames_seed{0,1,2,3}/weights/best.pt"
echo "  ${PROJECT}/pinhole_frames_seed{0,1,2,3}_test/results.csv"
echo "=================================================="
