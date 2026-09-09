"""
06. 이상 탐지 벤치마크

목적:
  정상 패치만 학습하고 결함을 얼마나 잡는지 측정한다.
  저자 baseline(지도학습)과 비교 가능한 형태로 결과를 낸다.

핵심 설계 - 왜 사소한 베이스라인을 먼저 재는가:
  05 분석에서 클래스별 밝기 특성이 극단적으로 달랐다.
    normal      최대밝기 144.5
    delamination 최대밝기 240.5
    pinhole      최대밝기 246.0
    surface_crack 최대밝기 145.5  <- 정상과 동일
  박리/핀홀은 하부 PTFE 기재가 노출되어 밝아지므로 밝기 임계값만으로도
  상당 부분 검출될 가능성이 높다. 딥러닝이 이 바닥을 얼마나 넘는지 모르면
  성능 수치에 의미를 부여할 수 없다.

평가 방법:
  Part A (numpy만 필요) - 밝기 통계 3종
    mean_brightness, max_brightness, std_brightness
  Part B (torch 필요)   - 사전학습 특징 기반 3종
    kNN, PaDiM(마할라노비스), PatchCore(coreset 메모리뱅크)

평가 지표:
  - 클래스별 AUROC (normal vs 각 결함 클래스)
  - 통합 AUROC: crack + delamination 를 "건조 결함"으로 묶음
    05에서 두 라벨이 물리적으로 연속임을 확인했으므로,
    통합 시 성능이 오르면 라벨 모호성의 정량적 증거가 된다
  - FPR@95TPR : 결함의 95%를 잡을 때 정상을 얼마나 오탐하는지
  - 갭별 오탐률 : 특정 공정 조건에서만 오탐이 몰리는지
  - 추론 속도 : 인라인 검사 적용 가능성

사용법:
  python3 06_benchmark_anomaly.py              # Part A만
  python3 06_benchmark_anomaly.py --deep       # Part A + B
  python3 06_benchmark_anomaly.py --deep --limit 3000   # 학습 패치 수 제한
"""

import csv
import os
import sys
import glob
import time
import random
import numpy as np
from PIL import Image


PATCH_ROOT = "data/patches"
MANIFEST_PATH = "data/patches/manifest.csv"
RESULT_PATH = "results_benchmark.csv"

DEFECT_CLASSES = ["surface_crack", "delamination", "pinhole"]
COMBINED_NAME = "drying_defect"          # crack + delamination
COMBINED_MEMBERS = ["surface_crack", "delamination"]


# ===============================================================
# 데이터 로딩
# ===============================================================

def load_manifest(manifest_path):
    """patch_file -> 메타데이터. 갭별 분석에 필요하다."""
    lookup = {}
    if os.path.exists(manifest_path) is False:
        return lookup

    manifest_file = open(manifest_path, "r", encoding="utf-8")
    reader = csv.DictReader(manifest_file)
    for row in reader:
        key = row["split"] + "/" + row["class_name"] + "/" + row["patch_file"]
        record = {}
        record["coating_gap"] = int(row["coating_gap"])
        record["source_image"] = row["source_image"]
        lookup[key] = record
    manifest_file.close()
    return lookup


def load_patch_group(folder, limit, seed):
    """
    폴더의 패치를 (배열, 파일명 리스트)로 읽는다.
    배열 shape: (N, 64, 64, 3) uint8
    """
    paths = sorted(glob.glob(folder + "/*.png"))
    if len(paths) == 0:
        return None, []

    if limit is not None and len(paths) > limit:
        random_generator = random.Random(seed)
        random_generator.shuffle(paths)
        paths = paths[:limit]
        paths = sorted(paths)

    images = []
    names = []
    for path in paths:
        image = Image.open(path).convert("RGB")
        images.append(np.array(image))
        names.append(os.path.basename(path))

    stacked = np.stack(images, axis=0)
    return stacked, names


# ===============================================================
# 평가 지표
# ===============================================================

