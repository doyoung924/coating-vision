"""
31. YOLO-seg 데이터셋 설계 확인

배경 (§18 → ②):
  ③ 을 건너뛰고 ② 세그멘테이션으로 진행. 학습 전에 데이터셋 변환 가능성과
  학습 타당성 지표를 확인한다. 변환·학습은 아직 시작하지 않는다.

조사 항목:
  1. 폴리곤 추출 가능성
     - ch0/ch1/ch2 각 채널 연결 성분의 외곽 폴리곤 (cv2.findContours, RETR_CCOMP)
     - 성분당 원본 점 개수 (CHAIN_APPROX_NONE) 및 압축 후 점 개수 (SIMPLE)
     - 구멍(hole) 있는 성분 존재 여부 (RETR_CCOMP hierarchy)
  2. 클래스별 성분 통계 (2,227 patch 전량)
     - 성분 수, 면적 분포
     - 충전율 = 성분 면적 / bbox 면적 (다중 클래스 검출 타당성 판단)
     - 크랙 종횡비 분포
  3. 학습/검증/테스트 분할안
     - R7/700 은 test hold-out (§15-7 도메인 이전성 관측)
     - 나머지 7 시퀀스는 원본 프레임 단위로 train/val 분할
     - 형제 patch 는 자동으로 함께 묶임
  4. 환경 (기재)

새 검출 기법 도입 없음. cv2 findContours 는 마스크 시각화 도구.

산출:
  - results_seg_component_stats.csv (성분 단위 상세)
  - stdout 요약 표
"""

import csv
import os
import re
import sys
import numpy as np
from PIL import Image
import cv2
from collections import defaultdict


LABELS_CSV = "classification/labels.csv"
MASK_DIR = "segmentation/masks"
OUT_CSV = "results_seg_component_stats.csv"

CH_NAMES = ["crack", "delam", "pinhole"]
BAND_Y_MIN = 448   # A3 반응 영역 밖. 정보용


def parse_original(name):
    m = re.match(r"^(R\d+)-?(\d+)um[-_](.+?)_frame_(\d+)", name)
    if not m: return None
    return {
        "run_id": m.group(1),
        "coating_gap": int(m.group(2)),
        "position": m.group(3),
        "frame_number": int(m.group(4)),
    }


