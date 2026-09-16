"""
20_temporal_smoothing.py

목적:
- 프레임 독립 YOLO 검출 (P 0.829 / R 0.909 / mAP@0.5 0.924, seed 0 val 기준) 을
  시간축 평활화로 오탐 제거·미검 트레이드오프를 정량화한다.

두 가지 방법:
  1) Majority Voting: 윈도우 N 프레임 중 M 회 이상 검출이면 인정
  2) Tracking: 프레임 간 검출 박스를 매칭해 트랙 길이 >= L 이면 인정

두 가지 이웃 정의:
  - 인덱스 기준: 시퀀스 정렬 순서대로 슬라이딩
  - 프레임 번호 기준: |Δframe_number| <= W 안의 원소만 이웃으로 인정
    (결번 때문에 인덱스 이웃과 다르다)

데이터 제약:
  - 연속 인덱스 쌍의 patch_idx 일치율이 사실상 0% (탐지 스크립트가 확인).
    즉, 인접 프레임의 공간 영역이 거의 매번 바뀌므로 위치 기반 트래킹은 성립 안 함.
    이 점을 결과에 명시하고 tracking 은 (a) 순진한 중심거리 매칭, (b) 같은 patch_idx 만
    잇는 매칭 두 변형을 함께 보고한다.

입출력:
  - 입력: detections_cache.json, classification/labels.csv,
          data/pinhole_balanced/labels/test/*.txt
  - 출력: results_temporal.csv, figures/temporal_tradeoff.png
"""

import csv
import json
import os
import re
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parent
LABELS_CSV = PROJECT_ROOT / "classification" / "labels.csv"
DETECTIONS_CACHE = PROJECT_ROOT / "detections_cache.json"
TEST_LABELS_DIR = PROJECT_ROOT / "data" / "pinhole_balanced" / "labels" / "test"
FIGURES_DIR = PROJECT_ROOT / "figures"
OUT_CSV = PROJECT_ROOT / "results_temporal.csv"
OUT_FIG = FIGURES_DIR / "temporal_tradeoff.png"

FILENAME_REGEX = re.compile(
    r"^(?P<run>R\d+)-(?P<gap>\d+)um-(?P<position>[a-zA-Z0-9\-]+?)_frame_(?P<frame>\d+)_patch_(?P<patch>\d+)\.png$"
)


# ---- 로더 ----

def load_metadata():
    """image_N -> {run_id, gap, position, frame, patch, pinhole_label, image}"""
    metadata = {}
    with open(LABELS_CSV, "r", encoding="utf-8") as file_handle:
        reader = csv.DictReader(file_handle)
        for row in reader:
            match = FILENAME_REGEX.match(row["original_file_name"])
            if match is None:
                continue
            stem = row["file_name"]
            if stem.endswith(".jpg"):
                stem = stem[:-4]
            metadata[stem] = {
                "stem": stem,
                "run_id": match.group("run"),
                "gap": int(match.group("gap")),
                "position": match.group("position"),
                "frame": int(match.group("frame")),
                "patch": int(match.group("patch")),
                "pinhole_label": int(row["Pinhole"]),
                "image": row["file_name"],
            }
    return metadata


def load_test_stems():
    stems = set()
    for name in os.listdir(TEST_LABELS_DIR):
        if not name.endswith(".txt"):
            continue
        stem = name[:-4]
        stems.add(stem)
    return stems


