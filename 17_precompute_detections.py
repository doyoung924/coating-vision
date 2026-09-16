"""
17_precompute_detections.py

목적:
- 대시보드 /stream 페이지의 재생 지연을 없애기 위해
  전체 2,227장에 대해 YOLO 추론을 미리 수행하고,
  SPC 지표(defect_ratio, EWMA, UCL, center) + 알람 규칙과 조인한
  시퀀스별 재생 데이터를 만든다.
- A3 이상 탐지 임계값도 여기서 산정해 저장한다
  (/inspect 페이지에서 재사용).

평문 로직:
1. classification/labels.csv 로부터 image_N ↔ (run/gap/position/frame_number/patch_idx/labels) 파싱
2. (run, gap, position) 로 시퀀스 그룹핑 후 (frame_number, stem) 로 정렬.
   09_spc_monitor.py 와 동일 순서로 → results_spc.csv 행과 1:1 매칭 가능
3. YOLO best.pt 로 각 이미지 추론, {stem → detections} 캐시 구성
4. 결함 라벨 총합 0 인 프레임들의 64×64 stride 64 패치 STD 분포에서
   95퍼센타일을 A3 patch_threshold 로 채택
5. results_spc.csv 를 순회하며 시퀀스별 순서대로 (defect_ratio, moving_average, ewma) 를 붙이고,
   results_spc_alerts.csv 로 (frame_number → 알람 규칙 리스트) 매핑을 조인
6. advisor_report.json 에서 시퀀스별 capability, center, ucl 확보
7. 산출:
   - detections_cache.json      : {stem_or_image_name: [{x1,y1,x2,y2,conf}, ...]}
   - stream_data/index.json     : {patch_config, sequences: [...]}
   - stream_data/<seq_id>.json  : {sequence header + frames[]}
"""

import csv
import json
import os
import re
import time
from pathlib import Path

import numpy as np
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parent
LABELS_CSV = PROJECT_ROOT / "classification" / "labels.csv"
SPC_CSV = PROJECT_ROOT / "results_spc.csv"
SPC_ALERTS_CSV = PROJECT_ROOT / "results_spc_alerts.csv"
ADVISOR_JSON = PROJECT_ROOT / "advisor_report.json"
IMAGE_DIR = PROJECT_ROOT / "segmentation" / "images"
YOLO_WEIGHTS = PROJECT_ROOT / "runs" / "pinhole_v1" / "weights" / "best.pt"

OUT_DETECTIONS = PROJECT_ROOT / "detections_cache.json"
OUT_STREAM_DIR = PROJECT_ROOT / "stream_data"

PATCH_SIZE = 64
STRIDE = 64
CLEAN_TARGET_PERCENTILE = 95.0
YOLO_CONF = 0.25


# ---------- 파일명 파싱 ----------

FILENAME_REGEX = re.compile(
    r"^(?P<run>R\d+)-(?P<gap>\d+)um-(?P<position>[a-zA-Z0-9\-]+?)_frame_(?P<frame>\d+)_patch_(?P<patch>\d+)\.png$"
)


def parse_original_filename(name):
    match = FILENAME_REGEX.match(name)
    if match is None:
        return None
    parsed = {
        "run_id": match.group("run"),
        "coating_gap": int(match.group("gap")),
        "position": match.group("position"),
        "frame_number": int(match.group("frame")),
        "patch_idx": int(match.group("patch")),
    }
    return parsed


def load_metadata():
    metadata = {}
    with open(LABELS_CSV, "r", encoding="utf-8") as file_handle:
        reader = csv.DictReader(file_handle)
        for row in reader:
            parsed = parse_original_filename(row["original_file_name"])
            if parsed is None:
                continue
            parsed["surface_crack"] = int(row["Surface_Crack"])
            parsed["delamination"] = int(row["Delamination"])
            parsed["pinhole"] = int(row["Pinhole"])
            image_file = row["file_name"]
            stem = image_file
            if stem.endswith(".jpg"):
                stem = stem[:-4]
            parsed["image"] = image_file
            parsed["stem"] = stem
            metadata[stem] = parsed
    return metadata