def compute_auroc(normal_scores, defect_scores):
    """
    AUROC 를 순위 기반으로 계산한다 (Mann-Whitney U).
    점수가 높을수록 이상이라고 가정한다.
    """
    normal_array = np.asarray(normal_scores, dtype=float)
    defect_array = np.asarray(defect_scores, dtype=float)

    if len(normal_array) == 0 or len(defect_array) == 0:
        return float("nan")

    combined = np.concatenate([normal_array, defect_array])
    order = np.argsort(combined, kind="mergesort")
    ranks = np.empty(len(combined), dtype=float)
    ranks[order] = np.arange(1, len(combined) + 1)

    # 동점 처리: 같은 값끼리 평균 순위를 준다
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
    auroc = u_statistic / (n_defect * n_normal)
    return auroc


def compute_fpr_at_tpr(normal_scores, defect_scores, target_tpr):
    """
    결함의 target_tpr 비율을 잡는 임계값에서의 정상 오탐률.
    실무에서 "결함을 95% 잡으려면 정상을 몇 % 버려야 하나"에 해당한다.
    """
    normal_array = np.asarray(normal_scores, dtype=float)
    defect_array = np.asarray(defect_scores, dtype=float)

    if len(normal_array) == 0 or len(defect_array) == 0:
        return float("nan"), float("nan")

    # 결함 점수의 하위 (1-target_tpr) 분위수를 임계값으로 삼는다
    threshold = float(np.percentile(defect_array, (1.0 - target_tpr) * 100.0))
    false_positives = int(np.sum(normal_array >= threshold))
    fpr = false_positives / float(len(normal_array))
    return fpr, threshold


def compute_gap_fpr(normal_scores, normal_gaps, threshold):
    """갭 조건별 오탐률."""
    result = {}
    gap_values = sorted(set(normal_gaps))
    for gap in gap_values:
        selected = []
        index = 0
        while index < len(normal_scores):
            if normal_gaps[index] == gap:
                selected.append(normal_scores[index])
            index = index + 1
        if len(selected) == 0:
            continue
        selected_array = np.asarray(selected, dtype=float)
        false_positives = int(np.sum(selected_array >= threshold))
        result[gap] = false_positives / float(len(selected_array))
    return result


# ===============================================================
# Part A: 밝기 통계 베이스라인
# ===============================================================

def score_mean_brightness(patch_array):
    gray = np.mean(patch_array.astype(np.float64), axis=3)
    return np.mean(gray, axis=(1, 2))


def score_max_brightness(patch_array):
    gray = np.mean(patch_array.astype(np.float64), axis=3)
    return np.max(gray, axis=(1, 2))


def score_std_brightness(patch_array):
    gray = np.mean(patch_array.astype(np.float64), axis=3)
    return np.std(gray, axis=(1, 2))


def score_deviation_from_normal(patch_array, normal_mean):
    """
    정상 평균 밝기로부터의 절대 편차.
    크랙은 어둡고 박리는 밝으므로, 단순 평균보다 양방향을 포착한다.
    """
    gray = np.mean(patch_array.astype(np.float64), axis=3)
    patch_means = np.mean(gray, axis=(1, 2))
    return np.abs(patch_means - normal_mean)


# ===============================================================
# Part B: 사전학습 특징 기반
# ===============================================================

def extract_deep_features(patch_array, batch_size, device_name):
    """
    사전학습 ResNet18 의 중간층 특징을 뽑는다.
    학습이 아니라 순전파만 하므로 저사양 GPU 나 CPU 에서도 동작한다.

    layer2, layer3 를 쓰는 이유:
      layer1 은 너무 저수준(에지), layer4 는 너무 고수준(의미)이다.
      텍스처 이상 탐지에는 중간층이 적합하다는 것이 PaDiM/PatchCore 의 공통 관찰이다.
    """
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

    total = patch_array.shape[0]
    start = 0
    while start < total:
        end = min(start + batch_size, total)
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


def build_knn_scorer(train_features, num_neighbors):
    """가장 가까운 k개 정상 특징까지의 평균 거리."""
    def scorer(query_features):
        scores = []
        index = 0
        while index < query_features.shape[0]:
            query = query_features[index]
            differences = train_features - query
            distances = np.sqrt(np.sum(differences * differences, axis=1))
            nearest = np.sort(distances)[:num_neighbors]
            scores.append(float(np.mean(nearest)))
            index = index + 1
        return np.array(scores)
    return scorer


