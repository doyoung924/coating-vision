"""
22. 세그멘테이션 마스크 면적비 - A3 이상 탐지 연속 정답 대체

배경:
  기존 SPC 평가는 프레임별 이진 라벨(Surface_Crack 등)을 정답으로 썼다.
  그러나 CoatingVision 은 crack 기저율 0.874 로 극도로 편중되어 있어
  이진 정답 위의 lift/precision/recall 지표가 시퀀스별로 자의적이다.
  (진단: R1/600 lift 46.75, R1/700 1.20, R1/800 1.57, 산술평균이 16.51)

  세그멘테이션 마스크는 2,227 프레임 전량 존재하고 결함 면적비를
  patch 단위 연속량으로 제공한다. 이 스크립트는 마스크로부터 면적비를 뽑고,
  A3 이상 탐지 결과(results_spc.csv 의 defect_ratio) 와 상관을 계산한다.

이 판(v2)에서 바뀐 것:
  - 이전 판은 (run_id, coating_gap, position, frame_number) 4-튜플로 조인했으나
    한 원본 프레임에 patch 여러 개가 붙어 튜플이 유일하지 않다 (unique 367 개).
    dict 덮어쓰기로 조인이 오염됐다.
  - 이번 판은 09_spc_monitor.py 가 저장하도록 개정된 stem 컬럼(image_N) 을 키로 조인.
    stem 은 patch 단위로 유일하다.

담는 범위:
  1) results_mask_area.csv 산출 (patch x 채널별 면적비 + a3_defect_ratio 조인)
  2) A3 vs 면적비 상관계수 - 시퀀스별 및 pooled (area_or, area_crack, area_pinhole)
  3) figures/a3_vs_mask_area.png 산점도 2x4 격자 (a3 vs area_or)

담지 않는 것:
  - 갭 단조성, PR 곡선 스윕, 부분군 관리도.
  - 상관 결과 확인 후 다음 지시에 따라 별도로 추가한다.

정답 채널:
  ch0 crack, ch1 delam, ch2 pinhole 3 채널의 논-제로 픽셀 합집합을 area_or 로 쓴다.
  ch3 unclassified 는 제외한다. 진단 대상이 정의되지 않은 라벨이라 원인 축과
  대응되지 않기 때문. 제외 사실은 로그에 남긴다.

주의:
  - 기존 결과 파일(results_spc.csv 등) 을 덮어쓰지 않는다.
  - 새로 만드는 results_mask_area.csv 는 실행 시 이미 있으면 중단한다.
"""

import csv
import os
import re
import sys
import numpy as np
from PIL import Image
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as font_manager


LABELS_PATH = "classification/labels.csv"
MASK_DIR = "segmentation/masks"
SPC_CSV = "results_spc.csv"
OUTPUT_CSV = "results_mask_area.csv"
OUTPUT_FIG = "figures/a3_vs_mask_area.png"

MIN_SEQUENCE_LENGTH = 30  # SPC 와 동일 (09_spc_monitor.analyze_sequence)


# ===============================================================
# 유틸
# ===============================================================

def parse_original_filename(original_name):
    result = {}
    result["run_id"] = None
    result["coating_gap"] = None
    result["position"] = None
    result["frame_number"] = None

    name = original_name
    if name.endswith(".png"):
        name = name[:-4]

    frame_match = re.search(r"_frame_(\d+)", name)
    if frame_match is not None:
        result["frame_number"] = int(frame_match.group(1))

    condition_part = name
    frame_split = name.split("_frame_")
    if len(frame_split) > 1:
        condition_part = frame_split[0]

    run_match = re.match(r"^(R\d+)", condition_part)
    if run_match is not None:
        result["run_id"] = run_match.group(1)

    gap_match = re.search(r"(\d+)um", condition_part)
    if gap_match is not None:
        result["coating_gap"] = int(gap_match.group(1))

    position_text = condition_part
    if result["run_id"] is not None:
        position_text = position_text.replace(result["run_id"], "", 1)
    if result["coating_gap"] is not None:
        position_text = position_text.replace(str(result["coating_gap"]) + "um", "", 1)
    position_text = position_text.strip("-").strip("_")
    if len(position_text) > 0:
        result["position"] = position_text

    return result