# ---------- 시퀀스 그룹핑 ----------

def group_sequences(metadata):
    sequences = {}
    for stem in metadata:
        record = metadata[stem]
        key = (record["run_id"], record["coating_gap"], record["position"])
        if key not in sequences:
            sequences[key] = []
        sequences[key].append((record["frame_number"], stem))

    for key in sequences:
        sequences[key].sort()

    return sequences


def sequence_id(run_id, coating_gap, position):
    return "{}_{}_{}".format(run_id, coating_gap, position)


def sequence_label(run_id, coating_gap, position):
    return "{}/{}µm/{}".format(run_id, coating_gap, position)


# ---------- YOLO 추론 (전체 이미지) ----------

def run_yolo_all(metadata):
    from ultralytics import YOLO
    print("YOLO 로드: {}".format(YOLO_WEIGHTS))
    model = YOLO(str(YOLO_WEIGHTS))

    detections_by_image = {}
    total = len(metadata)
    processed = 0
    start = time.time()

    stems = []
    for stem in metadata:
        stems.append(stem)
    stems.sort()

    for stem in stems:
        image_file = metadata[stem]["image"]
        image_path = IMAGE_DIR / image_file
        if image_path.exists() is False:
            continue
        image_array = np.array(Image.open(str(image_path)).convert("RGB"))
        predictions = model.predict(source=image_array, conf=YOLO_CONF, verbose=False)
        first = predictions[0]
        box_list = []
        if first.boxes is not None and len(first.boxes) > 0:
            xyxy = first.boxes.xyxy.cpu().numpy()
            conf = first.boxes.conf.cpu().numpy()
            for i in range(len(xyxy)):
                x1 = int(round(float(xyxy[i][0])))
                y1 = int(round(float(xyxy[i][1])))
                x2 = int(round(float(xyxy[i][2])))
                y2 = int(round(float(xyxy[i][3])))
                confidence = round(float(conf[i]), 3)
                box_list.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "conf": confidence})
        detections_by_image[image_file] = box_list

        processed = processed + 1
        if processed % 200 == 0:
            elapsed = time.time() - start
            print("  {}/{} ({:.1f}s)".format(processed, total, elapsed))

    elapsed = time.time() - start
    print("YOLO 추론 완료: {} 장, {:.1f}s".format(processed, elapsed))
    return detections_by_image


# ---------- A3 patch threshold ----------

def score_patches_std(image_array):
    gray = np.mean(image_array.astype(np.float64), axis=2)
    height = gray.shape[0]
    width = gray.shape[1]
    scores = []
    y = 0
    while y + PATCH_SIZE <= height:
        x = 0
        while x + PATCH_SIZE <= width:
            patch = gray[y:y + PATCH_SIZE, x:x + PATCH_SIZE]
            scores.append(float(np.std(patch)))
            x = x + STRIDE
        y = y + STRIDE
    return np.array(scores)


def determine_patch_threshold(metadata):
    print("A3 patch_threshold 계산 (결함 라벨 0 인 프레임의 STD 95pct)")
    clean_stems = []
    for stem in metadata:
        record = metadata[stem]
        total_labels = record["surface_crack"] + record["delamination"] + record["pinhole"]
        if total_labels == 0:
            clean_stems.append(stem)
    clean_stems.sort()

    all_scores = []
    for stem in clean_stems:
        image_path = IMAGE_DIR / metadata[stem]["image"]
        if image_path.exists() is False:
            continue
        image_array = np.array(Image.open(str(image_path)).convert("RGB"))
        scores = score_patches_std(image_array)
        for value in scores:
            all_scores.append(float(value))

    if len(all_scores) == 0:
        threshold = 3.0
        clean_count = 0
    else:
        threshold = float(np.percentile(np.array(all_scores), CLEAN_TARGET_PERCENTILE))
        clean_count = len(clean_stems)
    print("  clean_frames={}, threshold={:.3f}".format(clean_count, threshold))
    return threshold, clean_count


