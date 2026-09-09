"""
CoatingVision 메타데이터 파싱 및 공정 조건 - 결함률 관계 분석

original_file_name 에서 다음을 추출한다:
  - run_id       : R1, R7 등 샘플/런 번호
  - coating_gap  : 700um, 1100um 등 코팅 갭 (µm)
  - position     : middle, top-to-bottom-center 등 촬영 위치
  - frame_number : 원본 영상의 프레임 번호
  - patch_index  : 프레임 내 패치 인덱스

이후 코팅 갭별 결함 발생률을 집계한다.
논문 서술(코팅 갭이 클수록 결함 증가)을 정량 검증하는 것이 목적이다.
"""

import csv
import re
import os


def parse_original_filename(original_name):
    """
    파일명 예시:
      R7-700um-middle_frame_489_patch_1.png
      R1-1100um-top-to-bottom-center-1_frame_220_patch_7.png

    파싱 실패 시 해당 필드는 None 으로 둔다.
    """
    result = {}
    result["original_name"] = original_name
    result["run_id"] = None
    result["coating_gap"] = None
    result["position"] = None
    result["frame_number"] = None
    result["patch_index"] = None

    # 확장자 제거
    name = original_name
    if name.endswith(".png"):
        name = name[:-4]

    # frame_숫자 추출
    frame_match = re.search(r"_frame_(\d+)", name)
    if frame_match is not None:
        result["frame_number"] = int(frame_match.group(1))

    # patch_숫자 추출
    patch_match = re.search(r"_patch_(\d+)", name)
    if patch_match is not None:
        result["patch_index"] = int(patch_match.group(1))

    # 앞부분(조건 구간)만 분리
    condition_part = name
    frame_split = name.split("_frame_")
    if len(frame_split) > 1:
        condition_part = frame_split[0]

    # run_id 추출 (맨 앞 R+숫자)
    run_match = re.match(r"^(R\d+)", condition_part)
    if run_match is not None:
        result["run_id"] = run_match.group(1)

    # coating_gap 추출 (숫자+um)
    gap_match = re.search(r"(\d+)um", condition_part)
    if gap_match is not None:
        result["coating_gap"] = int(gap_match.group(1))

    # position: run_id 와 gap 을 제거한 나머지
    position_text = condition_part
    if result["run_id"] is not None:
        position_text = position_text.replace(result["run_id"], "", 1)
    if result["coating_gap"] is not None:
        position_text = position_text.replace(str(result["coating_gap"]) + "um", "", 1)
    position_text = position_text.strip("-").strip("_")
    if len(position_text) > 0:
        result["position"] = position_text

    return result


def load_labels(csv_path):
    """
    classification/labels.csv 를 읽어 파싱 결과와 결함 라벨을 합친 리스트를 만든다.
    """
    records = []

    csv_file = open(csv_path, "r", encoding="utf-8")
    reader = csv.DictReader(csv_file)

    for row in reader:
        record = parse_original_filename(row["original_file_name"])
        record["image_file"] = row["file_name"]
        record["surface_crack"] = int(row["Surface_Crack"])
        record["delamination"] = int(row["Delamination"])
        record["pinhole"] = int(row["Pinhole"])
        record["unclassified"] = int(row["unclassified"])

        defect_sum = (
            record["surface_crack"]
            + record["delamination"]
            + record["pinhole"]
            + record["unclassified"]
        )
        if defect_sum == 0:
            record["is_clean"] = 1
        else:
            record["is_clean"] = 0

        records.append(record)

    csv_file.close()
    return records


def report_parsing_coverage(records):
    """파싱이 얼마나 성공했는지 확인한다."""
    total = len(records)

    run_parsed = 0
    gap_parsed = 0
    frame_parsed = 0
    patch_parsed = 0

    for record in records:
        if record["run_id"] is not None:
            run_parsed = run_parsed + 1
        if record["coating_gap"] is not None:
            gap_parsed = gap_parsed + 1
        if record["frame_number"] is not None:
            frame_parsed = frame_parsed + 1
        if record["patch_index"] is not None:
            patch_parsed = patch_parsed + 1

    print("=== 파싱 성공률 (전체 " + str(total) + "건) ===")
    print("run_id      :", run_parsed, "(" + str(round(run_parsed / total * 100, 1)) + "%)")
    print("coating_gap :", gap_parsed, "(" + str(round(gap_parsed / total * 100, 1)) + "%)")
    print("frame_number:", frame_parsed, "(" + str(round(frame_parsed / total * 100, 1)) + "%)")
    print("patch_index :", patch_parsed, "(" + str(round(patch_parsed / total * 100, 1)) + "%)")
    print()


