"""
Flask dashboard for the coating defect inline inspection system.

동작 로직 (평문):

1. 앱 시작 시 캐시된 산출물을 메모리에 올린다.
   - advisor_report.json / defect_map.json / kpi_headline.json
   - stream_data/index.json + stream_data/<seq>.json (17_precompute_detections.py 산출)
   - detections_cache.json (스트림 및 /inspect 재사용)
   - results_benchmark.csv / results_robustness.csv
   - YOLO best.pt (1회 로드)

2. /            : 시퀀스별 SPC 관리도 + Advisor 3층 리포트 (v1 유지)
3. /stream      : 프레임 재생 인라인 시뮬레이션. 데이터는 /api/sequence/<seq> 에서 JSON 으로 받는다.
                  /frame/<name> 이 이미지 원본을 서빙한다.
4. /inspect     : 업로드 이미지에 대해 전체 파이프라인
                  (A3 이상 탐지 → 히트맵 → YOLO 검출 → 형태 분석 → Advisor 판정) 실행.
                  각 단계별 소요 시간 기록.
5. /benchmark   : 벤치마크 · 강건성 표 (v1 유지)
6. /detection   : /inspect 로 리다이렉트 (구 링크 호환)
"""

import csv
import json
import time
from pathlib import Path

import numpy as np
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)


# --- 경로 ---

PROJECT_ROOT = Path(__file__).resolve().parent
FIGURES_DIR = PROJECT_ROOT / "figures"
UPLOADS_DIR = PROJECT_ROOT / "static" / "uploads"
SAMPLES_DIR = PROJECT_ROOT / "static" / "samples"
IMAGE_DIR = PROJECT_ROOT / "segmentation" / "images"
STREAM_DIR = PROJECT_ROOT / "stream_data"
STREAM_INDEX = STREAM_DIR / "index.json"
DETECTIONS_CACHE = PROJECT_ROOT / "detections_cache.json"
YOLO_WEIGHTS = PROJECT_ROOT / "runs" / "pinhole_v1" / "weights" / "best.pt"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# --- 로더 ---