def load_labels(path):
    """labels.csv 의 각 행을 stem(image_N) 을 키로 dict 화"""
    result = {}
    file_handle = open(path, "r", encoding="utf-8")
    reader = csv.DictReader(file_handle)
    for row in reader:
        parsed = parse_original_filename(row["original_file_name"])
        stem = row["file_name"]
        if stem.endswith(".jpg"):
            stem = stem[:-4]
        parsed["stem"] = stem
        parsed["surface_crack"] = int(row["Surface_Crack"])
        parsed["delamination"] = int(row["Delamination"])
        parsed["pinhole"] = int(row["Pinhole"])
        result[stem] = parsed
    file_handle.close()
    return result


def group_sequences(labels):
    """(run_id, coating_gap, position) 별로 stem 리스트를 만든다"""
    result = {}
    for stem in labels:
        record = labels[stem]
        if record["frame_number"] is None:
            continue
        key = (record["run_id"], record["coating_gap"], record["position"])
        if key not in result:
            result[key] = []
        result[key].append((record["frame_number"], stem))
    for key in result:
        result[key].sort()
    return result


def load_a3_by_stem(path):
    """results_spc.csv 를 stem 키 dict 로 로드. stem 컬럼이 없으면 중단."""
    result = {}
    file_handle = open(path, "r", encoding="utf-8")
    reader = csv.DictReader(file_handle)
    if reader.fieldnames is None or "stem" not in reader.fieldnames:
        file_handle.close()
        print("results_spc.csv 에 stem 컬럼이 없다. 09 를 개정 후 재실행할 것.")
        sys.exit(1)
    for row in reader:
        stem = row["stem"]
        if stem in result:
            file_handle.close()
            print("stem 중복 감지:", stem, "- 조인 무결성 실패. 중단.")
            sys.exit(1)
        result[stem] = float(row["defect_ratio"])
    file_handle.close()
    return result


def area_ratios(mask_array):
    """
    마스크 (H, W, 4) 로부터 채널별 논-제로 픽셀 비율을 낸다.
    ch3 (unclassified) 은 반환하되 area_or 에는 포함하지 않는다.
    """
    height = mask_array.shape[0]
    width = mask_array.shape[1]
    total = float(height * width)

    ch0 = (mask_array[..., 0] > 0)
    ch1 = (mask_array[..., 1] > 0)
    ch2 = (mask_array[..., 2] > 0)
    union = np.logical_or(np.logical_or(ch0, ch1), ch2)

    result = {}
    result["area_crack"] = float(ch0.sum()) / total
    result["area_delam"] = float(ch1.sum()) / total
    result["area_pinhole"] = float(ch2.sum()) / total
    result["area_or"] = float(union.sum()) / total
    return result


# ===============================================================
# 상관계수 (자체 구현. scipy 없음)
# ===============================================================

def pearson_r(x_values, y_values):
    x = np.array(x_values, dtype=float)
    y = np.array(y_values, dtype=float)
    if len(x) < 3:
        return float("nan"), float("nan")
    mx = float(x.mean())
    my = float(y.mean())
    dx = x - mx
    dy = y - my
    denom = float(np.sqrt((dx * dx).sum() * (dy * dy).sum()))
    if denom == 0.0:
        return float("nan"), float("nan")
    r = float((dx * dy).sum() / denom)
    if r > 1.0:
        r = 1.0
    if r < -1.0:
        r = -1.0
    return r, fisher_p(r, len(x))


def fisher_p(r, n):
    """Fisher z-transformation 으로 두-측 p-value 근사 (r != ±1 가정)"""
    if abs(r) >= 0.999999 or n < 4:
        return 0.0
    z = 0.5 * np.log((1.0 + r) / (1.0 - r))
    se = 1.0 / np.sqrt(n - 3)
    z_stat = abs(z) / se
    t = 1.0 / (1.0 + 0.2316419 * z_stat)
    coefficients = [0.319381530, -0.356563782, 1.781477937,
                    -1.821255978, 1.330274429]
    poly = 0.0
    power = t
    for coefficient in coefficients:
        poly = poly + coefficient * power
        power = power * t
    density = np.exp(-0.5 * z_stat * z_stat) / np.sqrt(2.0 * np.pi)
    one_sided = density * poly
    return float(2.0 * one_sided)


