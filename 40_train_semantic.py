"""
40. 시맨틱 세그멘테이션 학습 (§18-8 방침 · §19-hd 추가)

역할 구분 (§2 확정):
  - test_in  (118 patch, R1 프레임 단위 홀드아웃): 같은 분포 성능.
    **주 지표: crack IoU.** delam 은 3 patch 로 적으나 문제 아님
  - test_out (132 patch, R7/700/middle): 도메인 이전성 대리.
    **delam 평가는 여기서 (delam 픽셀 5.12%).**
  - val (382 patch): epoch 조기 종료 기준 (val crack IoU)

모델·손실·증강:
  - segmentation_models_pytorch · U-Net · encoder ResNet34 (imagenet 사전학습)
  - 입력 480×640 그대로 (리사이즈·crop 금지, §18-11)
  - 손실 = CrossEntropy(weight=[0.0861, 0.5297, 2.3842]) + DiceLoss (1:1)
    (train 픽셀 빈도 sqrt 완화 + mean=1 정규화, §41 재계산)
  - 증강: 좌우 반전만 (p=0.5). 회전·스케일·색상 변형 금지
    (결함 형태와 밝기가 판단 근거)

하이퍼:
  - AdamW lr=1e-4, epochs=100, batch=4, patience=25 (val crack IoU 기준)
  - seed 0, 1, 2, 3 반복

평가:
  - 클래스별 IoU · Dice (평균 X, 각각 산출)
  - epoch 별 클래스별 IoU 를 CSV 로 저장
  - test 평가 결과를 stdout 과 파일에 tee

판정 기준 (사전 확정, §2):
  - crack IoU > 0.5 → 성공. 주 산출물로 사용
  - delam IoU > 0.3 → 2 클래스 유지
  - delam IoU 0.1~0.3 → crack 만 사용, delam 은 한계로 기록
  - delam IoU < 0.1 → 학습 실패. crack 단일 클래스 후퇴

사용법 (RunPod GPU):
  python3 40_train_semantic.py --seed 0
  python3 40_train_semantic.py --seed 1
  python3 40_train_semantic.py --seed 2
  python3 40_train_semantic.py --seed 3
"""

import argparse
import csv
import os
import random
import sys
import time
from collections import defaultdict

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from PIL import Image


DATA_ROOT = "data/semantic"
SPLITS_CSV = "splits.csv"

NUM_CLASSES = 3
CLASS_NAMES = ["background", "crack", "delam"]
CLASS_WEIGHTS = [0.0861, 0.5297, 2.3842]   # §41 재계산

IMG_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMG_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


# ===============================================================
# Dataset
# ===============================================================

class SemanticDataset(Dataset):
    def __init__(self, data_root, splits_csv, split, augment=False):
        self.data_root = data_root
        self.split = split
        self.augment = augment
        self.stems = []
        with open(splits_csv) as f:
            for r in csv.DictReader(f):
                if r["split"] == split:
                    self.stems.append(r["stem"])

    def __len__(self):
        return len(self.stems)

    def __getitem__(self, idx):
        stem = self.stems[idx]
        img_path = os.path.join(self.data_root, self.split, "images", stem + ".jpg")
        mask_path = os.path.join(self.data_root, self.split, "masks", stem + ".png")

        img = np.asarray(Image.open(img_path).convert("RGB"), dtype=np.float32) / 255.0
        mask = np.asarray(Image.open(mask_path), dtype=np.int64)

        if self.augment and np.random.rand() < 0.5:
            img = np.ascontiguousarray(img[:, ::-1, :])
            mask = np.ascontiguousarray(mask[:, ::-1])

        img = (img - IMG_MEAN) / IMG_STD
        img = img.transpose(2, 0, 1)   # HWC → CHW
        return torch.from_numpy(img).float(), torch.from_numpy(mask).long()


# ===============================================================
# 평가 (aggregate pixel 단위, per-class IoU & Dice)
# ===============================================================

def evaluate_split(model, loader, device, num_classes=NUM_CLASSES):
    """
    aggregate TP/FP/FN 로 각 클래스 IoU, Dice 계산.
    per-image 평균이 아님 (희소 클래스에서 안정).
    """
    tp = torch.zeros(num_classes, dtype=torch.int64)
    fp = torch.zeros(num_classes, dtype=torch.int64)
    fn = torch.zeros(num_classes, dtype=torch.int64)

    model.eval()
    with torch.no_grad():
        for imgs, targets in loader:
            imgs = imgs.to(device)
            logits = model(imgs)
            preds = logits.argmax(dim=1).cpu()
            for c in range(num_classes):
                pred_c = (preds == c)
                target_c = (targets == c)
                tp[c] += (pred_c & target_c).sum().item()
                fp[c] += (pred_c & ~target_c).sum().item()
                fn[c] += (~pred_c & target_c).sum().item()

    ious = []
    dices = []
    for c in range(num_classes):
        t, f_p, f_n = tp[c].item(), fp[c].item(), fn[c].item()
        union = t + f_p + f_n
        pos = t + f_n
        pred_pos = t + f_p
        iou = t / union if union > 0 else float("nan")
        dice = 2 * t / (pos + pred_pos) if (pos + pred_pos) > 0 else float("nan")
        ious.append(iou)
        dices.append(dice)
    return ious, dices


# ===============================================================
# 손실 (CE + Dice)
# ===============================================================