def load_json(path):
    with open(path, "r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


def load_csv(path):
    rows = []
    with open(path, "r", encoding="utf-8") as file_handle:
        reader = csv.DictReader(file_handle)
        for row in reader:
            rows.append(row)
    return rows


advisor = load_json(PROJECT_ROOT / "advisor_report.json")
defect_map = load_json(PROJECT_ROOT / "defect_map.json")
kpi = load_json(PROJECT_ROOT / "kpi_headline.json")
benchmark_rows = load_csv(PROJECT_ROOT / "results_benchmark.csv")
robustness_rows = load_csv(PROJECT_ROOT / "results_robustness.csv")


# --- 스트림 캐시 ---

stream_index_data = None
stream_index_error = None
try:
    stream_index_data = load_json(STREAM_INDEX)
except FileNotFoundError:
    stream_index_error = "stream_data/index.json 이 없음. `python 17_precompute_detections.py` 를 먼저 실행하세요."

detections_cache = {}
if DETECTIONS_CACHE.exists():
    detections_cache = load_json(DETECTIONS_CACHE)


# --- YOLO ---

yolo_model = None
yolo_load_error = None
try:
    from ultralytics import YOLO
    if YOLO_WEIGHTS.exists():
        yolo_model = YOLO(str(YOLO_WEIGHTS))
    else:
        yolo_load_error = "weights file not found: {}".format(YOLO_WEIGHTS)
except Exception as exc:
    yolo_load_error = "YOLO 로드 실패: {}".format(exc)


# --- 시맨틱 세그 (§20) ---

SEG_WEIGHTS = PROJECT_ROOT / "runs" / "semantic" / "seed0" / "best.pt"
seg_model = None
seg_load_error = None
try:
    import torch
    import segmentation_models_pytorch as smp
    if SEG_WEIGHTS.exists():
        seg_model = smp.Unet(
            encoder_name="resnet34", encoder_weights=None,
            in_channels=3, classes=3,
        )
        _sd = torch.load(str(SEG_WEIGHTS), map_location="cpu", weights_only=True)
        seg_model.load_state_dict(_sd)
        seg_model.eval()
    else:
        seg_load_error = "seg weights 없음: {}".format(SEG_WEIGHTS)
except Exception as exc:
    seg_load_error = "seg 로드 실패: {}".format(exc)


# --- KPI ---

def find_a3_drying_auroc():
    for row in benchmark_rows:
        if row["method"] == "A3_std_brightness" and row["target"] == "drying_defect":
            return float(row["auroc"])
    return None


def count_incapable(sequences):
    total = 0
    incapable = 0
    for sequence in sequences:
        total = total + 1
        capability = sequence["observation"]["spc"]["capability"]
        if capability == "INCAPABLE":
            incapable = incapable + 1
    return incapable, total


incapable_count, total_sequences = count_incapable(advisor["sequences"])

# INCAPABLE 카운트는 계산은 하되 headline KPI 카드로는 노출하지 않는다.
# 규격 상한 0.25 가 임의값(experiment_log.md §9-4, §15-10)이라 카드 크기의
# 주장으로 부적합. 사이드바 시퀀스 목록의 각 배지에서 판정 자체는 계속 볼 수 있고,
# 하단 각주로만 합계를 요약한다.
HEADLINE = {
    "map50_value": kpi["pinhole_map50"]["value"],
    "map50_std": kpi["pinhole_map50"]["std"],
    "map50_seeds": kpi["pinhole_map50"]["n_seeds"],
    "map50_test": kpi["pinhole_map50"].get("test_map50"),
    "map50_test_std": kpi["pinhole_map50"].get("test_map50_std"),
    "map50_test_n": kpi["pinhole_map50"].get("test_n_patch"),
    "auroc": find_a3_drying_auroc(),
    "speed_ms_per_cell": kpi["a3_speed_ms_per_cell"]["value"],
    "speed_ms_per_patch": kpi["a3_speed_ms_per_cell"]["approx_ms_per_patch"],
    "spearman_value": kpi["a3_vs_mask_spearman"]["value"],
    "spearman_n": kpi["a3_vs_mask_spearman"]["n"],
    "spearman_target": kpi["a3_vs_mask_spearman"]["target"],
    "seg_crack_val": kpi.get("semantic_crack_iou", {}).get("value", 0.0),
    "seg_crack_test_in": kpi.get("semantic_crack_iou", {}).get("test_in_iou", 0.0),
    "seg_crack_test_out": kpi.get("semantic_crack_iou", {}).get("test_out_iou", 0.0),
    "incapable": incapable_count,
    "total_sequences": total_sequences,
}


# --- 시퀀스 헬퍼 ---

def spc_figure_name(sequence):
    return "spc_{}_{}_{}.png".format(
        sequence["run_id"],
        sequence["coating_gap"],
        sequence["position"],
    )


def sequence_label(sequence):
    return "{}/{}µm/{}".format(
        sequence["run_id"],
        sequence["coating_gap"],
        sequence["position"],
    )


def interpretation_priority(item):
    return item.get("priority", 99)


def sort_interpretations(interpretations):
    ordered = []
    for item in interpretations:
        ordered.append(item)
    ordered.sort(key=interpretation_priority)
    return ordered


# --- 이상 탐지 (A3) ---

def score_patches_std_gray(gray):
    patch_size = 64
    stride = 64
    height = gray.shape[0]
    width = gray.shape[1]
    scores = []
    positions = []
    y = 0
    while y + patch_size <= height:
        x = 0
        while x + patch_size <= width:
            patch = gray[y:y + patch_size, x:x + patch_size]
            scores.append(float(np.std(patch)))
            positions.append((x, y, patch_size, patch_size))
            x = x + stride
        y = y + stride
    return scores, positions


def get_patch_threshold():
    if stream_index_data is None:
        return 3.0
    config = stream_index_data.get("patch_config", {})
    return float(config.get("threshold", 3.0))


# --- Flask ---

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024


@app.route("/")
def index():
    sequences = advisor["sequences"]

    seq_index_raw = request.args.get("seq", "0")
    try:
        seq_index = int(seq_index_raw)
    except ValueError:
        seq_index = 0
    if seq_index < 0 or seq_index >= len(sequences):
        seq_index = 0

    selected = sequences[seq_index]

    sidebar = []
    for idx in range(len(sequences)):
        sequence = sequences[idx]
        entry = {
            "index": idx,
            "label": sequence_label(sequence),
            "capability": sequence["observation"]["spc"]["capability"],
            "center": sequence["observation"]["spc"]["center"],
            "ucl": sequence["observation"]["spc"]["ucl"],
            "alerts": sequence["observation"]["spc"]["alert_total"],
            "selected": (idx == seq_index),
        }
        sidebar.append(entry)

    return render_template(
        "index.html",
        headline=HEADLINE,
        sidebar=sidebar,
        selected=selected,
        selected_index=seq_index,
        selected_label=sequence_label(selected),
        spc_figure=spc_figure_name(selected),
        interpretations=sort_interpretations(selected["interpretations"]),
        domain_gap_note=advisor.get("domain_gap_note", {}),
    )


@app.route("/figures/<path:name>")
def serve_figure(name):
    return send_from_directory(FIGURES_DIR, name)


@app.route("/frame/<path:name>")
def serve_frame(name):
    return send_from_directory(IMAGE_DIR, name)


# --- /stream ---

@app.route("/stream")
def stream_page():
    if stream_index_data is None:
        return render_template(
            "stream.html",
            index_ready=False,
            index_error=stream_index_error,
            sequences=[],
            patch_threshold=None,
            initial_seq=None,
        )

    sequences_list = stream_index_data.get("sequences", [])
    patch_config = stream_index_data.get("patch_config", {})
    initial_seq = None
    if len(sequences_list) > 0:
        initial_seq = sequences_list[0]["sequence_id"]

    return render_template(
        "stream.html",
        index_ready=True,
        index_error=None,
        sequences=sequences_list,
        patch_threshold=patch_config.get("threshold"),
        initial_seq=initial_seq,
    )


@app.route("/api/sequence/<seq_id>")
def api_sequence(seq_id):
    if stream_index_data is None:
        return jsonify({"error": stream_index_error}), 503
    target = STREAM_DIR / (seq_id + ".json")
    if target.exists() is False:
        return jsonify({"error": "unknown sequence: {}".format(seq_id)}), 404
    return send_from_directory(STREAM_DIR, seq_id + ".json")


SAMPLE_INFO = [
    {"name": "pinhole", "label": "핀홀 있는 프레임", "file": "pinhole.jpg", "hint": "종횡비/응집체 걸림 판정 재현"},
    {"name": "crack", "label": "표면 크랙 프레임", "file": "crack.jpg", "hint": "A3 이상 비율이 크게 뜨는 케이스"},
    {"name": "clean", "label": "정상 프레임", "file": "clean.jpg", "hint": "검출·이상 모두 낮게 나오는 케이스"},
]

STAGE_INFO = [
    {"num": "①", "title": "A3 이상 탐지", "detail": "64×64 cell 밝기 표준편차 (patch 480×640 당 70 cell) → 임계 초과 cell 비율",
     "expected": "≈ 8 ms/patch (0.11 ms/cell × 70 cell)"},
    {"num": "②", "title": "YOLO 검출", "detail": "runs/pinhole_v1/weights/best.pt · conf ≥ 0.25 · CPU 추론",
     "expected": "median 47 ms/patch (§17-5 재측정)"},
    {"num": "②'", "title": "세그 (crack·delam)", "detail": "U-Net + ResNet34 (§20) · runs/semantic/seed0/best.pt · CPU 추론. delam 은 §20-5 안정성 한계로 참고",
     "expected": "median 450 ms/patch (§21-1, CPU batch=1). GPU 측정치 없음"},
    {"num": "③", "title": "형태 분석", "detail": "각 박스의 종횡비 · roundness 계산 후 defect_map.pinhole.diagnostic_rule 참조",
     "expected": "≈ 1 ms/patch"},
    {"num": "④", "title": "Advisor 판정", "detail": "관측 / 해석 / 참조 3층 규칙 기반 리포트",
     "expected": "규칙 기반 (0 ms)"},
]


def get_available_samples():
    available = []
    for info in SAMPLE_INFO:
        path = SAMPLES_DIR / info["file"]
        entry = dict(info)
        entry["available"] = path.exists()
        available.append(entry)
    return available


# --- /inspect ---

@app.route("/inspect", methods=["GET", "POST"])
def inspect_page():
    samples = get_available_samples()

    if request.method == "GET":
        return render_template(
            "inspect.html",
            model_ready=(yolo_model is not None),
            model_error=yolo_load_error,
            patch_threshold=get_patch_threshold(),
            result=None,
            samples=samples,
            stages=STAGE_INFO,
        )

    if yolo_model is None:
        return render_template(
            "inspect.html",
            model_ready=False,
            model_error=yolo_load_error,
            patch_threshold=get_patch_threshold(),
            result=None,
            samples=samples,
            stages=STAGE_INFO,
        )

    sample_name = request.form.get("sample")
    if sample_name is not None and sample_name != "":
        source_upload = build_sample_pseudo_upload(sample_name)
        if source_upload is None:
            return render_template(
                "inspect.html",
                model_ready=True,
                model_error=None,
                patch_threshold=get_patch_threshold(),
                result={"error": "샘플이 없다: {}. `python 18_prepare_samples.py` 를 먼저 실행하라.".format(sample_name)},
                samples=samples,
                stages=STAGE_INFO,
            )
        upload = source_upload
    else:
        upload = request.files.get("image")
        if upload is None or upload.filename == "":
            return render_template(
                "inspect.html",
                model_ready=True,
                model_error=None,
                patch_threshold=get_patch_threshold(),
                result={"error": "이미지 파일이 첨부되지 않았습니다."},
                samples=samples,
                stages=STAGE_INFO,
            )

    result_payload = run_inspection(upload)
    return render_template(
        "inspect.html",
        model_ready=True,
        model_error=None,
        patch_threshold=get_patch_threshold(),
        result=result_payload,
        samples=samples,
        stages=STAGE_INFO,
    )


class SampleUpload:
    """샘플 파일을 request.files 처럼 다루기 위한 얇은 래퍼."""

    def __init__(self, source_path):
        self.source_path = source_path
        self.filename = source_path.name

    def save(self, destination):
        import shutil
        shutil.copyfile(str(self.source_path), str(destination))


def build_sample_pseudo_upload(name):
    for info in SAMPLE_INFO:
        if info["name"] == name:
            path = SAMPLES_DIR / info["file"]
            if path.exists() is False:
                return None
            return SampleUpload(path)
    return None


@app.route("/detection", methods=["GET", "POST"])
def detection_alias():
    return redirect(url_for("inspect_page"))


def run_inspection(upload):
    import cv2
    from PIL import Image, ImageDraw

    timestamp = int(time.time() * 1000)
    orig_name = "upload_{}.png".format(timestamp)
    anno_name = "annotated_{}.png".format(timestamp)
    heat_name = "heatmap_{}.png".format(timestamp)
    seg_name = "seg_{}.png".format(timestamp)
    orig_path = UPLOADS_DIR / orig_name
    anno_path = UPLOADS_DIR / anno_name
    heat_path = UPLOADS_DIR / heat_name
    seg_path = UPLOADS_DIR / seg_name

    upload.save(str(orig_path))

    image_pil = Image.open(str(orig_path)).convert("RGB")
    image_np = np.array(image_pil)

    # 1) 이상 탐지 (A3 밝기 표준편차)
    threshold = get_patch_threshold()
    anomaly_stage = run_anomaly_stage(image_np, threshold)
    save_heatmap(image_pil, anomaly_stage["positions"], anomaly_stage["scores"], threshold, heat_path)

    # 2) YOLO 검출
    detect_stage = run_detect_stage(image_np, anno_path, image_pil.copy())

    # 2') 시맨틱 세그 (crack·delam 픽셀 오버레이) — 요청당 1 회 (§21-5)
    seg_stage = run_seg_stage(image_np, image_pil, seg_path)

    # 3) 형태 분석 (defect_map 규칙 적용)
    shape_stage = run_shape_stage(detect_stage["detections"], image_np)

    # 4) Advisor 판정 (관측 / 해석 / 참조 3층)
    advisor_stage = build_advisor(anomaly_stage, detect_stage, shape_stage, threshold)

    payload = {
        "orig_url": url_for("static", filename="uploads/" + orig_name),
        "anno_url": url_for("static", filename="uploads/" + anno_name),
        "heat_url": url_for("static", filename="uploads/" + heat_name),
        "seg_url": url_for("static", filename="uploads/" + seg_name) if seg_stage["available"] else None,
        "image_size": {"w": image_pil.width, "h": image_pil.height},
        "anomaly": anomaly_stage_for_client(anomaly_stage, threshold),
        "detect": detect_stage_for_client(detect_stage),
        "seg": seg_stage_for_client(seg_stage),
        "shape": shape_stage,
        "advisor": advisor_stage,
    }
    return payload


def run_anomaly_stage(image_np, threshold):
    start = time.time()
    gray = np.mean(image_np.astype(np.float64), axis=2)
    scores, positions = score_patches_std_gray(gray)
    anomalous_indices = []
    for i in range(len(scores)):
        if scores[i] >= threshold:
            anomalous_indices.append(i)
    elapsed_ms = (time.time() - start) * 1000.0
    total = len(scores)
    anomalous = len(anomalous_indices)
    if total > 0:
        ratio = anomalous / float(total)
    else:
        ratio = 0.0
    # elapsed_ms 는 patch (480x640) 하나에 대한 총 처리시간. total 은 그 안의 cell 수 (=70).
    # 따라서 elapsed_ms / total 은 실제로 per_cell_ms 다. §16-0 용어 확정.
    return {
        "scores": scores,
        "positions": positions,
        "anomalous_indices": anomalous_indices,
        "total_cells": total,
        "anomalous_cells": anomalous,
        "ratio": ratio,
        "elapsed_ms_per_patch": elapsed_ms,
        "per_cell_ms": elapsed_ms / max(1, total),
    }


def anomaly_stage_for_client(stage, threshold):
    return {
        "total_cells": stage["total_cells"],
        "anomalous_cells": stage["anomalous_cells"],
        "ratio": stage["ratio"],
        "threshold": threshold,
        "elapsed_ms_per_patch": round(stage["elapsed_ms_per_patch"], 2),
        "per_cell_ms": round(stage["per_cell_ms"], 3),
    }


def save_heatmap(image_pil, positions, scores, threshold, out_path):
    from PIL import Image, ImageDraw
    overlay = Image.new("RGBA", image_pil.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for i in range(len(positions)):
        if scores[i] < threshold:
            continue
        x, y, w, h = positions[i]
        draw.rectangle(
            [(x, y), (x + w, y + h)],
            fill=(220, 40, 40, 90),
            outline=(220, 40, 40, 200),
            width=1,
        )
    combined = Image.alpha_composite(image_pil.convert("RGBA"), overlay)
    combined.convert("RGB").save(str(out_path))


def run_detect_stage(image_np, anno_path, image_pil_copy):
    """YOLO 검출 + 오버레이 저장.

    시간 계측:
    - infer_ms: ultralytics results.speed['inference'] (순수 모델 forward)
    - pre_post_ms: 모델 호출의 preprocess + postprocess + 우리쪽 파싱/그리기
    분리해서 반환한다. 발표에서 "0.11 ms/cell" 같은 A3 순수 STD 수치와
    구분되도록 표기하기 위함이다 (A3 는 patch 당 70 cell 이므로 patch 당 약 8 ms).
    """
    from PIL import ImageDraw

    predict_start = time.time()
    predictions = yolo_model.predict(source=image_np, conf=0.25, verbose=False)
    predict_end = time.time()
    first = predictions[0]

    detections = []
    if first.boxes is not None:
        boxes_xyxy = first.boxes.xyxy.cpu().numpy()
        confidences = first.boxes.conf.cpu().numpy()
        for i in range(len(boxes_xyxy)):
            x1 = int(round(float(boxes_xyxy[i][0])))
            y1 = int(round(float(boxes_xyxy[i][1])))
            x2 = int(round(float(boxes_xyxy[i][2])))
            y2 = int(round(float(boxes_xyxy[i][3])))
            width = max(1, x2 - x1)
            height = max(1, y2 - y1)
            aspect = max(width, height) / max(1, min(width, height))
            detection_record = {
                "index": i + 1,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "w": width,
                "h": height,
                "conf": round(float(confidences[i]), 3),
                "aspect": round(float(aspect), 3),
            }
            detections.append(detection_record)

    draw = ImageDraw.Draw(image_pil_copy)
    for detection in detections:
        draw.rectangle(
            [(detection["x1"], detection["y1"]), (detection["x2"], detection["y2"])],
            outline=(220, 40, 40),
            width=2,
        )
        label_text = "#{} p={:.2f}".format(detection["index"], detection["conf"])
        draw.text(
            (detection["x1"] + 3, detection["y1"] + 3),
            label_text,
            fill=(220, 40, 40),
        )
    image_pil_copy.save(str(anno_path))
    stage_end = time.time()

    predict_bundle_ms = (predict_end - predict_start) * 1000.0
    draw_bundle_ms = (stage_end - predict_end) * 1000.0

    speed_dict = getattr(first, "speed", None) or {}
    infer_ms = float(speed_dict.get("inference", 0.0))
    # ultralytics 가 보고한 preprocess/postprocess 는 predict_bundle 에 포함되어 있다.
    # 나머지 (predict_bundle - infer) + draw_bundle 을 '전후처리' 로 묶는다.
    pre_post_ms = (predict_bundle_ms - infer_ms) + draw_bundle_ms

    return {
        "detections": detections,
        "infer_ms": infer_ms,
        "pre_post_ms": pre_post_ms,
        "total_ms": predict_bundle_ms + draw_bundle_ms,
    }


def detect_stage_for_client(stage):
    return {
        "n_detections": len(stage["detections"]),
        "detections": stage["detections"],
        "infer_ms": round(stage["infer_ms"], 2),
        "pre_post_ms": round(stage["pre_post_ms"], 2),
        "total_ms": round(stage["total_ms"], 2),
    }


# --- 시맨틱 세그 (§20, §21) ---

SEG_IMG_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
SEG_IMG_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def run_seg_stage(image_np, image_pil, seg_out_path):
    """
    U-Net + ResNet34 로 crack·delam 픽셀 세그. 오버레이 PNG 저장.
    요청당 1 회 추론 (§21-5, CPU 450 ms/patch).
    캐시·비동기 없음.
    """
    from PIL import Image, ImageDraw

    if seg_model is None:
        return {
            "available": False,
            "error": seg_load_error or "seg model 미로드",
            "elapsed_ms": 0.0,
            "crack_ratio": 0.0,
            "delam_ratio": 0.0,
        }

    import torch as _torch

    start = time.time()
    # 480x640 그대로 전제. 다른 크기 업로드는 예외 처리
    img_f = image_np.astype(np.float32) / 255.0
    img_f = (img_f - SEG_IMG_MEAN) / SEG_IMG_STD
    tensor = _torch.from_numpy(img_f.transpose(2, 0, 1)).unsqueeze(0).float()

    with _torch.no_grad():
        logits = seg_model(tensor)
    pred = logits.argmax(dim=1).cpu().numpy()[0]  # (H, W)
    elapsed_ms = (time.time() - start) * 1000.0

    n_crack = int(np.sum(pred == 1))
    n_delam = int(np.sum(pred == 2))
    total = pred.size
    crack_ratio = n_crack / total
    delam_ratio = n_delam / total

    # 오버레이 저장: 원본 위에 crack(빨강)·delam(주황) 반투명
    overlay = image_pil.convert("RGBA").copy()
    color_layer = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    color_np = np.zeros((pred.shape[0], pred.shape[1], 4), dtype=np.uint8)
    color_np[pred == 1] = [220, 40, 40, 130]      # crack red
    color_np[pred == 2] = [255, 140, 0, 130]      # delam orange
    color_layer = Image.fromarray(color_np, mode="RGBA")
    combined = Image.alpha_composite(overlay, color_layer)
    combined.convert("RGB").save(str(seg_out_path))

    return {
        "available": True,
        "elapsed_ms": elapsed_ms,
        "crack_pixels": n_crack,
        "delam_pixels": n_delam,
        "total_pixels": total,
        "crack_ratio": crack_ratio,
        "delam_ratio": delam_ratio,
    }


def seg_stage_for_client(stage):
    if not stage.get("available"):
        return {
            "available": False,
            "error": stage.get("error", ""),
            "elapsed_ms": 0.0,
        }
    return {
        "available": True,
        "elapsed_ms": round(stage["elapsed_ms"], 1),
        "crack_ratio": round(stage["crack_ratio"], 5),
        "delam_ratio": round(stage["delam_ratio"], 5),
        "crack_pixels": stage["crack_pixels"],
        "delam_pixels": stage["delam_pixels"],
    }


def run_shape_stage(detections, image_np):
    start = time.time()
    if len(detections) == 0:
        elapsed_ms = (time.time() - start) * 1000.0
        return {
            "per_detection": [],
            "median_aspect": None,
            "median_roundness": None,
            "elapsed_ms": round(elapsed_ms, 2),
            "reference": collect_shape_reference(),
        }

    per_detection = []
    aspects = []
    roundnesses = []
    for detection in detections:
        crop = image_np[detection["y1"]:detection["y2"], detection["x1"]:detection["x2"]]
        roundness = compute_roundness(crop)
        per_entry = {
            "index": detection["index"],
            "aspect": detection["aspect"],
            "roundness": roundness,
        }
        per_detection.append(per_entry)
        aspects.append(detection["aspect"])
        if roundness is not None:
            roundnesses.append(roundness)

    median_aspect = median_value(aspects)
    median_roundness = None
    if len(roundnesses) > 0:
        median_roundness = median_value(roundnesses)

    elapsed_ms = (time.time() - start) * 1000.0
    if median_roundness is None:
        median_roundness_out = None
    else:
        median_roundness_out = round(median_roundness, 3)
    return {
        "per_detection": per_detection,
        "median_aspect": round(median_aspect, 3),
        "median_roundness": median_roundness_out,
        "elapsed_ms": round(elapsed_ms, 2),
        "reference": collect_shape_reference(),
    }


def collect_shape_reference():
    pinhole = defect_map["dataset_classes"]["pinhole"]
    rule = pinhole.get("diagnostic_rule", {})
    rules_summary = []
    for entry in rule.get("rules", []):
        rules_summary.append({
            "condition": entry.get("condition", ""),
            "inference": entry.get("inference", ""),
            "confidence": entry.get("confidence", "medium"),
        })
    return {
        "description": rule.get("description", ""),
        "threshold_caveat": rule.get("threshold_caveat", ""),
        "rules": rules_summary,
    }


def compute_roundness(crop_rgb):
    import cv2
    if crop_rgb is None:
        return None
    if crop_rgb.size == 0:
        return None
    if crop_rgb.ndim == 3:
        gray = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY)
    else:
        gray = crop_rgb
    threshold_value = 180
    _, binary = cv2.threshold(gray, threshold_value, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if len(contours) == 0:
        return None
    largest = None
    largest_area = 0.0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area > largest_area:
            largest_area = area
            largest = contour
    if largest is None:
        return None
    if largest_area <= 0:
        return None
    perimeter = cv2.arcLength(largest, True)
    if perimeter <= 0:
        return None
    roundness = 4.0 * np.pi * largest_area / (perimeter * perimeter)
    return round(float(roundness), 3)


def median_value(values):
    ordered = []
    for value in values:
        ordered.append(value)
    ordered.sort()
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


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
    pinhole = defect_map["dataset_classes"]["pinhole"]
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


# --- /benchmark ---

@app.route("/benchmark")
def benchmark_page():
    methods_order = []
    by_method = {}
    for row in benchmark_rows:
        method = row["method"]
        target = row["target"]
        if method not in by_method:
            by_method[method] = {"method": method}
            methods_order.append(method)
        by_method[method][target] = {
            "auroc": float(row["auroc"]),
            "fpr": float(row["fpr_at_95tpr"]),
            "n_defect": int(row["n_defect"]),
        }

    benchmark_table = []
    for method_name in methods_order:
        benchmark_table.append(by_method[method_name])

    best_method = None
    best_auroc = -1.0
    for entry in benchmark_table:
        drying = entry.get("drying_defect")
        if drying is None:
            continue
        if drying["auroc"] > best_auroc:
            best_auroc = drying["auroc"]
            best_method = entry["method"]

    robustness_table = []
    for row in robustness_rows:
        robustness_table.append({
            "perturbation": row["perturbation"],
            "a3_auroc": float(row["a3_auroc"]),
            "a3_fpr_fixed": float(row["a3_fixed_threshold_fpr"]),
            "b2_auroc": float(row["b2_auroc"]),
            "b2_fpr_fixed": float(row["b2_fixed_threshold_fpr"]),
        })

    targets_order = ["surface_crack", "delamination", "pinhole", "drying_defect"]

    return render_template(
        "benchmark.html",
        benchmark=benchmark_table,
        robustness=robustness_table,
        targets_order=targets_order,
        best_method=best_method,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
