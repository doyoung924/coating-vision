"""
모델 lazy 로더 (§2단계 (b)).

legacy app.py:57-86 의 로드 로직과 예외 처리를 그대로 옮겼다.
차이점: import 시점이 아니라 최초 get_*() 호출 시점에 로드된다.
실패한 경우 에러 문자열을 슬롯에 보관하고 재시도하지 않는다 (한 번의 실패로 확정).
"""

from .. import config


# --- YOLO ---
# legacy: yolo_model, yolo_load_error 두 개의 module-level 슬롯

_yolo_model = None
_yolo_error = None
_yolo_loaded = False


def get_yolo():
    """YOLO 모델을 반환한다. 로드 실패 시 None. 에러 사유는 get_yolo_error()."""
    global _yolo_model, _yolo_error, _yolo_loaded
    if _yolo_loaded:
        return _yolo_model
    _yolo_loaded = True
    try:
        from ultralytics import YOLO
        if config.YOLO_WEIGHTS.exists():
            _yolo_model = YOLO(str(config.YOLO_WEIGHTS))
        else:
            _yolo_error = "weights file not found: {}".format(config.YOLO_WEIGHTS)
    except Exception as exc:
        _yolo_error = "YOLO 로드 실패: {}".format(exc)
    return _yolo_model


def get_yolo_error():
    # 로드 시도 전이라 아직 None 이면 강제로 시도하지 않는다 (legacy 는 import 시 시도).
    # inspect_page 는 항상 get_yolo() 를 먼저 호출하므로 여기서 시도 강제 불필요.
    return _yolo_error


def is_yolo_loaded():
    """YOLO 가 이미 로드되었는지 (모델 객체가 메모리에 있는지) 반환.
    §3-4 IS_COLD_START 판정용."""
    return _yolo_model is not None


# --- 시맨틱 세그 (§20) ---
# legacy: seg_model, seg_load_error 두 개의 module-level 슬롯

_seg_model = None
_seg_error = None
_seg_loaded = False


def get_seg():
    """세그 모델을 반환한다. 로드 실패 시 None. 에러 사유는 get_seg_error()."""
    global _seg_model, _seg_error, _seg_loaded
    if _seg_loaded:
        return _seg_model
    _seg_loaded = True
    try:
        import torch
        import segmentation_models_pytorch as smp
        if config.SEG_WEIGHTS.exists():
            _seg_model = smp.Unet(
                encoder_name="resnet34", encoder_weights=None,
                in_channels=3, classes=3,
            )
            state_dict = torch.load(str(config.SEG_WEIGHTS), map_location="cpu", weights_only=True)
            _seg_model.load_state_dict(state_dict)
            _seg_model.eval()
        else:
            _seg_error = "seg weights 없음: {}".format(config.SEG_WEIGHTS)
    except Exception as exc:
        _seg_error = "seg 로드 실패: {}".format(exc)
    return _seg_model


def get_seg_error():
    return _seg_error


def is_seg_loaded():
    """세그 모델이 이미 로드되었는지 반환. §3-4 IS_COLD_START 판정용."""
    return _seg_model is not None