def load_detections():
    with open(DETECTIONS_CACHE, "r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


_SORT_METADATA = None


def _stem_sort_key(stem_value):
    record = _SORT_METADATA[stem_value]
    return (record["frame"], record["patch"])


def group_sequences(metadata):
    """(run_id, gap, position) -> [stem, ...] sorted by (frame, patch)"""
    global _SORT_METADATA
    _SORT_METADATA = metadata
    sequences = defaultdict(list)
    for stem in metadata:
        record = metadata[stem]
        key = (record["run_id"], record["gap"], record["position"])
        sequences[key].append(stem)

    for key in sequences:
        sequences[key].sort(key=_stem_sort_key)

    return dict(sequences)


# ---- Baseline (frame-independent) ----

def detected(detections_cache, image_name):
    box_list = detections_cache.get(image_name, [])
    if len(box_list) == 0:
        return False
    return True


def prf1(tp, fp, fn):
    if tp + fp == 0:
        precision = 0.0
    else:
        precision = tp / float(tp + fp)
    if tp + fn == 0:
        recall = 0.0
    else:
        recall = tp / float(tp + fn)
    if precision + recall == 0.0:
        f1 = 0.0
    else:
        f1 = 2.0 * precision * recall / (precision + recall)
    return precision, recall, f1


def baseline_frame_level(metadata, detections_cache, test_stems):
    tp = 0
    fp = 0
    fn = 0
    tn = 0
    positive_predicts = 0
    for stem in test_stems:
        if stem not in metadata:
            continue
        gt = metadata[stem]["pinhole_label"]
        pred = 0
        if detected(detections_cache, metadata[stem]["image"]):
            pred = 1
            positive_predicts = positive_predicts + 1
        if pred == 1 and gt == 1:
            tp = tp + 1
        elif pred == 1 and gt == 0:
            fp = fp + 1
        elif pred == 0 and gt == 1:
            fn = fn + 1
        else:
            tn = tn + 1
    precision, recall, f1 = prf1(tp, fp, fn)
    return {
        "method": "frame_independent",
        "window": "-",
        "threshold": "-",
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "n_positive_predict": positive_predicts,
        "n_unique_tracks": "-",
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    }


# ---- 방법 1: Majority Voting (index-based) ----

def build_position_index(sequence_stems):
    position = {}
    for i in range(len(sequence_stems)):
        position[sequence_stems[i]] = i
    return position


def majority_voting_index(metadata, detections_cache, test_stems, sequences, window_size, threshold):
    tp = 0
    fp = 0
    fn = 0
    positive_predicts = 0
    half = window_size // 2

    positions = {}
    for key in sequences:
        positions[key] = build_position_index(sequences[key])

    for stem in test_stems:
        if stem not in metadata:
            continue
        record = metadata[stem]
        key = (record["run_id"], record["gap"], record["position"])
        sequence = sequences[key]
        index = positions[key][stem]
        start = index - half
        end = index + half
        if start < 0:
            start = 0
        if end > len(sequence) - 1:
            end = len(sequence) - 1

        count = 0
        i = start
        while i <= end:
            neighbor_stem = sequence[i]
            neighbor_image = metadata[neighbor_stem]["image"]
            if detected(detections_cache, neighbor_image):
                count = count + 1
            i = i + 1

        if count >= threshold:
            pred = 1
            positive_predicts = positive_predicts + 1
        else:
            pred = 0
        gt = record["pinhole_label"]

        if pred == 1 and gt == 1:
            tp = tp + 1
        elif pred == 1 and gt == 0:
            fp = fp + 1
        elif pred == 0 and gt == 1:
            fn = fn + 1

    precision, recall, f1 = prf1(tp, fp, fn)
    return {
        "method": "majority_index",
        "window": "N=" + str(window_size),
        "threshold": "M=" + str(threshold),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "n_positive_predict": positive_predicts,
        "n_unique_tracks": "-",
        "tp": tp, "fp": fp, "fn": fn,
    }


# ---- 방법 1': Majority Voting (frame-number-based) ----

def majority_voting_frame(metadata, detections_cache, test_stems, sequences, frame_window, threshold):
    """frame_number 차이가 ±frame_window 안의 이웃만 이웃으로 인정."""
    tp = 0
    fp = 0
    fn = 0
    positive_predicts = 0

    # 시퀀스별로 (frame_number -> list of stems) 인덱스를 만든다.
    # 시퀀스가 patch 여러 개를 같은 frame_number 에 담을 수 있으므로 리스트.
    frame_index_by_sequence = {}
    for key in sequences:
        by_frame = defaultdict(list)
        for stem in sequences[key]:
            record = metadata[stem]
            by_frame[record["frame"]].append(stem)
        frame_index_by_sequence[key] = by_frame

    for stem in test_stems:
        if stem not in metadata:
            continue
        record = metadata[stem]
        key = (record["run_id"], record["gap"], record["position"])
        center_frame = record["frame"]

        # 프레임 번호 ±frame_window 범위 안의 모든 이웃 stem 을 모은다.
        neighbors = []
        lower = center_frame - frame_window
        upper = center_frame + frame_window
        by_frame = frame_index_by_sequence[key]
        frame_number = lower
        while frame_number <= upper:
            for neighbor_stem in by_frame.get(frame_number, []):
                neighbors.append(neighbor_stem)
            frame_number = frame_number + 1

        count = 0
        for neighbor_stem in neighbors:
            neighbor_image = metadata[neighbor_stem]["image"]
            if detected(detections_cache, neighbor_image):
                count = count + 1

        if count >= threshold:
            pred = 1
            positive_predicts = positive_predicts + 1
        else:
            pred = 0
        gt = record["pinhole_label"]

        if pred == 1 and gt == 1:
            tp = tp + 1
        elif pred == 1 and gt == 0:
            fp = fp + 1
        elif pred == 0 and gt == 1:
            fn = fn + 1

    precision, recall, f1 = prf1(tp, fp, fn)
    return {
        "method": "majority_frame",
        "window": "W=" + str(frame_window),
        "threshold": "M=" + str(threshold),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "n_positive_predict": positive_predicts,
        "n_unique_tracks": "-",
        "tp": tp, "fp": fp, "fn": fn,
    }


# ---- 방법 2: Tracking ----

def box_center(box):
    x = 0.5 * (box["x1"] + box["x2"])
    y = 0.5 * (box["y1"] + box["y2"])
    return x, y


def distance(pa, pb):
    dx = pa[0] - pb[0]
    dy = pa[1] - pb[1]
    return (dx * dx + dy * dy) ** 0.5


def run_tracking(metadata, detections_cache, sequences, max_skip, min_length, distance_threshold, same_patch_only):
    """
    각 시퀀스별로 트랙을 만든다.
    - 트랙은 {track_id, last_index, boxes: [(stem, box)], last_center}
    - 프레임 i 의 각 박스에 대해 가장 가까운 open 트랙(마지막 매칭 이후 max_skip 프레임 이내)과
      거리 <= distance_threshold 이면 그 트랙에 이어붙인다. 없으면 새 트랙 생성.
    - same_patch_only=True 이면 트랙 끝 stem 과 현재 stem 의 patch_idx 가 동일할 때만 확장.

    반환:
    - tracks: 전체 (여러 시퀀스) 트랙 리스트
    - stem_in_track: {stem: True if 어떤 유효 트랙(길이>=min_length)에 속함}
    """
    all_tracks = []
    stem_positive = set()

    for key in sequences:
        sequence = sequences[key]
        open_tracks = []
        for index in range(len(sequence)):
            stem = sequence[index]
            record = metadata[stem]
            box_list = detections_cache.get(record["image"], [])

            # 만료된 트랙 정리 (마지막 매칭 이후 max_skip 초과)
            still_open = []
            for track in open_tracks:
                if index - track["last_index"] <= max_skip:
                    still_open.append(track)
                else:
                    all_tracks.append(track)
            open_tracks = still_open

            for box in box_list:
                center = box_center(box)
                best_track = None
                best_distance = distance_threshold + 1.0
                for track in open_tracks:
                    if same_patch_only:
                        last_patch = metadata[track["last_stem"]]["patch"]
                        if last_patch != record["patch"]:
                            continue
                    delta = distance(center, track["last_center"])
                    if delta < best_distance:
                        best_distance = delta
                        best_track = track
                if best_track is not None and best_distance <= distance_threshold:
                    best_track["boxes"].append((stem, box))
                    best_track["last_index"] = index
                    best_track["last_center"] = center
                    best_track["last_stem"] = stem
                else:
                    new_track = {
                        "boxes": [(stem, box)],
                        "last_index": index,
                        "last_center": center,
                        "last_stem": stem,
                    }
                    open_tracks.append(new_track)

        for track in open_tracks:
            all_tracks.append(track)

    # 트랙 필터 + stem 집합
    valid_tracks = []
    for track in all_tracks:
        if len(track["boxes"]) >= min_length:
            valid_tracks.append(track)
            for member in track["boxes"]:
                stem_positive.add(member[0])

    return valid_tracks, stem_positive


def evaluate_tracking(metadata, sequences, detections_cache, test_stems,
                     max_skip, min_length, distance_threshold, same_patch_only):
    valid_tracks, stem_positive = run_tracking(
        metadata, detections_cache, sequences,
        max_skip, min_length, distance_threshold, same_patch_only,
    )

    tp = 0
    fp = 0
    fn = 0
    positive_predicts = 0
    for stem in test_stems:
        if stem not in metadata:
            continue
        gt = metadata[stem]["pinhole_label"]
        if stem in stem_positive:
            pred = 1
            positive_predicts = positive_predicts + 1
        else:
            pred = 0
        if pred == 1 and gt == 1:
            tp = tp + 1
        elif pred == 1 and gt == 0:
            fp = fp + 1
        elif pred == 0 and gt == 1:
            fn = fn + 1

    precision, recall, f1 = prf1(tp, fp, fn)
    if same_patch_only:
        method_name = "tracking_same_patch"
    else:
        method_name = "tracking_naive"
    return {
        "method": method_name,
        "window": "K=" + str(max_skip),
        "threshold": "L=" + str(min_length),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "n_positive_predict": positive_predicts,
        "n_unique_tracks": len(valid_tracks),
        "tp": tp, "fp": fp, "fn": fn,
    }


# ---- 시퀀스 통계 보고 ----

def report_sequence_stats(metadata, sequences):
    print("시퀀스 프레임 결번 · patch 연속성 통계")
    for key in sorted(sequences.keys()):
        stems = sequences[key]
        frames = []
        patches = []
        for stem in stems:
            frames.append(metadata[stem]["frame"])
            patches.append(metadata[stem]["patch"])
        span = max(frames) - min(frames) + 1
        fill = len(frames) / float(span)

        same_patch_pairs = 0
        for i in range(1, len(stems)):
            if patches[i] == patches[i - 1]:
                same_patch_pairs = same_patch_pairs + 1
        if len(stems) > 1:
            same_patch_rate = same_patch_pairs / float(len(stems) - 1)
        else:
            same_patch_rate = 0.0

        print("  {}: n={}, frame_span={}, fill_rate={:.2f}, same_patch_adjacent={:.2%}".format(
            key, len(stems), span, fill, same_patch_rate))


# ---- 실행 ----

def main():
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("20_temporal_smoothing.py")
    print("=" * 60)

    metadata = load_metadata()
    print("labels.csv: {} images".format(len(metadata)))

    test_stems = load_test_stems()
    print("test split: {} images".format(len(test_stems)))

    detections_cache = load_detections()
    print("detections_cache: {} images".format(len(detections_cache)))

    sequences = group_sequences(metadata)
    print("sequences: {}".format(len(sequences)))
    print()
    report_sequence_stats(metadata, sequences)
    print()

    results = []

    # Baseline
    base = baseline_frame_level(metadata, detections_cache, test_stems)
    print("Baseline (frame_independent, frame-level P/R on test):")
    print("  P={:.3f}, R={:.3f}, F1={:.3f}, positive_predicts={}".format(
        base["precision"], base["recall"], base["f1"], base["n_positive_predict"]))
    results.append(base)
    print()

    # Majority index
    print("Majority Voting (index-based):")
    for window_size in [3, 5, 7]:
        for threshold in range(1, window_size + 1):
            record = majority_voting_index(metadata, detections_cache, test_stems, sequences, window_size, threshold)
            results.append(record)
            print("  N={} M={}: P={:.3f} R={:.3f} F1={:.3f} (pos_pred={})".format(
                window_size, threshold, record["precision"], record["recall"], record["f1"], record["n_positive_predict"]))
    print()

    # Majority frame-number
    print("Majority Voting (frame_number-based):")
    for frame_window in [1, 2, 3, 5]:
        for threshold in [1, 2, 3]:
            record = majority_voting_frame(metadata, detections_cache, test_stems, sequences, frame_window, threshold)
            results.append(record)
            print("  W=±{} M={}: P={:.3f} R={:.3f} F1={:.3f} (pos_pred={})".format(
                frame_window, threshold, record["precision"], record["recall"], record["f1"], record["n_positive_predict"]))
    print()

    # Tracking (naive)
    print("Tracking (naive center distance, spatial correspondence UNVERIFIED):")
    for max_skip in [1, 2, 3]:
        for min_length in [1, 2, 3]:
            record = evaluate_tracking(metadata, sequences, detections_cache, test_stems,
                                        max_skip, min_length, distance_threshold=60.0, same_patch_only=False)
            results.append(record)
            print("  K={} L={}: P={:.3f} R={:.3f} F1={:.3f} tracks={} (pos_pred={})".format(
                max_skip, min_length, record["precision"], record["recall"], record["f1"],
                record["n_unique_tracks"], record["n_positive_predict"]))
    print()

    # Tracking (same-patch only)
    print("Tracking (same-patch-idx only, spatially principled but sparse):")
    for max_skip in [1, 2, 3]:
        for min_length in [1, 2, 3]:
            record = evaluate_tracking(metadata, sequences, detections_cache, test_stems,
                                        max_skip, min_length, distance_threshold=60.0, same_patch_only=True)
            results.append(record)
            print("  K={} L={}: P={:.3f} R={:.3f} F1={:.3f} tracks={} (pos_pred={})".format(
                max_skip, min_length, record["precision"], record["recall"], record["f1"],
                record["n_unique_tracks"], record["n_positive_predict"]))
    print()

    # CSV 저장
    write_csv(results)
    print("저장: {}".format(OUT_CSV))

    # 트레이드오프 그림
    plot_tradeoff(results)
    print("저장: {}".format(OUT_FIG))

    print()
    print("완료.")


def write_csv(results):
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as file_handle:
        writer = csv.writer(file_handle)
        writer.writerow([
            "method", "window", "threshold",
            "precision", "recall", "f1",
            "n_positive_predict", "n_unique_tracks",
        ])
        for record in results:
            writer.writerow([
                record["method"], record["window"], record["threshold"],
                round(record["precision"], 4),
                round(record["recall"], 4),
                round(record["f1"], 4),
                record["n_positive_predict"],
                record["n_unique_tracks"],
            ])


# ---- 파레토 프론티어 ----

def compute_pareto(points):
    """(recall, precision) 페어의 파레토 프론티어를 반환."""
    ordered = []
    for entry in points:
        ordered.append(entry)

    def sort_key(item):
        return (-item[0], -item[1])
    ordered.sort(key=sort_key)

    frontier = []
    best_precision = -1.0
    for recall, precision, label in ordered:
        if precision > best_precision:
            frontier.append((recall, precision, label))
            best_precision = precision
    return frontier


def plot_tradeoff(results):
    method_style = {
        "frame_independent": {"color": "#dc2626", "marker": "*", "size": 220, "label": "frame_independent (baseline)"},
        "majority_index":    {"color": "#2563eb", "marker": "o", "size": 40,  "label": "majority_index"},
        "majority_frame":    {"color": "#059669", "marker": "s", "size": 40,  "label": "majority_frame"},
        "tracking_naive":    {"color": "#f59e0b", "marker": "D", "size": 40,  "label": "tracking_naive (unverified)"},
        "tracking_same_patch": {"color": "#7c3aed", "marker": "^", "size": 60, "label": "tracking_same_patch (principled/sparse)"},
    }

    fig, axis = plt.subplots(figsize=(8, 6))

    plotted_labels = set()
    all_points = []
    for record in results:
        style = method_style.get(record["method"])
        if style is None:
            continue
        show_label = None
        if style["label"] not in plotted_labels:
            show_label = style["label"]
            plotted_labels.add(style["label"])
        axis.scatter(
            record["recall"], record["precision"],
            c=style["color"], marker=style["marker"],
            s=style["size"], edgecolors="black", linewidths=0.5,
            label=show_label, zorder=3,
        )
        all_points.append((record["recall"], record["precision"], record["method"]))

    # 파레토 프론티어
    frontier = compute_pareto(all_points)
    if len(frontier) >= 2:
        xs = []
        ys = []
        for recall, precision, _ in frontier:
            xs.append(recall)
            ys.append(precision)
        axis.plot(xs, ys, color="#111827", linestyle="--", linewidth=1.2,
                  alpha=0.6, label="Pareto frontier", zorder=2)

    axis.set_xlabel("Recall (frame-level, test)")
    axis.set_ylabel("Precision (frame-level, test)")
    axis.set_title("Temporal smoothing tradeoff (frame-level, test split)")
    axis.set_xlim(0.0, 1.0)
    axis.set_ylim(0.0, 1.05)
    axis.grid(True, alpha=0.3)
    axis.legend(loc="lower left", fontsize=9)
    fig.tight_layout()
    fig.savefig(str(OUT_FIG), dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