def build_mahalanobis_scorer(train_features):
    """
    PaDiM 의 핵심 아이디어를 단순화한 것.
    정상 특징을 다변량 가우시안으로 모델링하고 마할라노비스 거리를 이상 점수로 쓴다.
    """
    mean_vector = np.mean(train_features, axis=0)
    centered = train_features - mean_vector
    covariance = np.cov(centered, rowvar=False)

    # 수치 안정성을 위한 정규화 항
    dimension = covariance.shape[0]
    covariance = covariance + np.eye(dimension) * 0.01
    inverse_covariance = np.linalg.inv(covariance)

    def scorer(query_features):
        difference = query_features - mean_vector
        left = np.dot(difference, inverse_covariance)
        distances = np.sum(left * difference, axis=1)
        return np.sqrt(np.maximum(distances, 0.0))

    return scorer


def coreset_subsample(features, target_size, seed):
    """
    PatchCore 의 greedy coreset 을 근사한다.
    무작위 시작점에서 시작해 가장 먼 점을 반복 선택하여 분포를 고르게 덮는다.
    """
    total = features.shape[0]
    if target_size >= total:
        return np.arange(total)

    random_generator = np.random.RandomState(seed)
    first_index = random_generator.randint(total)

    selected = [first_index]
    differences = features - features[first_index]
    min_distances = np.sqrt(np.sum(differences * differences, axis=1))

    step = 1
    while step < target_size:
        next_index = int(np.argmax(min_distances))
        selected.append(next_index)

        differences = features - features[next_index]
        new_distances = np.sqrt(np.sum(differences * differences, axis=1))
        min_distances = np.minimum(min_distances, new_distances)

        step = step + 1

    return np.array(selected)


# ===============================================================
# 실행 및 리포트
# ===============================================================

def evaluate_method(method_name, normal_scores, normal_gaps, defect_score_map, result_rows):
    """한 방법에 대해 클래스별 + 통합 지표를 계산하고 출력한다."""

    print("--- " + method_name + " ---")
    print("target".ljust(18) + "n".ljust(8) + "AUROC".ljust(10) + "FPR@95TPR")

    # 개별 클래스
    for class_name in DEFECT_CLASSES:
        if class_name not in defect_score_map:
            continue
        defect_scores = defect_score_map[class_name]
        auroc = compute_auroc(normal_scores, defect_scores)
        fpr, threshold = compute_fpr_at_tpr(normal_scores, defect_scores, 0.95)

        line = class_name.ljust(18)
        line = line + str(len(defect_scores)).ljust(8)
        line = line + str(round(auroc, 4)).ljust(10)
        line = line + str(round(fpr, 4))
        print(line)

        row = {}
        row["method"] = method_name
        row["target"] = class_name
        row["n_defect"] = len(defect_scores)
        row["auroc"] = round(auroc, 4)
        row["fpr_at_95tpr"] = round(fpr, 4)
        result_rows.append(row)

    # 통합: crack + delamination
    combined_scores = []
    for class_name in COMBINED_MEMBERS:
        if class_name not in defect_score_map:
            continue
        index = 0
        while index < len(defect_score_map[class_name]):
            combined_scores.append(defect_score_map[class_name][index])
            index = index + 1

    if len(combined_scores) > 0:
        combined_array = np.array(combined_scores)
        auroc = compute_auroc(normal_scores, combined_array)
        fpr, threshold = compute_fpr_at_tpr(normal_scores, combined_array, 0.95)

        line = COMBINED_NAME.ljust(18)
        line = line + str(len(combined_array)).ljust(8)
        line = line + str(round(auroc, 4)).ljust(10)
        line = line + str(round(fpr, 4))
        print(line)

        row = {}
        row["method"] = method_name
        row["target"] = COMBINED_NAME
        row["n_defect"] = len(combined_array)
        row["auroc"] = round(auroc, 4)
        row["fpr_at_95tpr"] = round(fpr, 4)
        result_rows.append(row)

        # 갭별 오탐률은 통합 기준 임계값으로 본다
        if len(normal_gaps) == len(normal_scores):
            gap_fpr = compute_gap_fpr(normal_scores, normal_gaps, threshold)
            gap_text = "  갭별 오탐률: "
            for gap in sorted(gap_fpr.keys()):
                gap_text = gap_text + str(gap) + "um=" + str(round(gap_fpr[gap], 3)) + "  "
            print(gap_text)

    print()


