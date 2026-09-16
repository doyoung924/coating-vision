"""
inspect blueprint — /inspect 파이프라인 + /detection 리다이렉트.

§3-3: @login_required 적용. POST 성공 후 결과를 DB 에 저장.
저장 실패는 검사 결과 표시를 막지 않는다 (flash 로 알리고 결과는 그대로 렌더).
"""

import shutil

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import config
from .. import store
from ..auth_utils import login_required
from ..logging_config import get_logger
from ..ml import loader as ml_loader
from ..services import auth as auth_service
from ..services import inspection_store
from ..services import pipeline as pipeline_service


log = get_logger(__name__)


bp = Blueprint("inspect", __name__)


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


class SampleUpload:
    """샘플 파일을 request.files 처럼 다루기 위한 얇은 래퍼."""

    def __init__(self, source_path):
        self.source_path = source_path
        self.filename = source_path.name

    def save(self, destination):
        shutil.copyfile(str(self.source_path), str(destination))


def build_sample_pseudo_upload(name):
    for info in SAMPLE_INFO:
        if info["name"] == name:
            path = config.SAMPLES_DIR / info["file"]
            if path.exists() is False:
                return None
            return SampleUpload(path)
    return None


def get_available_samples():
    available = []
    for info in SAMPLE_INFO:
        path = config.SAMPLES_DIR / info["file"]
        entry = dict(info)
        entry["available"] = path.exists()
        available.append(entry)
    return available


@bp.route("/inspect", methods=["GET", "POST"])
@login_required
def inspect_page():
    samples = get_available_samples()

    if request.method == "GET":
        yolo_model = ml_loader.get_yolo()
        return render_template(
            "inspect.html",
            model_ready=(yolo_model is not None),
            model_error=ml_loader.get_yolo_error(),
            patch_threshold=store.get_patch_threshold(),
            result=None,
            samples=samples,
            stages=STAGE_INFO,
        )

    yolo_model = ml_loader.get_yolo()
    if yolo_model is None:
        return render_template(
            "inspect.html",
            model_ready=False,
            model_error=ml_loader.get_yolo_error(),
            patch_threshold=store.get_patch_threshold(),
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
                patch_threshold=store.get_patch_threshold(),
                result={"error": "샘플이 없다: {}. `python 18_prepare_samples.py` 를 먼저 실행하라.".format(sample_name)},
                samples=samples,
                stages=STAGE_INFO,
            )
        upload = source_upload
        source_kind = "sample"
    else:
        upload = request.files.get("image")
        if upload is None or upload.filename == "":
            return render_template(
                "inspect.html",
                model_ready=True,
                model_error=None,
                patch_threshold=store.get_patch_threshold(),
                result={"error": "이미지 파일이 첨부되지 않았습니다."},
                samples=samples,
                stages=STAGE_INFO,
            )
        source_kind = "upload"

    # §3-4 콜드 스타트 감지: 모델 로드 상태를 pipeline 실행 전에 캡처.
    # 두 모델 중 하나라도 이번 요청에서 로드되면 IS_COLD_START=1.
    yolo_preloaded = ml_loader.is_yolo_loaded()
    seg_preloaded = ml_loader.is_seg_loaded()

    result_payload = pipeline_service.run_inspection(upload)

    yolo_cold = not yolo_preloaded and ml_loader.is_yolo_loaded()
    seg_cold = not seg_preloaded and ml_loader.is_seg_loaded()
    is_cold_start = yolo_cold or seg_cold

    # DB 저장. 실패해도 결과 표시는 유지 (§3-3 원칙).
    current_user = auth_service.get_current_user()
    if current_user is not None:
        try:
            file_name = getattr(upload, "filename", None) or "unknown"
            inspection_id = inspection_store.save_inspection(
                result=result_payload,
                user_id=current_user["id"],
                file_name=file_name,
                source=source_kind,
                is_cold_start=is_cold_start,
            )
            result_payload["inspection_id"] = inspection_id
            result_payload["is_cold_start"] = is_cold_start
            log.info(
                "Inspection saved id=%d user_id=%s file=%s source=%s cold=%d "
                "a3_ratio=%.4f seg_crack=%s pinhole=%d",
                inspection_id, current_user["id"], file_name, source_kind,
                1 if is_cold_start else 0,
                float(result_payload["anomaly"]["ratio"] if result_payload.get("anomaly") else 0),
                "None" if not (result_payload.get("seg") or {}).get("available")
                    else "%.4f" % result_payload["seg"]["crack_ratio"],
                int((result_payload.get("detect") or {}).get("n_detections") or 0),
            )
        except Exception as exc:
            log.error("Inspection save failed user_id=%s: %s: %s",
                      current_user["id"], type(exc).__name__, exc)
            flash("검사 결과 DB 저장 실패 (결과는 화면에만 표시): {}: {}".format(
                type(exc).__name__, exc,
            ), "error")

    return render_template(
        "inspect.html",
        model_ready=True,
        model_error=None,
        patch_threshold=store.get_patch_threshold(),
        result=result_payload,
        samples=samples,
        stages=STAGE_INFO,
    )


@bp.route("/detection", methods=["GET", "POST"])
def detection_alias():
    return redirect(url_for("inspect.inspect_page"))
