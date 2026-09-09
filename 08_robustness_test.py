"""
08. 강건성 시험

배경:
  06/07 벤치마크에서 A3(밝기 표준편차)가 AUROC 0.976, B2(마할라노비스)가 0.984였다.
  AUROC 차이는 1%p 에 불과하지만 FPR@95TPR 은 0.117 vs 0.052 로 절반 이하다.

  그러나 이 수치는 낙관적이다.
    - 애매한 패치를 배제했다 (정상은 margin 4px 까지 깨끗한 것만, 결함은 82px 이상)
    - 조명 조건이 단일하다 (데이터의 94%가 R1 단일 런)

  실제 인라인 검사에서는 조명이 변하고, 렌즈에 먼지가 앉고, 웹 진동으로 초점이 흔들린다.

가설:
  A3(표준편차)는 "결함이 있는가"가 아니라 "텍스처가 있는가"를 재는 지표다.
  따라서 노이즈나 블러 같은 텍스처 교란에 원리적으로 취약할 것이다.
  B2(사전학습 특징)는 ImageNet 으로 다양한 조명/노이즈를 본 표현이므로
  상대적으로 강건할 것이다.

  단, 반대 결과도 가능하다. 딥러닝 특징이 학습 분포를 벗어나면 급격히 무너지는 반면
  표준편차는 단순해서 오히려 예측 가능하게 열화할 수 있다.

방법:
  test/normal 과 eval 결함 패치에 동일한 교란을 가하고 지표 변화를 측정한다.
  중요: 학습(정상 분포 모델)은 교란 없는 원본으로 하고, 추론만 교란된 입력으로 한다.
        실제 상황이 그렇다 - 모델은 정상 조건에서 만들어지고 현장 조건이 변한다.

교란 종류:
  brightness_shift : 조명 밝기 변화 (+-)
  brightness_gain  : 조명 세기 배율 (대비 동시 변화)
  gaussian_noise   : 센서 노이즈
  blur             : 초점 흐림 / 웹 진동
  jpeg_like        : 압축 손실 (블록 노이즈 근사)

사용법:
  python3 08_robustness_test.py
  python3 08_robustness_test.py --limit 3000
"""

import csv
import os
import sys
import glob
import random
import numpy as np
from PIL import Image

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


PATCH_ROOT = "data/patches"
RESULT_PATH = "results_robustness.csv"
DEFECT_CLASSES = ["surface_crack", "delamination", "pinhole"]


# ===============================================================
# 데이터
# ===============================================================

def load_patch_group(folder, limit, seed):
    paths = sorted(glob.glob(folder + "/*.png"))
    if len(paths) == 0:
        return None

    if limit is not None and len(paths) > limit:
        random_generator = random.Random(seed)
        random_generator.shuffle(paths)
        paths = sorted(paths[:limit])

    images = []
    for path in paths:
        images.append(np.array(Image.open(path).convert("RGB")))
    return np.stack(images, axis=0)


# ===============================================================
# 교란 함수
# ===============================================================

def perturb_brightness_shift(patch_array, amount):
    """조명 밝기가 일정하게 오르내리는 상황."""
    shifted = patch_array.astype(np.float64) + amount
    return np.clip(shifted, 0, 255).astype(np.uint8)


def perturb_brightness_gain(patch_array, factor):
    """
    조명 세기 자체가 변하는 상황.
    밝기와 대비가 함께 변하므로 표준편차 지표에 직접 영향을 준다.
    """
    scaled = patch_array.astype(np.float64) * factor
    return np.clip(scaled, 0, 255).astype(np.uint8)


def perturb_gaussian_noise(patch_array, sigma, seed):
    """센서 노이즈."""
    random_generator = np.random.RandomState(seed)
    noise = random_generator.normal(0, sigma, patch_array.shape)
    noisy = patch_array.astype(np.float64) + noise
    return np.clip(noisy, 0, 255).astype(np.uint8)


def perturb_blur(patch_array, sigma):
    """초점 흐림 또는 웹 진동에 의한 모션 블러 근사."""
    output = np.zeros(patch_array.shape, dtype=np.uint8)

    kernel_size = int(sigma * 4) * 2 + 1

    index = 0
    while index < patch_array.shape[0]:
        if HAS_CV2 is True:
            blurred = cv2.GaussianBlur(
                patch_array[index], (kernel_size, kernel_size), sigma
            )
        else:
            blurred = simple_box_blur(patch_array[index], int(sigma) + 1)
        output[index] = blurred
        index = index + 1

    return output