# ---------- SPC 지표 로드 ----------

def load_spc_metrics():
    """
    results_spc.csv 를 (run_id, coating_gap, position) 별 순서 리스트로 반환.
    09_spc_monitor.py 가 sequence 정렬 순서대로 행을 썼기 때문에
    이 리스트의 i 번째 원소는 group_sequences 결과의 i 번째 (frame_number, stem) 와 대응한다.

    09 가 stem 컬럼을 저장하도록 개정된 뒤로는 stem 을 함께 로드해 두어
    build_stream_data 의 순서 조인 지점에서 sequence_entries 의 stem 과 대조한다.
    """
    metrics_by_key = {}
    with open(SPC_CSV, "r", encoding="utf-8") as file_handle:
        reader = csv.DictReader(file_handle)
        for row in reader:
            key = (row["run_id"], int(row["coating_gap"]), row["position"])
            if key not in metrics_by_key:
                metrics_by_key[key] = []
            metrics_by_key[key].append({
                "stem": row.get("stem", ""),
                "frame_number": int(row["frame_number"]),
                "defect_ratio": float(row["defect_ratio"]),
                "moving_average": float(row["moving_average"]),
                "ewma": float(row["ewma"]),
                "ucl": float(row["ucl"]),
                "center": float(row["center"]),
                "label_crack": int(row["label_crack"]),
                "label_delam": int(row["label_delam"]),
                "label_pinhole": int(row["label_pinhole"]),
            })
    return metrics_by_key


def load_alerts():
    alerts_by_key = {}
    with open(SPC_ALERTS_CSV, "r", encoding="utf-8") as file_handle:
        reader = csv.DictReader(file_handle)
        for row in reader:
            key = (row["run_id"], int(row["coating_gap"]), row["position"], int(row["frame_number"]))
            if key not in alerts_by_key:
                alerts_by_key[key] = []
            alerts_by_key[key].append({
                "rule": row["rule"],
                "value": float(row["value"]),
            })
    return alerts_by_key


def load_capability_map():
    with open(ADVISOR_JSON, "r", encoding="utf-8") as file_handle:
        advisor = json.load(file_handle)
    capability_by_key = {}
    for sequence in advisor["sequences"]:
        key = (sequence["run_id"], sequence["coating_gap"], sequence["position"])
        capability_by_key[key] = {
            "capability": sequence["observation"]["spc"]["capability"],
            "center": sequence["observation"]["spc"]["center"],
            "ucl": sequence["observation"]["spc"]["ucl"],
            "alert_total": sequence["observation"]["spc"]["alert_total"],
        }
    return capability_by_key


# ---------- 조인 및 출력 ----------