def load_metadata():
    result = {}
    with open(LABELS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed = parse_original(row["original_file_name"])
            if parsed is None: continue
            stem = row["file_name"]
            if stem.endswith(".jpg"): stem = stem[:-4]
            parsed["stem"] = stem
            result[stem] = parsed
    return result


def analyze_channel(binary):
    """
    이진 마스크(0/255)에서 연결 성분 추출 후 각 성분에 대해:
      - area, bbox_w/h, fill_rate
      - 외곽 폴리곤 점 개수 (원본 NONE, 압축 SIMPLE)
      - 내부 구멍 개수, 구멍 점 개수 총합
    반환: 성분별 dict 리스트
    """
    # 8-연결 성분 라벨링
    n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        binary, connectivity=8)
    components = []
    idx = 1
    while idx < n_labels:
        area = int(stats[idx, cv2.CC_STAT_AREA])
        x = int(stats[idx, cv2.CC_STAT_LEFT])
        y = int(stats[idx, cv2.CC_STAT_TOP])
        w = int(stats[idx, cv2.CC_STAT_WIDTH])
        h = int(stats[idx, cv2.CC_STAT_HEIGHT])
        bbox_area = max(1, w * h)
        fill_rate = area / bbox_area
        # 성분 단독 mask 만들어 hierarchy 획득
        comp_mask = ((labels == idx).astype(np.uint8)) * 255
        # NONE: 원본 점, SIMPLE: 압축
        contours_none, hierarchy_none = cv2.findContours(
            comp_mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        contours_simple, _ = cv2.findContours(
            comp_mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        n_outer_none = 0
        n_hole_none = 0
        n_hole_count = 0
        if hierarchy_none is not None:
            hier = hierarchy_none[0]
            for j in range(len(contours_none)):
                pts = len(contours_none[j])
                if hier[j][3] == -1:
                    n_outer_none += pts
                else:
                    n_hole_none += pts
                    n_hole_count += 1
        n_outer_simple = 0
        n_hole_simple = 0
        for j in range(len(contours_simple)):
            n_outer_simple += len(contours_simple[j])
        # aspect (bbox 기준)
        aspect = max(w, h) / max(1, min(w, h))

        # band 판정: 성분의 bbox 가 완전히 y >= BAND 영역 안?
        band_only = (y >= BAND_Y_MIN)

        components.append({
            "area": area,
            "bbox_x": x, "bbox_y": y, "bbox_w": w, "bbox_h": h,
            "fill_rate": round(fill_rate, 4),
            "aspect": round(aspect, 3),
            "n_outer_pts_none": n_outer_none,
            "n_outer_pts_simple": n_outer_simple,
            "n_hole_count": n_hole_count,
            "n_hole_pts": n_hole_none,
            "band_only": 1 if band_only else 0,
        })
        idx += 1
    return components


def summarize(values, name, indent="  "):
    a = np.array(values, dtype=float)
    if len(a) == 0:
        print(indent + name + " N=0")
        return
    q = np.percentile(a, [0, 25, 50, 75, 90, 100])
    print("{}{:<28} N={:>6}  min={:>8.2f}  p25={:>8.2f}  p50={:>8.2f}  p75={:>8.2f}  p90={:>8.2f}  max={:>8.2f}".format(
        indent, name, len(a), q[0], q[1], q[2], q[3], q[4], q[5]))


def main():
    if os.path.exists(OUT_CSV):
        print("이미 존재:", OUT_CSV, "— 사용자가 옮긴 뒤 재실행할 것.")
        sys.exit(1)

    print("=" * 92)
    print("31. YOLO-seg 데이터셋 설계 확인")
    print("=" * 92)

    metadata = load_metadata()
    print("labels.csv 프레임 수:", len(metadata))
    print()

    all_rows = []
    class_components = defaultdict(list)   # ch_name -> [component dicts]
    print("2,227 patch 스캔 중...")
    processed = 0
    for stem in metadata:
        record = metadata[stem]
        mask_path = os.path.join(MASK_DIR, stem + ".png")
        if os.path.exists(mask_path) is False:
            continue
        mask = np.array(Image.open(mask_path))
        for ci, ch_name in enumerate(CH_NAMES):
            binary = ((mask[..., ci] > 0).astype(np.uint8)) * 255
            components = analyze_channel(binary)
            for c in components:
                row = {
                    "stem": stem,
                    "run_id": record["run_id"],
                    "coating_gap": record["coating_gap"],
                    "position": record["position"],
                    "frame_number": record["frame_number"],
                    "class": ch_name,
                    **c,
                }
                all_rows.append(row)
                class_components[ch_name].append(c)
        processed += 1
        if processed % 500 == 0:
            print("  진행 {}/{}".format(processed, len(metadata)))

    print("스캔 완료. 총 성분:", len(all_rows))
    print()

    # 1. 클래스별 성분 통계
    print("=" * 92)
    print("2-2. 클래스별 성분 통계 (2,227 patch 전량)")
    print("=" * 92)
    for ch_name in CH_NAMES:
        comps = class_components[ch_name]
        print()
        print("  [{}]  성분 수 = {}".format(ch_name, len(comps)))
        if len(comps) == 0:
            continue
        areas = [c["area"] for c in comps]
        fills = [c["fill_rate"] for c in comps]
        aspects = [c["aspect"] for c in comps]
        holes = [c["n_hole_count"] for c in comps]
        summarize(areas, "면적 (px)")
        summarize(fills, "충전율 (area/bbox)")
        summarize(aspects, "종횡비 (bbox 기준)")
        # 구멍 성분 요약
        n_with_hole = sum(1 for h in holes if h > 0)
        print("  {:<28} 구멍 있는 성분 = {} / {}  ({:.4f}), 총 구멍 수 = {}".format(
            "구멍 통계", n_with_hole, len(comps),
            n_with_hole / max(1, len(comps)), sum(holes)))

    print()

    # 2. 폴리곤 점 개수 분포 (전 클래스)
    print("=" * 92)
    print("2-1. 폴리곤 외곽 점 개수 분포 (전 성분)")
    print("=" * 92)
    for ch_name in CH_NAMES:
        comps = class_components[ch_name]
        if len(comps) == 0: continue
        pts_none = [c["n_outer_pts_none"] for c in comps]
        pts_simple = [c["n_outer_pts_simple"] for c in comps]
        print()
        print("  [{}]".format(ch_name))
        summarize(pts_none, "외곽 점 (원본 NONE)")
        summarize(pts_simple, "외곽 점 (압축 SIMPLE)")

    print()

    # 3. 분할안 계산
    print("=" * 92)
    print("2-3. 학습/검증/테스트 분할안")
    print("=" * 92)
    frame_by_seq = defaultdict(set)
    patch_count_by_seq = defaultdict(int)
    for stem in metadata:
        record = metadata[stem]
        key = (record["run_id"], record["coating_gap"], record["position"])
        frame_by_seq[key].add(record["frame_number"])
        patch_count_by_seq[key] += 1

    total_frames = 0
    total_patches = 0
    print("  {:<40}{:>10}{:>10}".format("시퀀스", "프레임", "patch"))
    for key in sorted(frame_by_seq.keys()):
        n_frames = len(frame_by_seq[key])
        n_patches = patch_count_by_seq[key]
        print("  {:<40}{:>10}{:>10}".format(str(key), n_frames, n_patches))
        total_frames += n_frames
        total_patches += n_patches
    print("  " + "-" * 60)
    print("  {:<40}{:>10}{:>10}".format("전체", total_frames, total_patches))
    print()

    r7_key = ("R7", 700, "middle")
    r7_frames = len(frame_by_seq.get(r7_key, set()))
    r7_patches = patch_count_by_seq.get(r7_key, 0)
    print("  test hold-out 후보 (R7/700/middle): 프레임 {}, patch {}".format(
        r7_frames, r7_patches))

    other_frames = total_frames - r7_frames
    other_patches = total_patches - r7_patches
    print("  나머지 7 시퀀스: 프레임 {}, patch {}".format(other_frames, other_patches))

    # train/val 분할안 (80:20, 프레임 단위)
    val_frames = int(round(other_frames * 0.2))
    train_frames = other_frames - val_frames
    print("  분할안 (프레임 80:20):")
    print("    train: 약 {} 프레임".format(train_frames))
    print("    val  : 약 {} 프레임".format(val_frames))
    print("    test : {} 프레임 (R7/700 전체)".format(r7_frames))
    print()

    # 4. CSV 저장
    field_names = [
        "stem", "run_id", "coating_gap", "position", "frame_number", "class",
        "area", "bbox_x", "bbox_y", "bbox_w", "bbox_h",
        "fill_rate", "aspect",
        "n_outer_pts_none", "n_outer_pts_simple",
        "n_hole_count", "n_hole_pts", "band_only",
    ]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=field_names)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)
    print("저장:", OUT_CSV, "(", len(all_rows), "행 )")

    # 5. 환경 (기재)
    print()
    print("=" * 92)
    print("2-4. 환경")
    print("=" * 92)
    print("  로컬 GPU  : MX450 (2GB) - 학습 불가 (CLAUDE.md 명시)")
    print("  학습 환경 : RunPod RTX 3090 (§CLAUDE.md 개발환경 절)")
    print("  데이터 규모 (업로드 대상 대략치):")
    total_img_mb = 2227 * 480 * 640 * 3 / 1024 / 1024
    total_mask_mb = 2227 * 480 * 640 * 4 / 1024 / 1024
    print("    이미지 raw (480x640x3 uint8) : ~{:.0f} MB".format(total_img_mb))
    print("    마스크 raw (480x640x4 uint8) : ~{:.0f} MB".format(total_mask_mb))
    print("    실제 tar (JPEG + PNG 압축)   : 300~500 MB 예상")
    print("    폴리곤 라벨 (텍스트)         : 수 MB")


main()
