"""
정적 산출물 (JSON/CSV) lazy 접근 계층.

원칙 (§2단계 지시):
  - 임포트 시 파일 읽지 않는다. 최초 호출 시 로드 + 캐시
  - 로드 실패 시 에러를 별도 슬롯에 보관 (legacy 의 stream_index_error 동작 유지)
  - defect_map[dataset_classes][pinhole] 하드코딩을 get_pinhole_class() 하나로 통일
  - HEADLINE dict 조립도 여기서 (advisor · kpi · benchmark 3 소스 취합)

접근 함수 (모두 lazy, cache):
  get_advisor(), get_defect_map(), get_kpi(),
  get_benchmark_rows(), get_robustness_rows(),
  get_stream_index() → (data, error) 튜플,
  get_pinhole_class() → defect_map["dataset_classes"]["pinhole"],
  get_patch_threshold(),
  find_a3_drying_auroc(),
  count_incapable(),
  get_headline().

계산 로직은 legacy app.py 130-172 를 그대로 옮겼다. 수정 없음.
"""

import csv
import json

from . import config


# ===============================================================
# 내부 로더
# ===============================================================

def _load_json(path):
    with open(path, "r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


def _load_csv(path):
    rows = []
    with open(path, "r", encoding="utf-8") as file_handle:
        reader = csv.DictReader(file_handle)
        for row in reader:
            rows.append(row)
    return rows


# 캐시 슬롯. 초기값 None = 아직 로드 안 함. 로드 후 값 저장.
_advisor = None
_defect_map = None
_kpi = None
_benchmark_rows = None
_robustness_rows = None
_stream_index = None   # (data, error) 튜플. data 는 dict 또는 None
_headline = None


# ===============================================================
# JSON 3종
# ===============================================================

def get_advisor():
    global _advisor
    if _advisor is None:
        _advisor = _load_json(config.PROJECT_ROOT / "advisor_report.json")
    return _advisor


def get_defect_map():
    global _defect_map
    if _defect_map is None:
        _defect_map = _load_json(config.PROJECT_ROOT / "defect_map.json")
    return _defect_map


def get_kpi():
    global _kpi
    if _kpi is None:
        _kpi = _load_json(config.PROJECT_ROOT / "kpi_headline.json")
    return _kpi


# ===============================================================
# CSV 2종
# ===============================================================

def get_benchmark_rows():
    global _benchmark_rows
    if _benchmark_rows is None:
        _benchmark_rows = _load_csv(config.PROJECT_ROOT / "results_benchmark.csv")
    return _benchmark_rows


def get_robustness_rows():
    global _robustness_rows
    if _robustness_rows is None:
        _robustness_rows = _load_csv(config.PROJECT_ROOT / "results_robustness.csv")
    return _robustness_rows


# ===============================================================
# 스트림 캐시 (legacy 82-89 동작 유지)
# ===============================================================

def get_stream_index():
    """반환: (data, error). 최초 호출 시 로드 시도. 파일 없으면 data=None,
    error 에 사유 문자열."""
    global _stream_index
    if _stream_index is None:
        try:
            data = _load_json(config.STREAM_INDEX)
            _stream_index = (data, None)
        except FileNotFoundError:
            _stream_index = (
                None,
                "stream_data/index.json 이 없음. "
                "`python 17_precompute_detections.py` 를 먼저 실행하세요.",
            )
    return _stream_index


# ===============================================================
# defect_map 접근 통일 (§2단계 지시 3)
# ===============================================================

def get_pinhole_class():
    """legacy 775·911·929·942 근처의 defect_map["dataset_classes"]["pinhole"]
    참조를 하나로 통일."""
    return get_defect_map()["dataset_classes"]["pinhole"]


# ===============================================================
# get_patch_threshold (legacy 226-230 · stream_index 의존)
# ===============================================================

def get_patch_threshold():
    data, _ = get_stream_index()
    if data is None:
        return 3.0
    return float(data.get("patch_config", {}).get("threshold", 3.0))


# ===============================================================
# KPI 계산 (legacy 130-172)
# ===============================================================

def find_a3_drying_auroc():
    """legacy 130-134 그대로."""
    for row in get_benchmark_rows():
        if row["method"] == "A3_std_brightness" and row["target"] == "drying_defect":
            return float(row["auroc"])
    return None


def count_incapable():
    """legacy 137-145. 내부에서 get_advisor() 호출."""
    sequences = get_advisor()["sequences"]
    total = 0
    incapable = 0
    for sequence in sequences:
        total = total + 1
        capability = sequence["observation"]["spc"]["capability"]
        if capability == "INCAPABLE":
            incapable = incapable + 1
    return incapable, total


def get_headline():
    """legacy 148-172. lazy cache. kpi + AUROC 계산 + incapable 취합."""
    global _headline
    if _headline is not None:
        return _headline

    kpi = get_kpi()
    incapable, total_sequences = count_incapable()

    _headline = {
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
        "incapable": incapable,
        "total_sequences": total_sequences,
    }
    return _headline
