#!/bin/bash
# RunPod 학습 스크립트 — 시맨틱 세그멘테이션 (§18-8 · §41)
#
# §2 판정 기준(사전 확정, 결과에 맞춰 조정 금지):
#   crack IoU > 0.5 → 성공 · delam IoU > 0.3 → 2 클래스 유지
#   delam IoU 0.1~0.3 → crack 만 사용 · delam IoU < 0.1 → 학습 실패
#
# 사용법:
#   1. /workspace 로 업로드: semantic_dataset.tar.gz, runpod_semantic.sh
#   2. md5:  md5sum semantic_dataset.tar.gz
#           # 예상: 5b825d9f52455ff2ca27af8d51b95bf4
#   3. tar -xzf semantic_dataset.tar.gz
#      # data/semantic/{train,val,test_in,test_out} + splits.csv + 40_train_semantic.py
#   4. bash runpod_semantic.sh
#
# 학습 조건 (사전 확정):
#   smp U-Net · encoder ResNet34 (imagenet)
#   입력 480×640 · CE(w=[0.0861, 0.5297, 2.3842]) + Dice · AdamW lr=1e-4
#   epochs 100 · batch 4 · patience 25 (val crack IoU)
#   증강: hflip p=0.5 만
#
# 회수할 파일:
#   /workspace/runs/semantic/seed{0..3}/epoch_metrics.csv
#   /workspace/runs/semantic/seed{0..3}/test_results.txt
#   /workspace/runs/semantic/seed{0..3}/best.pt
#   /workspace/logs/train_seed{0..3}.log (tee 로 저장)

set -e

# ============================================================
# 의존성
# ============================================================
echo "=== 의존성 설치 ==="
pip install --quiet segmentation-models-pytorch==0.3.4 || pip install segmentation-models-pytorch
python -c "import segmentation_models_pytorch as smp; print('smp', smp.__version__)"
python -c "import torch; print('torch', torch.__version__, '| cuda', torch.cuda.is_available())"

# ============================================================
# 학습 (seed 0~3)
# ============================================================
mkdir -p /workspace/logs

for SEED in 0 1 2 3; do
    LOG="/workspace/logs/train_seed${SEED}.log"
    echo ""
    echo "=================================================="
    echo "학습 · seed=${SEED}"
    echo "출력: $LOG"
    echo "=================================================="
    python 40_train_semantic.py --seed ${SEED} --project /workspace/runs/semantic 2>&1 | tee "$LOG"
done

echo ""
echo "=================================================="
echo "완료. 회수할 파일:"
echo "  /workspace/runs/semantic/seed{0..3}/epoch_metrics.csv"
echo "  /workspace/runs/semantic/seed{0..3}/test_results.txt"
echo "  /workspace/runs/semantic/seed{0..3}/best.pt"
echo "  /workspace/logs/train_seed{0..3}.log"
echo "=================================================="