def simple_box_blur(image_array, radius):
    """cv2 가 없을 때의 대체 구현."""
    result = image_array.astype(np.float64).copy()
    accumulator = np.zeros(image_array.shape, dtype=np.float64)
    count = 0

    offset_y = -radius
    while offset_y <= radius:
        offset_x = -radius
        while offset_x <= radius:
            shifted = np.roll(np.roll(result, offset_y, axis=0), offset_x, axis=1)
            accumulator = accumulator + shifted
            count = count + 1
            offset_x = offset_x + 1
        offset_y = offset_y + 1

    averaged = accumulator / float(count)
    return np.clip(averaged, 0, 255).astype(np.uint8)


def perturb_block_artifact(patch_array, block_size, strength, seed):
    """
    압축 손실 근사. 블록 단위로 평균화하여 디테일을 죽인다.
    strength 만큼 원본과 섞는다.
    """
    random_generator = np.random.RandomState(seed)
    output = patch_array.astype(np.float64).copy()

    height = patch_array.shape[1]
    width = patch_array.shape[2]

    index = 0
    while index < patch_array.shape[0]:
        blocked = output[index].copy()
        y = 0
        while y < height:
            x = 0
            while x < width:
                y_end = min(y + block_size, height)
                x_end = min(x + block_size, width)
                block = output[index][y:y_end, x:x_end, :]
                block_mean = np.mean(block, axis=(0, 1))
                blocked[y:y_end, x:x_end, :] = block_mean
                x = x + block_size
            y = y + block_size
        output[index] = output[index] * (1.0 - strength) + blocked * strength
        index = index + 1

    return np.clip(output, 0, 255).astype(np.uint8)


# ===============================================================
# 점수 함수
# ===============================================================

def score_std_brightness(patch_array):
    gray = np.mean(patch_array.astype(np.float64), axis=3)
    return np.std(gray, axis=(1, 2))


def extract_deep_features(patch_array, batch_size, device_name):
    import torch
    import torchvision

    device = torch.device(device_name)
    weights = torchvision.models.ResNet18_Weights.IMAGENET1K_V1
    model = torchvision.models.resnet18(weights=weights)
    model.eval()
    model.to(device)

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    features_list = []
    start = 0
    while start < patch_array.shape[0]:
        end = min(start + batch_size, patch_array.shape[0])
        batch = patch_array[start:end].astype(np.float32) / 255.0
        batch = (batch - mean) / std
        batch = np.transpose(batch, (0, 3, 1, 2))
        tensor = torch.from_numpy(batch).to(device)

        with torch.no_grad():
            x = model.conv1(tensor)
            x = model.bn1(x)
            x = model.relu(x)
            x = model.maxpool(x)
            x = model.layer1(x)
            feature2 = model.layer2(x)
            feature3 = model.layer3(feature2)
            pooled2 = torch.nn.functional.adaptive_avg_pool2d(feature2, 1)
            pooled3 = torch.nn.functional.adaptive_avg_pool2d(feature3, 1)
            combined = torch.cat([pooled2.flatten(1), pooled3.flatten(1)], dim=1)

        features_list.append(combined.cpu().numpy())
        start = end

    return np.concatenate(features_list, axis=0)


def build_mahalanobis_scorer(train_features):
    mean_vector = np.mean(train_features, axis=0)
    centered = train_features - mean_vector
    covariance = np.cov(centered, rowvar=False)
    dimension = covariance.shape[0]
    covariance = covariance + np.eye(dimension) * 0.01
    inverse_covariance = np.linalg.inv(covariance)

    def scorer(query_features):
        difference = query_features - mean_vector
        left = np.dot(difference, inverse_covariance)
        distances = np.sum(left * difference, axis=1)
        return np.sqrt(np.maximum(distances, 0.0))

    return scorer


# ===============================================================
# 지표
# ===============================================================

def compute_auroc(normal_scores, defect_scores):
    normal_array = np.asarray(normal_scores, dtype=float)
    defect_array = np.asarray(defect_scores, dtype=float)
    if len(normal_array) == 0 or len(defect_array) == 0:
        return float("nan")

    combined = np.concatenate([normal_array, defect_array])
    order = np.argsort(combined, kind="mergesort")
    ranks = np.empty(len(combined), dtype=float)
    ranks[order] = np.arange(1, len(combined) + 1)

    sorted_values = combined[order]
    start = 0
    while start < len(sorted_values):
        end = start
        while end + 1 < len(sorted_values) and sorted_values[end + 1] == sorted_values[start]:
            end = end + 1
        if end > start:
            average_rank = (start + end + 2) / 2.0
            index = start
            while index <= end:
                ranks[order[index]] = average_rank
                index = index + 1
        start = end + 1

    defect_ranks = ranks[len(normal_array):]
    rank_sum = float(np.sum(defect_ranks))
    n_defect = float(len(defect_array))
    n_normal = float(len(normal_array))
    u_statistic = rank_sum - n_defect * (n_defect + 1) / 2.0
    return u_statistic / (n_defect * n_normal)


