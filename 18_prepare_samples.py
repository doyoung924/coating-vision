"""
18_prepare_samples.py

/inspect 페이지의 "예시로 시도해보기" 버튼용 샘플 이미지 3장을
app/static/samples/ 로 복사한다:

  - pinhole.jpg : Pinhole 라벨만 있는 이미지
  - crack.jpg   : Surface_Crack 라벨만 있는 이미지
  - clean.jpg   : 전 라벨 0 인 이미지

CoatingVision 라이선스(CC BY-NC-ND 4.0) 제약으로 app/static/samples/ 는
gitignore 되어 있다. 이 스크립트는 로컬에 원본 세그멘테이션 이미지가
있어야 동작한다.
"""

import csv
import os
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
LABELS_CSV = PROJECT_ROOT / "classification" / "labels.csv"
IMAGE_DIR = PROJECT_ROOT / "segmentation" / "images"
OUT_DIR = PROJECT_ROOT / "app" / "static" / "samples"


def load_rows():
    rows = []
    with open(LABELS_CSV, "r", encoding="utf-8") as file_handle:
        reader = csv.DictReader(file_handle)
        for row in reader:
            rows.append(row)
    return rows


def is_pinhole_only(row):
    if int(row["Pinhole"]) != 1:
        return False
    if int(row["Surface_Crack"]) != 0:
        return False
    if int(row["Delamination"]) != 0:
        return False
    return True


def is_crack_only(row):
    if int(row["Surface_Crack"]) != 1:
        return False
    if int(row["Pinhole"]) != 0:
        return False
    if int(row["Delamination"]) != 0:
        return False
    return True


def is_clean(row):
    if int(row["Surface_Crack"]) != 0:
        return False
    if int(row["Delamination"]) != 0:
        return False
    if int(row["Pinhole"]) != 0:
        return False
    if int(row["unclassified"]) != 0:
        return False
    return True


def pick_first(rows, predicate):
    for row in rows:
        if predicate(row):
            return row["file_name"]
    return None


def copy_sample(source_name, dest_name):
    source = IMAGE_DIR / source_name
    destination = OUT_DIR / dest_name
    if source.exists() is False:
        print("  원본 없음: {}".format(source))
        return False
    shutil.copyfile(str(source), str(destination))
    print("  {} → {}".format(source_name, destination.name))
    return True


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    print("labels.csv: {} rows".format(len(rows)))

    picks = {
        "pinhole.jpg": pick_first(rows, is_pinhole_only),
        "crack.jpg": pick_first(rows, is_crack_only),
        "clean.jpg": pick_first(rows, is_clean),
    }

    for dest_name in picks:
        source_name = picks[dest_name]
        if source_name is None:
            print("  {} : 라벨 조건에 맞는 이미지가 없음".format(dest_name))
            continue
        copy_sample(source_name, dest_name)

    print("완료. app/static/samples/ 에서 3장 확인.")


if __name__ == "__main__":
    main()