def make_loss(device):
    import segmentation_models_pytorch as smp
    weight = torch.tensor(CLASS_WEIGHTS, dtype=torch.float32, device=device)
    ce = nn.CrossEntropyLoss(weight=weight)
    dice = smp.losses.DiceLoss(mode="multiclass", from_logits=True)

    def loss_fn(logits, targets):
        return ce(logits, targets) + dice(logits, targets)

    return loss_fn


# ===============================================================
# 메인
# ===============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--patience", type=int, default=25)
    parser.add_argument("--project", type=str, default="runs/semantic")
    args = parser.parse_args()

    seed = args.seed
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device: {device}")
    print(f"seed:   {seed}")

    run_dir = os.path.join(args.project, f"seed{seed}")
    os.makedirs(run_dir, exist_ok=True)
    epoch_csv = os.path.join(run_dir, "epoch_metrics.csv")
    result_txt = os.path.join(run_dir, "test_results.txt")
    best_pt = os.path.join(run_dir, "best.pt")

    # Datasets
    print("데이터 로드")
    train_ds = SemanticDataset(DATA_ROOT, SPLITS_CSV, "train", augment=True)
    val_ds = SemanticDataset(DATA_ROOT, SPLITS_CSV, "val", augment=False)
    test_in_ds = SemanticDataset(DATA_ROOT, SPLITS_CSV, "test_in", augment=False)
    test_out_ds = SemanticDataset(DATA_ROOT, SPLITS_CSV, "test_out", augment=False)
    print(f"  train {len(train_ds)} / val {len(val_ds)} / test_in {len(test_in_ds)} / test_out {len(test_out_ds)}")

    def make_loader(ds, shuffle):
        return DataLoader(ds, batch_size=args.batch, shuffle=shuffle,
                          num_workers=2, pin_memory=True, drop_last=False)

    train_loader = make_loader(train_ds, True)
    val_loader = make_loader(val_ds, False)
    test_in_loader = make_loader(test_in_ds, False)
    test_out_loader = make_loader(test_out_ds, False)

    # Model
    import segmentation_models_pytorch as smp
    model = smp.Unet(
        encoder_name="resnet34",
        encoder_weights="imagenet",
        in_channels=3,
        classes=NUM_CLASSES,
    ).to(device)

    loss_fn = make_loss(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)

    # Epoch CSV header
    with open(epoch_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "epoch", "train_loss",
            "val_iou_bg", "val_iou_crack", "val_iou_delam",
            "val_dice_bg", "val_dice_crack", "val_dice_delam",
            "elapsed_s",
        ])

    best_val_crack_iou = -1.0
    no_improve = 0

    print()
    print("=" * 82)
    print(f"학습 시작 · epochs={args.epochs} · patience={args.patience} (val crack IoU 기준)")
    print("=" * 82)

    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        model.train()
        train_loss_sum = 0.0
        train_count = 0
        for imgs, targets in train_loader:
            imgs = imgs.to(device)
            targets = targets.to(device)
            optimizer.zero_grad()
            logits = model(imgs)
            loss = loss_fn(logits, targets)
            loss.backward()
            optimizer.step()
            train_loss_sum += loss.item() * imgs.size(0)
            train_count += imgs.size(0)
        train_loss = train_loss_sum / max(1, train_count)

        val_ious, val_dices = evaluate_split(model, val_loader, device)
        elapsed = time.time() - t0

        with open(epoch_csv, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                epoch, round(train_loss, 6),
                round(val_ious[0], 5), round(val_ious[1], 5), round(val_ious[2], 5),
                round(val_dices[0], 5), round(val_dices[1], 5), round(val_dices[2], 5),
                round(elapsed, 2),
            ])

        val_crack = val_ious[1]
        marker = ""
        if val_crack > best_val_crack_iou:
            best_val_crack_iou = val_crack
            torch.save(model.state_dict(), best_pt)
            no_improve = 0
            marker = "  *best"
        else:
            no_improve += 1

        print(f"  epoch {epoch:3d}  loss={train_loss:.4f}  "
              f"val IoU [bg {val_ious[0]:.3f} · crack {val_ious[1]:.3f} · delam {val_ious[2]:.3f}]  "
              f"({elapsed:.1f}s){marker}")

        if no_improve >= args.patience:
            print(f"조기 종료: {args.patience} epoch 동안 val crack IoU 개선 없음 (best {best_val_crack_iou:.4f})")
            break

    # 최종 평가 (best.pt)
    print()
    print("=" * 82)
    print(f"최종 평가 (best.pt · best val crack IoU {best_val_crack_iou:.4f})")
    print("=" * 82)
    model.load_state_dict(torch.load(best_pt, map_location=device))

    lines = []
    lines.append(f"seed={seed}, best_val_crack_iou={best_val_crack_iou:.4f}")
    for split_name, loader in [
        ("val", val_loader),
        ("test_in", test_in_loader),
        ("test_out", test_out_loader),
    ]:
        ious, dices = evaluate_split(model, loader, device)
        line = (f"[{split_name}]  "
                f"IoU bg={ious[0]:.4f} crack={ious[1]:.4f} delam={ious[2]:.4f} | "
                f"Dice bg={dices[0]:.4f} crack={dices[1]:.4f} delam={dices[2]:.4f}")
        print(line)
        lines.append(line)

    with open(result_txt, "w") as rf:
        for line in lines:
            rf.write(line + "\n")
    print()
    print("저장:")
    print(f"  {epoch_csv}")
    print(f"  {result_txt}")
    print(f"  {best_pt}")


if __name__ == "__main__":
    main()