def compute_fpr_at_tpr(normal_scores, defect_scores, target_tpr):
    normal_array = np.asarray(normal_scores, dtype=float)
    defect_array = np.asarray(defect_scores, dtype=float)
    if len(normal_array) == 0 or len(defect_array) == 0:
        return float("nan")
    threshold = float(np.percentile(defect_array, (1.0 - target_tpr) * 100.0))
    false_positives = int(np.sum(normal_array >= threshold))
    return false_positives / float(len(normal_array))


def compute_fixed_threshold_fpr(normal_scores, threshold):
    """
    운영 상황을 모사한다.
    임계값은 교란 없는 조건에서 정해지고, 현장 조건이 변해도 그대로 쓰인다.
    이 임계값에서 오탐률이 얼마나 폭증하는지가 실무적 강건성이다.
    """
    normal_array = np.asarray(normal_scores, dtype=float)
    false_positives = int(np.sum(normal_array >= threshold))
    return false_positives / float(len(normal_array))


# ===============================================================
# 실행
# ===============================================================

def build_perturbation_list():
    """(이름, 함수) 목록. 함수는 patch_array 를 받아 교란된 배열을 반환한다."""
    perturbations = []

    perturbations.append(("none", lambda a: a))

    perturbations.append(("bright_shift_+10", lambda a: perturb_brightness_shift(a, 10)))
    perturbations.append(("bright_shift_+25", lambda a: perturb_brightness_shift(a, 25)))
    perturbations.append(("bright_shift_-15", lambda a: perturb_brightness_shift(a, -15)))

    perturbations.append(("bright_gain_1.10", lambda a: perturb_brightness_gain(a, 1.10)))
    perturbations.append(("bright_gain_1.25", lambda a: perturb_brightness_gain(a, 1.25)))
    perturbations.append(("bright_gain_0.85", lambda a: perturb_brightness_gain(a, 0.85)))

    perturbations.append(("noise_sigma_3", lambda a: perturb_gaussian_noise(a, 3.0, 1)))
    perturbations.append(("noise_sigma_8", lambda a: perturb_gaussian_noise(a, 8.0, 2)))
    perturbations.append(("noise_sigma_15", lambda a: perturb_gaussian_noise(a, 15.0, 3)))

    perturbations.append(("blur_sigma_0.8", lambda a: perturb_blur(a, 0.8)))
    perturbations.append(("blur_sigma_1.5", lambda a: perturb_blur(a, 1.5)))

    perturbations.append(("block_4px", lambda a: perturb_block_artifact(a, 4, 0.7, 4)))

    return perturbations