def main():
    use_deep = "--deep" in sys.argv

    train_limit = None
    if "--limit" in sys.argv:
        position = sys.argv.index("--limit")
        if position + 1 < len(sys.argv):
            train_limit = int(sys.argv[position + 1])

    if os.path.exists(PATCH_ROOT) is False:
        print("패치 폴더를 찾을 수 없습니다:", PATCH_ROOT)
        print("현재 위치:", os.getcwd())
        return

    print("=" * 78)
    print("이상 탐지 벤치마크")
    print("=" * 78)
    print()

    manifest = load_manifest(MANIFEST_PATH)

    print("데이터 로딩...")
    train_normal, train_names = load_patch_group(
        PATCH_ROOT + "/train/normal", train_limit, 42
    )
    test_normal, test_names = load_patch_group(
        PATCH_ROOT + "/test/normal", None, 42
    )

    if train_normal is None or test_normal is None:
        print("정상 패치를 찾을 수 없습니다.")
        return

    print("  train normal:", train_normal.shape[0])
    print("  test normal :", test_normal.shape[0])

    # 정상 패치의 갭 정보
    test_normal_gaps = []
    for name in test_names:
        key = "test/normal/" + name
        if key in manifest:
            test_normal_gaps.append(manifest[key]["coating_gap"])
        else:
            test_normal_gaps.append(0)

    defect_arrays = {}
    for class_name in DEFECT_CLASSES:
        array, names = load_patch_group(
            PATCH_ROOT + "/eval/" + class_name, None, 42
        )
        if array is None:
            continue
        defect_arrays[class_name] = array
        print("  eval " + class_name + ":", array.shape[0])
    print()

    result_rows = []

    # -----------------------------------------------------------
    # Part A: 밝기 통계
    # -----------------------------------------------------------
    print("=" * 78)
    print("Part A. 밝기 통계 베이스라인 (딥러닝 없음)")
    print("=" * 78)
    print()

    train_gray_mean = float(np.mean(np.mean(train_normal.astype(np.float64), axis=3)))
    print("정상 평균 밝기 기준값:", round(train_gray_mean, 2))
    print()

    trivial_methods = {
        "A1_mean_brightness": score_mean_brightness,
        "A2_max_brightness": score_max_brightness,
        "A3_std_brightness": score_std_brightness,
    }

    for method_name in sorted(trivial_methods.keys()):
        scorer = trivial_methods[method_name]
        start_time = time.time()
        normal_scores = scorer(test_normal)
        elapsed = time.time() - start_time

        defect_score_map = {}
        for class_name in defect_arrays:
            defect_score_map[class_name] = scorer(defect_arrays[class_name])

        evaluate_method(method_name, normal_scores, test_normal_gaps,
                        defect_score_map, result_rows)
        per_patch_ms = elapsed / float(test_normal.shape[0]) * 1000.0
        print("  추론 속도: " + str(round(per_patch_ms, 4)) + " ms/patch")
        print()

    # A4: 정상 평균으로부터의 편차 (양방향)
    def deviation_scorer(array):
        return score_deviation_from_normal(array, train_gray_mean)

    normal_scores = deviation_scorer(test_normal)
    defect_score_map = {}
    for class_name in defect_arrays:
        defect_score_map[class_name] = deviation_scorer(defect_arrays[class_name])
    evaluate_method("A4_abs_deviation", normal_scores, test_normal_gaps,
                    defect_score_map, result_rows)

    # -----------------------------------------------------------
    # Part B: 딥러닝
    # -----------------------------------------------------------
    if use_deep is True:
        print("=" * 78)
        print("Part B. 사전학습 특징 기반")
        print("=" * 78)
        print()

        try:
            import torch
        except ImportError:
            print("torch 가 설치되어 있지 않습니다.")
            print("pip install torch torchvision --break-system-packages")
            write_results(result_rows)
            return

        device_name = "cpu"
        if torch.cuda.is_available() is True:
            device_name = "cuda"
        print("device:", device_name)
        print()

        print("특징 추출 중...")
        start_time = time.time()
        train_features = extract_deep_features(train_normal, 64, device_name)
        print("  train:", train_features.shape, round(time.time() - start_time, 1), "초")

        start_time = time.time()
        test_features = extract_deep_features(test_normal, 64, device_name)
        feature_time = time.time() - start_time
        print("  test :", test_features.shape, round(feature_time, 1), "초")

        defect_features = {}
        for class_name in defect_arrays:
            defect_features[class_name] = extract_deep_features(
                defect_arrays[class_name], 64, device_name
            )
        print()

        feature_ms = feature_time / float(test_normal.shape[0]) * 1000.0

        # B1: kNN
        print("B1: kNN (k=5, 전체 메모리)")
        knn_scorer = build_knn_scorer(train_features, 5)
        start_time = time.time()
        normal_scores = knn_scorer(test_features)
        knn_time = time.time() - start_time

        defect_score_map = {}
        for class_name in defect_features:
            defect_score_map[class_name] = knn_scorer(defect_features[class_name])
        evaluate_method("B1_kNN", normal_scores, test_normal_gaps,
                        defect_score_map, result_rows)
        total_ms = feature_ms + knn_time / float(test_normal.shape[0]) * 1000.0
        print("  추론 속도: " + str(round(total_ms, 2)) + " ms/patch (특징추출 포함)")
        print()

        # B2: 마할라노비스 (PaDiM 단순화)
        print("B2: Mahalanobis (PaDiM 단순화)")
        maha_scorer = build_mahalanobis_scorer(train_features)
        start_time = time.time()
        normal_scores = maha_scorer(test_features)
        maha_time = time.time() - start_time

        defect_score_map = {}
        for class_name in defect_features:
            defect_score_map[class_name] = maha_scorer(defect_features[class_name])
        evaluate_method("B2_Mahalanobis", normal_scores, test_normal_gaps,
                        defect_score_map, result_rows)
        total_ms = feature_ms + maha_time / float(test_normal.shape[0]) * 1000.0
        print("  추론 속도: " + str(round(total_ms, 2)) + " ms/patch (특징추출 포함)")
        print()

        # B3: PatchCore (coreset)
        coreset_size = min(2000, train_features.shape[0])
        print("B3: PatchCore (coreset " + str(coreset_size) + " / "
              + str(train_features.shape[0]) + ")")
        start_time = time.time()
        coreset_indices = coreset_subsample(train_features, coreset_size, 42)
        print("  coreset 구성:", round(time.time() - start_time, 1), "초")

        coreset_features = train_features[coreset_indices]
        patchcore_scorer = build_knn_scorer(coreset_features, 1)

        start_time = time.time()
        normal_scores = patchcore_scorer(test_features)
        pc_time = time.time() - start_time

        defect_score_map = {}
        for class_name in defect_features:
            defect_score_map[class_name] = patchcore_scorer(defect_features[class_name])
        evaluate_method("B3_PatchCore", normal_scores, test_normal_gaps,
                        defect_score_map, result_rows)
        total_ms = feature_ms + pc_time / float(test_normal.shape[0]) * 1000.0
        print("  추론 속도: " + str(round(total_ms, 2)) + " ms/patch (특징추출 포함)")
        print()

    write_results(result_rows)


def write_results(result_rows):
    if len(result_rows) == 0:
        return

    result_file = open(RESULT_PATH, "w", newline="", encoding="utf-8")
    field_names = ["method", "target", "n_defect", "auroc", "fpr_at_95tpr"]
    writer = csv.DictWriter(result_file, fieldnames=field_names)
    writer.writeheader()
    for row in result_rows:
        writer.writerow(row)
    result_file.close()

    print("=" * 78)
    print("결과 저장:", RESULT_PATH)
    print("=" * 78)


main()
