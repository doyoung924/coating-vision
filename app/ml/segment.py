"""
U-Net + ResNet34 시맨틱 세그 (§20, §21) — §2단계 (b).

legacy app.py:576-653 를 그대로 옮겼다. 계산 로직 미변경.
차이: 전역 seg_model 참조를 loader.get_seg() 호출로 교체.
seg_out_path 는 이미 legacy 시점부터 인자로 받는다. url_for 의존성 없음.
"""

import time

import numpy as np

from . import loader


# ImageNet 정규화 상수 (legacy 578-579 그대로)
SEG_IMG_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
SEG_IMG_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def run_seg_stage(image_np, image_pil, seg_out_path):
    """
    U-Net + ResNet34 로 crack·delam 픽셀 세그. 오버레이 PNG 저장.
    요청당 1 회 추론 (§21-5, CPU 450 ms/patch).
    캐시·비동기 없음.
    """
    from PIL import Image

    seg_model = loader.get_seg()
    if seg_model is None:
        return {
            "available": False,
            "error": loader.get_seg_error() or "seg model 미로드",
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