def build_stream_data(metadata, sequences, detections_by_image, spc_metrics, alerts_by_key, capability_map):
    OUT_STREAM_DIR.mkdir(parents=True, exist_ok=True)

    index_entries = []
    for key in sorted(sequences.keys()):
        run_id, coating_gap, position = key
        seq_id = sequence_id(run_id, coating_gap, position)
        label = sequence_label(run_id, coating_gap, position)
        sequence_entries = sequences[key]
        metrics_list = spc_metrics.get(key, [])
        capability_info = capability_map.get(key, {})

        if len(sequence_entries) != len(metrics_list):
            print("경고: {} 길이 불일치 (seq={}, spc={})".format(
                label, len(sequence_entries), len(metrics_list)))

        frames = []
        for index in range(len(sequence_entries)):
            frame_number, stem = sequence_entries[index]
            record = metadata[stem]
            image_file = record["image"]
            metrics = None
            if index < len(metrics_list):
                metrics = metrics_list[index]
                metrics_stem = metrics.get("stem", "")
                if metrics_stem != "" and metrics_stem != stem:
                    raise RuntimeError(
                        "SPC CSV 순서 조인 검증 실패: "
                        "sequence_entries[{}]={} vs results_spc.csv={}".format(
                            index, stem, metrics_stem))
            alert_key = (run_id, coating_gap, position, frame_number)
            alerts_here = alerts_by_key.get(alert_key, [])
            frame_entry = {
                "index": index,
                "image": image_file,
                "frame_number": frame_number,
                "patch_idx": record["patch_idx"],
                "labels": {
                    "surface_crack": record["surface_crack"],
                    "delamination": record["delamination"],
                    "pinhole": record["pinhole"],
                },
                "detections": detections_by_image.get(image_file, []),
                "alerts": alerts_here,
            }
            if metrics is None:
                frame_entry["defect_ratio"] = None
                frame_entry["moving_average"] = None
                frame_entry["ewma"] = None
            else:
                frame_entry["defect_ratio"] = round(metrics["defect_ratio"], 4)
                frame_entry["moving_average"] = round(metrics["moving_average"], 4)
                frame_entry["ewma"] = round(metrics["ewma"], 4)
            frames.append(frame_entry)

        capability = capability_info.get("capability", "UNKNOWN")
        center = capability_info.get("center", 0.0)
        ucl = capability_info.get("ucl", 0.0)
        alert_total = capability_info.get("alert_total", 0)

        seq_payload = {
            "sequence_id": seq_id,
            "label": label,
            "run_id": run_id,
            "coating_gap": coating_gap,
            "position": position,
            "capability": capability,
            "center": center,
            "ucl": ucl,
            "alert_total": alert_total,
            "n_frames": len(frames),
            "frames": frames,
        }
        out_path = OUT_STREAM_DIR / (seq_id + ".json")
        with open(out_path, "w", encoding="utf-8") as file_handle:
            json.dump(seq_payload, file_handle, ensure_ascii=False)
        print("  {} → {} ({} frames)".format(label, out_path.name, len(frames)))

        index_entries.append({
            "sequence_id": seq_id,
            "label": label,
            "run_id": run_id,
            "coating_gap": coating_gap,
            "position": position,
            "capability": capability,
            "center": center,
            "ucl": ucl,
            "alert_total": alert_total,
            "n_frames": len(frames),
        })

    return index_entries


def main():
    print("=" * 60)
    print("17_precompute_detections.py")
    print("=" * 60)

    metadata = load_metadata()
    print("labels.csv: {} entries".format(len(metadata)))

    sequences = group_sequences(metadata)
    print("시퀀스: {} 개".format(len(sequences)))

    detections_by_image = run_yolo_all(metadata)

    with open(OUT_DETECTIONS, "w", encoding="utf-8") as file_handle:
        json.dump(detections_by_image, file_handle, ensure_ascii=False)
    print("저장: {} ({} 이미지)".format(OUT_DETECTIONS.name, len(detections_by_image)))

    patch_threshold, clean_count = determine_patch_threshold(metadata)

    spc_metrics = load_spc_metrics()
    alerts_by_key = load_alerts()
    capability_map = load_capability_map()

    index_entries = build_stream_data(
        metadata, sequences, detections_by_image, spc_metrics, alerts_by_key, capability_map,
    )

    index_payload = {
        "patch_config": {
            "patch_size": PATCH_SIZE,
            "stride": STRIDE,
            "threshold": round(patch_threshold, 4),
            "clean_frames_used": clean_count,
            "source": "17_precompute_detections.py:determine_patch_threshold",
        },
        "sequences": index_entries,
    }
    out_index = OUT_STREAM_DIR / "index.json"
    with open(out_index, "w", encoding="utf-8") as file_handle:
        json.dump(index_payload, file_handle, ensure_ascii=False, indent=2)
    print("저장: {}".format(out_index))

    print("완료")


if __name__ == "__main__":
    main()