def rank_values(values):
    """평균 랭크 (동점은 평균)"""
    array = np.array(values, dtype=float)
    order = np.argsort(array, kind="mergesort")
    ranks = np.zeros(len(array), dtype=float)
    index = 0
    while index < len(array):
        end = index
        while end + 1 < len(array) and array[order[end + 1]] == array[order[index]]:
            end = end + 1
        average_rank = (index + end) / 2.0 + 1.0
        pos = index
        while pos <= end:
            ranks[order[pos]] = average_rank
            pos = pos + 1
        index = end + 1
    return ranks


def spearman_r(x_values, y_values):
    if len(x_values) < 3:
        return float("nan"), float("nan")
    rx = rank_values(x_values)
    ry = rank_values(y_values)
    return pearson_r(rx.tolist(), ry.tolist())


# ===============================================================
# 실행
# ===============================================================

def main():
    if os.path.exists(OUTPUT_CSV):
        print("이미 존재:", OUTPUT_CSV)
        print("기존 파일을 덮어쓰지 않는다. 필요하면 사용자가 옮긴 뒤 재실행할 것.")
        sys.exit(1)

    if os.path.isdir(MASK_DIR) is False:
        print("마스크 디렉터리 없음:", MASK_DIR)
        sys.exit(1)
    if os.path.exists(LABELS_PATH) is False:
        print("labels.csv 없음:", LABELS_PATH)
        sys.exit(1)
    if os.path.exists(SPC_CSV) is False:
        print("results_spc.csv 없음. 09 를 먼저 실행할 것.")
        sys.exit(1)

    print("=" * 82)
    print("22. 마스크 면적비 산출 및 A3 대비 상관 (stem 키 조인)")
    print("=" * 82)
    print("ch3 (unclassified) 은 area_or 계산에서 제외한다.")
    print()

    labels = load_labels(LABELS_PATH)
    sequences = group_sequences(labels)
    a3_by_stem = load_a3_by_stem(SPC_CSV)

    print("labels.csv 프레임 수:", len(labels))
    print("results_spc.csv stem 수:", len(a3_by_stem))
    print("시퀀스 (프리필터):", len(sequences))

    # SPC 스킵 정책과 맞추기 위해 30 프레임 미만 시퀀스 제거
    kept_keys = []
    for key in sequences:
        if len(sequences[key]) >= MIN_SEQUENCE_LENGTH:
            kept_keys.append(key)
    kept_keys.sort(key=lambda item: (str(item[0]), item[1], str(item[2])))
    print("시퀀스 (>=30 프레임):", len(kept_keys))

    # kept_keys 에 속한 stem 집합 - 이 집합이 조인 대상
    target_stems = set()
    for key in kept_keys:
        for _, stem in sequences[key]:
            target_stems.add(stem)
    print("조인 대상 stem 수:", len(target_stems))

    # 조인 무결성 검증
    labels_stems = set(target_stems)
    spc_stems = set(a3_by_stem.keys())
    missing_in_spc = labels_stems - spc_stems
    missing_in_labels = spc_stems - labels_stems
    if len(missing_in_spc) > 0 or len(missing_in_labels) > 0:
        print("조인 무결성 실패:")
        print("  labels 에 있으나 SPC 에 없는 stem:", len(missing_in_spc))
        print("  SPC 에 있으나 labels(kept) 에 없는 stem:", len(missing_in_labels))
        if len(missing_in_spc) > 0:
            print("  예시:", sorted(missing_in_spc)[:5])
        if len(missing_in_labels) > 0:
            print("  예시:", sorted(missing_in_labels)[:5])
        sys.exit(1)
    print("stem 일치 검증:", len(labels_stems), "/", len(labels_stems), "OK")
    print()

    # 프레임별 면적비 계산 + a3 조인
    print("마스크 로드 및 면적비 계산")
    all_rows = []
    missing_mask = 0

    for key in kept_keys:
        run_id = key[0]
        coating_gap = key[1]
        position = key[2]
        for frame_number, stem in sequences[key]:
            mask_path = os.path.join(MASK_DIR, stem + ".png")
            if os.path.exists(mask_path) is False:
                missing_mask = missing_mask + 1
                continue
            mask_array = np.array(Image.open(mask_path))
            if mask_array.ndim != 3 or mask_array.shape[2] < 3:
                missing_mask = missing_mask + 1
                continue

            ratios = area_ratios(mask_array)
            a3_value = a3_by_stem[stem]  # 위에서 stem 완전 일치 검증됨

            row = {}
            row["stem"] = stem
            row["run_id"] = run_id
            row["coating_gap"] = coating_gap
            row["position"] = position
            row["frame_number"] = frame_number
            row["area_crack"] = round(ratios["area_crack"], 6)
            row["area_delam"] = round(ratios["area_delam"], 6)
            row["area_pinhole"] = round(ratios["area_pinhole"], 6)
            row["area_or"] = round(ratios["area_or"], 6)
            row["a3_defect_ratio"] = round(a3_value, 6)
            all_rows.append(row)

    print("  처리 행:", len(all_rows))
    print("  마스크 결측:", missing_mask)
    print()

    field_names = ["stem", "run_id", "coating_gap", "position", "frame_number",
                   "area_crack", "area_delam", "area_pinhole", "area_or",
                   "a3_defect_ratio"]
    output_file = open(OUTPUT_CSV, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(output_file, fieldnames=field_names)
    writer.writeheader()
    for row in all_rows:
        writer.writerow(row)
    output_file.close()
    print("저장:", OUTPUT_CSV, "(", len(all_rows), "행 )")
    print()

    # 상관계수
    print("=" * 90)
    print("A3 vs 면적비 상관 (Pearson / Spearman, p-value 는 Fisher z 근사)")
    print("=" * 90)
    print()

    header = ("시퀀스".ljust(38) + "N".rjust(6)
              + "S(OR)".rjust(9) + "P(OR)".rjust(9)
              + "S(crack)".rjust(11) + "P(crack)".rjust(11)
              + "S(pin)".rjust(9) + "P(pin)".rjust(9))
    print(header)
    print("-" * len(header))

    pooled_a3 = []
    pooled_or = []
    pooled_crack = []
    pooled_pinhole = []
    per_sequence_stats = {}

    for key in kept_keys:
        seq_a3 = []
        seq_or = []
        seq_crack = []
        seq_pinhole = []
        for row in all_rows:
            row_key = (row["run_id"], row["coating_gap"], row["position"])
            if row_key != key:
                continue
            seq_a3.append(row["a3_defect_ratio"])
            seq_or.append(row["area_or"])
            seq_crack.append(row["area_crack"])
            seq_pinhole.append(row["area_pinhole"])

        pooled_a3.extend(seq_a3)
        pooled_or.extend(seq_or)
        pooled_crack.extend(seq_crack)
        pooled_pinhole.extend(seq_pinhole)

        s_or_r, s_or_p = spearman_r(seq_a3, seq_or)
        p_or_r, p_or_p = pearson_r(seq_a3, seq_or)
        s_ck_r, s_ck_p = spearman_r(seq_a3, seq_crack)
        p_ck_r, p_ck_p = pearson_r(seq_a3, seq_crack)
        s_pn_r, s_pn_p = spearman_r(seq_a3, seq_pinhole)
        p_pn_r, p_pn_p = pearson_r(seq_a3, seq_pinhole)

        per_sequence_stats[key] = {
            "n": len(seq_a3),
            "a3": seq_a3,
            "area_or": seq_or,
            "spearman_or": s_or_r,
            "pearson_or": p_or_r,
            "spearman_crack": s_ck_r,
            "pearson_crack": p_ck_r,
            "spearman_pinhole": s_pn_r,
            "pearson_pinhole": p_pn_r,
        }

        key_text = str(key[0]) + "/" + str(key[1]) + "/" + str(key[2])
        if len(key_text) > 36:
            key_text = key_text[:33] + "..."

        line = key_text.ljust(38)
        line = line + str(len(seq_a3)).rjust(6)
        line = line + ("{:.3f}".format(s_or_r)).rjust(9)
        line = line + ("{:.3f}".format(p_or_r)).rjust(9)
        line = line + ("{:.3f}".format(s_ck_r)).rjust(11)
        line = line + ("{:.3f}".format(p_ck_r)).rjust(11)
        line = line + ("{:.3f}".format(s_pn_r)).rjust(9)
        line = line + ("{:.3f}".format(p_pn_r)).rjust(9)
        print(line)

    print()
    ps_or_r, ps_or_p = spearman_r(pooled_a3, pooled_or)
    pp_or_r, pp_or_p = pearson_r(pooled_a3, pooled_or)
    ps_ck_r, ps_ck_p = spearman_r(pooled_a3, pooled_crack)
    pp_ck_r, pp_ck_p = pearson_r(pooled_a3, pooled_crack)
    ps_pn_r, ps_pn_p = spearman_r(pooled_a3, pooled_pinhole)
    pp_pn_r, pp_pn_p = pearson_r(pooled_a3, pooled_pinhole)

    print("pooled (전체 stem 합산):", len(pooled_a3), "행")
    print("  Spearman(A3, area_or)      =", round(ps_or_r, 4),
          "(p =", "{:.2e}".format(ps_or_p) + ")")
    print("  Pearson (A3, area_or)      =", round(pp_or_r, 4),
          "(p =", "{:.2e}".format(pp_or_p) + ")")
    print("  Spearman(A3, area_crack)   =", round(ps_ck_r, 4),
          "(p =", "{:.2e}".format(ps_ck_p) + ")")
    print("  Pearson (A3, area_crack)   =", round(pp_ck_r, 4),
          "(p =", "{:.2e}".format(pp_ck_p) + ")")
    print("  Spearman(A3, area_pinhole) =", round(ps_pn_r, 4),
          "(p =", "{:.2e}".format(ps_pn_p) + ")")
    print("  Pearson (A3, area_pinhole) =", round(pp_pn_r, 4),
          "(p =", "{:.2e}".format(pp_pn_p) + ")")
    print()

    # 산점도 (a3 vs area_or)
    print("산점도 저장:", OUTPUT_FIG)
    korean_font = None
    for font_object in font_manager.fontManager.ttflist:
        if font_object.name == "Noto Sans CJK KR":
            korean_font = font_object.name
            break
    if korean_font is not None:
        plt.rcParams["font.family"] = korean_font
    plt.rcParams["axes.unicode_minus"] = False

    figure, axes = plt.subplots(2, 4, figsize=(16, 8), sharex=True, sharey=True)
    x_max = 0.0
    y_max = 0.0
    for key in kept_keys:
        stats = per_sequence_stats[key]
        if len(stats["a3"]) > 0:
            x_max = max(x_max, float(np.max(stats["a3"])))
            y_max = max(y_max, float(np.max(stats["area_or"])))
    x_max = x_max * 1.05
    y_max = y_max * 1.05
    if x_max <= 0.0:
        x_max = 1.0
    if y_max <= 0.0:
        y_max = 1.0

    index = 0
    for key in kept_keys:
        row_idx = index // 4
        col_idx = index % 4
        axis = axes[row_idx][col_idx]
        stats = per_sequence_stats[key]

        axis.scatter(stats["a3"], stats["area_or"],
                     s=8, alpha=0.4, color="#1f77b4", edgecolors="none")

        title = str(key[0]) + " / " + str(key[1]) + "μm / " + str(key[2])
        if len(title) > 34:
            title = title[:31] + "..."
        axis.set_title(title, fontsize=9)

        text_lines = "N = " + str(stats["n"]) + "\n"
        text_lines = text_lines + "Spearman = " + "{:.3f}".format(stats["spearman_or"]) + "\n"
        text_lines = text_lines + "Pearson  = " + "{:.3f}".format(stats["pearson_or"])
        axis.text(0.02, 0.98, text_lines, transform=axis.transAxes,
                  ha="left", va="top", fontsize=8,
                  bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                            edgecolor="lightgray", alpha=0.85))

        axis.set_xlim(0.0, x_max)
        axis.set_ylim(0.0, y_max)
        if row_idx == 1:
            axis.set_xlabel("A3 defect_ratio")
        if col_idx == 0:
            axis.set_ylabel("area_or")
        axis.grid(alpha=0.2)
        index = index + 1

    figure.suptitle("A3 defect_ratio vs mask area_or (patch, stem-joined)", fontsize=12)
    figure.tight_layout(rect=[0, 0, 1, 0.96])
    figure.savefig(OUTPUT_FIG, dpi=140)
    plt.close(figure)

    print()
    print("완료.")


main()