def main():
    limit = None
    if "--limit" in sys.argv:
        position = sys.argv.index("--limit")
        if position + 1 < len(sys.argv):
            limit = int(sys.argv[position + 1])

    if os.path.exists(PATCH_ROOT) is False:
        print("패치 폴더를 찾을 수 없습니다:", PATCH_ROOT)
        return

    train_limit = 5000
    if limit is not None:
        train_limit = limit

    print("=" * 84)
    print("강건성 시험")
    print("=" * 84)
    print()
    print("설계: 정상 분포 모델은 교란 없는 원본으로 만들고, 추론만 교란된 입력으로 한다.")
    print("      실제 운영이 그렇다 - 모델은 정상 조건에서 만들어지고 현장 조건이 변한다.")
    print()

    print("데이터 로딩...")
    train_normal = load_patch_group(PATCH_ROOT + "/train/normal", train_limit, 42)
    test_normal = load_patch_group(PATCH_ROOT + "/test/normal", None, 42)

    defect_arrays = {}
    for class_name in DEFECT_CLASSES:
        array = load_patch_group(PATCH_ROOT + "/eval/" + class_name, None, 42)
        if array is not None:
            defect_arrays[class_name] = array

    print("  train normal:", train_normal.shape[0])
    print("  test normal :", test_normal.shape[0])
    for class_name in defect_arrays:
        print("  eval " + class_name + ":", defect_arrays[class_name].shape[0])
    print()

    # 딥러닝 사용 가능 여부
    use_deep = True
    try:
        import torch
    except ImportError:
        use_deep = False
        print("torch 없음 - A3 만 시험합니다.")
        print()

    maha_scorer = None
    device_name = "cpu"
    if use_deep is True:
        import torch
        if torch.cuda.is_available() is True:
            device_name = "cuda"
        print("정상 분포 모델 구축 (교란 없는 원본)...")
        train_features = extract_deep_features(train_normal, 64, device_name)
        maha_scorer = build_mahalanobis_scorer(train_features)
        print("  완료:", train_features.shape)
        print()

    # 교란 없는 조건에서 운영 임계값을 정한다
    # 정상 패치의 95 퍼센타일을 임계값으로 삼는다 (오탐률 5% 목표)
    baseline_std_scores = score_std_brightness(test_normal)
    std_threshold = float(np.percentile(baseline_std_scores, 95))

    maha_threshold = None
    if use_deep is True:
        baseline_maha_features = extract_deep_features(test_normal, 64, device_name)
        baseline_maha_scores = maha_scorer(baseline_maha_features)
        maha_threshold = float(np.percentile(baseline_maha_scores, 95))

    print("운영 임계값 (교란 없는 조건에서 오탐률 5% 목표):")
    print("  A3 표준편차 :", round(std_threshold, 4))
    if maha_threshold is not None:
        print("  B2 마할라노비스:", round(maha_threshold, 4))
    print()

    perturbations = build_perturbation_list()
    result_rows = []

    print("=" * 84)
    print("결과")
    print("=" * 84)
    print()

    header = "perturbation".ljust(20)
    header = header + "A3_AUROC".ljust(11) + "A3_FPR@95".ljust(12) + "A3_fixFPR".ljust(12)
    if use_deep is True:
        header = header + "B2_AUROC".ljust(11) + "B2_FPR@95".ljust(12) + "B2_fixFPR"
    print(header)
    print("-" * 84)

    for perturbation_name, perturbation_function in perturbations:
        perturbed_normal = perturbation_function(test_normal)

        perturbed_defects = []
        for class_name in DEFECT_CLASSES:
            if class_name not in defect_arrays:
                continue
            perturbed_defects.append(perturbation_function(defect_arrays[class_name]))

        all_defect = np.concatenate(perturbed_defects, axis=0)

        # A3
        normal_scores = score_std_brightness(perturbed_normal)
        defect_scores = score_std_brightness(all_defect)
        a3_auroc = compute_auroc(normal_scores, defect_scores)
        a3_fpr95 = compute_fpr_at_tpr(normal_scores, defect_scores, 0.95)
        a3_fixed = compute_fixed_threshold_fpr(normal_scores, std_threshold)

        line = perturbation_name.ljust(20)
        line = line + str(round(a3_auroc, 4)).ljust(11)
        line = line + str(round(a3_fpr95, 4)).ljust(12)
        line = line + str(round(a3_fixed, 4)).ljust(12)

        row = {}
        row["perturbation"] = perturbation_name
        row["a3_auroc"] = round(a3_auroc, 4)
        row["a3_fpr_at_95tpr"] = round(a3_fpr95, 4)
        row["a3_fixed_threshold_fpr"] = round(a3_fixed, 4)

        # B2
        if use_deep is True:
            normal_features = extract_deep_features(perturbed_normal, 64, device_name)
            defect_features = extract_deep_features(all_defect, 64, device_name)
            normal_scores_b = maha_scorer(normal_features)
            defect_scores_b = maha_scorer(defect_features)

            b2_auroc = compute_auroc(normal_scores_b, defect_scores_b)
            b2_fpr95 = compute_fpr_at_tpr(normal_scores_b, defect_scores_b, 0.95)
            b2_fixed = compute_fixed_threshold_fpr(normal_scores_b, maha_threshold)

            line = line + str(round(b2_auroc, 4)).ljust(11)
            line = line + str(round(b2_fpr95, 4)).ljust(12)
            line = line + str(round(b2_fixed, 4))

            row["b2_auroc"] = round(b2_auroc, 4)
            row["b2_fpr_at_95tpr"] = round(b2_fpr95, 4)
            row["b2_fixed_threshold_fpr"] = round(b2_fixed, 4)

        print(line)
        result_rows.append(row)

    print()
    print("지표 설명:")
    print("  AUROC     : 임계값을 다시 최적화했을 때의 판별력 (이상적 상황)")
    print("  FPR@95    : 결함 95% 검출 시 오탐률 (임계값 재조정 가정)")
    print("  fixFPR    : 원래 임계값을 그대로 쓸 때의 오탐률 <- 실무에서 실제로 일어나는 일")
    print()
    print("fixFPR 이 핵심이다. 현장에서 조명이 변했다고 임계값을 즉시 재조정할 수는 없다.")
    print()

    write_results(result_rows, use_deep)


def write_results(result_rows, use_deep):
    if len(result_rows) == 0:
        return

    field_names = ["perturbation", "a3_auroc", "a3_fpr_at_95tpr", "a3_fixed_threshold_fpr"]
    if use_deep is True:
        field_names.append("b2_auroc")
        field_names.append("b2_fpr_at_95tpr")
        field_names.append("b2_fixed_threshold_fpr")

    result_file = open(RESULT_PATH, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(result_file, fieldnames=field_names)
    writer.writeheader()
    for row in result_rows:
        writer.writerow(row)
    result_file.close()

    print("결과 저장:", RESULT_PATH)


main()