def report_clean_images(records):
    """결함이 전혀 없는 이미지가 몇 장인지 센다. 비지도 학습 가능성의 핵심 지표."""
    total = len(records)

    clean_count = 0
    for record in records:
        if record["is_clean"] == 1:
            clean_count = clean_count + 1

    print("=== 완전 정상 이미지 ===")
    print("정상:", clean_count, "장 (" + str(round(clean_count / total * 100, 1)) + "%)")
    print("결함:", total - clean_count, "장")
    print()


def report_gap_vs_defect(records):
    """코팅 갭별 결함 발생률을 집계한다."""

    gap_groups = {}
    for record in records:
        gap = record["coating_gap"]
        if gap is None:
            continue
        if gap not in gap_groups:
            gap_groups[gap] = []
        gap_groups[gap].append(record)

    gap_values = sorted(gap_groups.keys())

    print("=== 코팅 갭별 결함 발생률 ===")
    print("gap(um)  n     crack%  delam%  pinhole%  clean%")
    for gap in gap_values:
        group = gap_groups[gap]
        n = len(group)

        crack_count = 0
        delam_count = 0
        pinhole_count = 0
        clean_count = 0
        for record in group:
            crack_count = crack_count + record["surface_crack"]
            delam_count = delam_count + record["delamination"]
            pinhole_count = pinhole_count + record["pinhole"]
            clean_count = clean_count + record["is_clean"]

        crack_ratio = round(crack_count / n * 100, 1)
        delam_ratio = round(delam_count / n * 100, 1)
        pinhole_ratio = round(pinhole_count / n * 100, 1)
        clean_ratio = round(clean_count / n * 100, 1)

        line = str(gap).ljust(8)
        line = line + str(n).ljust(6)
        line = line + str(crack_ratio).ljust(8)
        line = line + str(delam_ratio).ljust(8)
        line = line + str(pinhole_ratio).ljust(10)
        line = line + str(clean_ratio)
        print(line)
    print()


def report_run_groups(records):
    """런(R1, R7 ...)별 분포. 용매비 조건일 가능성이 있다."""

    run_groups = {}
    for record in records:
        run = record["run_id"]
        if run is None:
            run = "UNKNOWN"
        if run not in run_groups:
            run_groups[run] = []
        run_groups[run].append(record)

    run_keys = sorted(run_groups.keys())

    print("=== 런별 분포 ===")
    print("run     n     gaps                          crack%  clean%")
    for run in run_keys:
        group = run_groups[run]
        n = len(group)

        gap_set = set()
        crack_count = 0
        clean_count = 0
        for record in group:
            if record["coating_gap"] is not None:
                gap_set.add(record["coating_gap"])
            crack_count = crack_count + record["surface_crack"]
            clean_count = clean_count + record["is_clean"]

        gap_list = sorted(gap_set)
        gap_text = ",".join(str(g) for g in gap_list)
        if len(gap_text) > 28:
            gap_text = gap_text[:25] + "..."

        line = run.ljust(8)
        line = line + str(n).ljust(6)
        line = line + gap_text.ljust(30)
        line = line + str(round(crack_count / n * 100, 1)).ljust(8)
        line = line + str(round(clean_count / n * 100, 1))
        print(line)
    print()


def report_frame_continuity(records):
    """
    프레임 번호의 연속성을 확인한다.
    SPC 레이어에서 시계열로 쓸 수 있는지 판단하는 근거가 된다.
    """

    sequence_groups = {}
    for record in records:
        if record["frame_number"] is None:
            continue
        key = str(record["run_id"]) + "|" + str(record["coating_gap"]) + "|" + str(record["position"])
        if key not in sequence_groups:
            sequence_groups[key] = []
        sequence_groups[key].append(record["frame_number"])

    print("=== 프레임 시퀀스 (상위 10개 그룹) ===")
    print("그룹 수:", len(sequence_groups))

    group_sizes = []
    for key in sequence_groups:
        group_sizes.append((len(sequence_groups[key]), key))
    group_sizes.sort(reverse=True)

    print("group".ljust(50) + "n     frame range")
    for i in range(min(10, len(group_sizes))):
        size = group_sizes[i][0]
        key = group_sizes[i][1]
        frames = sorted(sequence_groups[key])
        frame_range = str(frames[0]) + " ~ " + str(frames[-1])

        key_text = key
        if len(key_text) > 48:
            key_text = key_text[:45] + "..."

        print(key_text.ljust(50) + str(size).ljust(6) + frame_range)
    print()


def main():
    csv_path = "classification/labels.csv"

    if os.path.exists(csv_path) is False:
        print("labels.csv 를 찾을 수 없습니다. 데이터셋 루트에서 실행하세요.")
        print("현재 위치:", os.getcwd())
        return

    records = load_labels(csv_path)
    print("총 레코드:", len(records))
    print()

    report_parsing_coverage(records)
    report_clean_images(records)
    report_gap_vs_defect(records)
    report_run_groups(records)
    report_frame_continuity(records)


main()
