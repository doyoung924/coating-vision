"""
19_capture_screens.py

Playwright 로 대시보드 화면을 자동 캡처하고, /stream 관리도가
현재 인덱스까지만 누적되는지를 정량 검증한다.

**전제**: Flask 앱이 이미 별도 터미널에서 `python app.py` 로 5000 포트에 떠 있어야 한다.

캡처 대상:
  A. /stream 재생 누적 (R1/600 idx=40, 120, 200, 305, back to 40)
  B. INCAPABLE 대비 (R1/900 idx=150)
  C. 검출 상세 크롭 패널 (박스가 있는 첫 프레임)
  D. /, /inspect (업로드 전), /inspect (샘플 pinhole 결과), /benchmark

각 A 캡처는 DOM 의 "누적 알람" 숫자와 Chart.js 데이터셋 길이를 읽어
stream_data JSON 에서 계산한 정답과 대조한다.
"""

import json
import os
import socket
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent
CAPTURE_DIR = PROJECT_ROOT / "capture"
STREAM_DATA = PROJECT_ROOT / "stream_data"
BASE_URL = "http://localhost:5000"
SEQ_MAIN = "R1_600_top-to-bottom-center"
SEQ_INCAPABLE = "R1_900_top-to-bottom-center"


def check_app_running():
    probe = socket.socket()
    probe.settimeout(1.0)
    try:
        probe.connect(("localhost", 5000))
        probe.close()
        return True
    except Exception:
        return False


def load_stream_json(sequence_id):
    path = STREAM_DATA / (sequence_id + ".json")
    with open(path, "r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


def count_alerts_up_to(data, up_to_index):
    count = 0
    limit = up_to_index + 1
    if limit > len(data["frames"]):
        limit = len(data["frames"])
    for i in range(limit):
        frame = data["frames"][i]
        if len(frame["alerts"]) > 0:
            count = count + 1
    return count


def find_first_detection_index(data):
    for index in range(len(data["frames"])):
        frame = data["frames"][index]
        if len(frame["detections"]) > 0:
            return index
    return None


def select_sequence(page, sequence_id):
    script = """
    (target) => {
        const sel = document.getElementById('seqSelect');
        sel.value = target;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
    }
    """
    page.evaluate(script, sequence_id)


def move_slider(page, index):
    script = """
    (target) => {
        const slider = document.getElementById('frameSlider');
        slider.value = String(target);
        slider.dispatchEvent(new Event('input', { bubbles: true }));
    }
    """
    page.evaluate(script, index)


def read_alert_count(page):
    text = page.locator("#statAlertCount").text_content()
    if text is None:
        return None
    text = text.strip()
    try:
        return int(text)
    except ValueError:
        return None


def read_chart_lengths(page):
    script = """
    () => {
        if (typeof Chart === 'undefined') return null;
        const chart = Chart.getChart('controlChart');
        if (!chart) return null;
        return {
            defect: chart.data.datasets[0].data.length,
            ewma: chart.data.datasets[1].data.length,
            ucl: chart.data.datasets[2].data.length,
            center: chart.data.datasets[3].data.length,
            alert: chart.data.datasets[4].data.length,
            cursor: chart.data.datasets[5].data.length,
        };
    }
    """
    return page.evaluate(script)


def capture_stream_frame(page, sequence_id, index, out_name, label, gt_data):
    move_slider(page, index)
    page.wait_for_timeout(600)

    dom_alerts = read_alert_count(page)
    lengths = read_chart_lengths(page)
    gt_alerts = count_alerts_up_to(gt_data, index)
    expected_defect = index + 1

    page.screenshot(path=str(CAPTURE_DIR / out_name), full_page=False)

    print("[" + label + "] seq=" + sequence_id + " idx=" + str(index))
    print("  DOM 누적 알람: " + str(dom_alerts))
    if lengths is not None:
        print("  Chart defect points: " + str(lengths["defect"])
              + ", ewma: " + str(lengths["ewma"])
              + ", alert marker: " + str(lengths["alert"])
              + ", cursor: " + str(lengths["cursor"]))
    print("  Ground truth alerts (0.." + str(index) + "): " + str(gt_alerts))

    match_alerts = (dom_alerts == gt_alerts)
    if lengths is None:
        match_chart = False
    else:
        match_chart = (lengths["defect"] == expected_defect)
    overall = match_alerts and match_chart
    if overall:
        print("  판정: PASS")
    else:
        print("  판정: FAIL")

    if lengths is None:
        chart_defect = None
    else:
        chart_defect = lengths["defect"]

    return {
        "label": label,
        "index": index,
        "dom_alerts": dom_alerts,
        "gt_alerts": gt_alerts,
        "chart_defect": chart_defect,
        "expected_defect": expected_defect,
        "pass": overall,
    }


def main():
    if not check_app_running():
        print("ERROR: localhost:5000 에 응답이 없다. `python app.py` 를 먼저 별도 터미널에서 실행하라.")
        sys.exit(1)

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

    main_data = load_stream_json(SEQ_MAIN)
    incapable_data = load_stream_json(SEQ_INCAPABLE)

    results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        # ---- A. /stream 누적 검증 ----
        page.goto(BASE_URL + "/stream", wait_until="networkidle")
        page.wait_for_timeout(1500)

        select_sequence(page, SEQ_MAIN)
        page.wait_for_timeout(700)

        for index in [40, 120, 200, 305]:
            filename = "stream_idx" + str(index).zfill(3) + ".png"
            record = capture_stream_frame(page, SEQ_MAIN, index, filename, "idx" + str(index), main_data)
            results.append(record)

        # 후진: 305 → 40
        record = capture_stream_frame(page, SEQ_MAIN, 40, "stream_back040.png", "back040", main_data)
        results.append(record)

        # ---- B. INCAPABLE 대비 ----
        select_sequence(page, SEQ_INCAPABLE)
        page.wait_for_timeout(800)
        capture_stream_frame(page, SEQ_INCAPABLE, 150, "stream_incapable.png", "incapable_idx150", incapable_data)

        # ---- B'. GOOD vs INCAPABLE 50% 비교 ----
        good_50pct = len(main_data["frames"]) // 2
        incapable_50pct = len(incapable_data["frames"]) // 2

        select_sequence(page, SEQ_MAIN)
        page.wait_for_timeout(700)
        move_slider(page, good_50pct)
        page.wait_for_timeout(600)
        page.screenshot(path=str(CAPTURE_DIR / "compare_good_50pct.png"), full_page=False)
        print("[compare_good_50pct] " + SEQ_MAIN + " idx=" + str(good_50pct)
              + " (전체 " + str(len(main_data["frames"])) + "프레임 중 50%)")

        select_sequence(page, SEQ_INCAPABLE)
        page.wait_for_timeout(800)
        move_slider(page, incapable_50pct)
        page.wait_for_timeout(600)
        page.screenshot(path=str(CAPTURE_DIR / "compare_incapable_50pct.png"), full_page=False)
        print("[compare_incapable_50pct] " + SEQ_INCAPABLE + " idx=" + str(incapable_50pct)
              + " (전체 " + str(len(incapable_data["frames"])) + "프레임 중 50%)")

        # ---- C. 크롭 패널 ----
        select_sequence(page, SEQ_MAIN)
        page.wait_for_timeout(700)
        detection_index = find_first_detection_index(main_data)
        if detection_index is None:
            print("검출된 프레임이 없어 crop_panel.png 캡처를 건너뛴다.")
        else:
            move_slider(page, detection_index)
            page.wait_for_timeout(700)
            crop = page.locator(".crop-frame").first
            crop.screenshot(path=str(CAPTURE_DIR / "crop_panel.png"))
            print("[crop_panel] idx=" + str(detection_index) + " 캡처 완료")

        # ---- D. 나머지 페이지 ----
        page.goto(BASE_URL + "/", wait_until="networkidle")
        page.wait_for_timeout(600)
        page.screenshot(path=str(CAPTURE_DIR / "page_index.png"), full_page=False)

        page.goto(BASE_URL + "/inspect", wait_until="networkidle")
        page.wait_for_timeout(400)
        page.screenshot(path=str(CAPTURE_DIR / "page_inspect_empty.png"), full_page=True)

        # 첫 번째 sample-form 은 pinhole 이다 (SAMPLE_INFO 순서 유지)
        page.locator("form.sample-form").first.locator("button").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(600)
        page.screenshot(path=str(CAPTURE_DIR / "page_inspect_result.png"), full_page=True)

        page.goto(BASE_URL + "/benchmark", wait_until="networkidle")
        page.wait_for_timeout(400)
        page.screenshot(path=str(CAPTURE_DIR / "page_benchmark.png"), full_page=True)

        browser.close()

    # ---- 요약 ----
    print()
    print("=" * 60)
    print("A 판정 요약 (누적 알람 · 차트 데이터 · 정답 일치)")
    print("=" * 60)
    all_pass = True
    for record in results:
        if record["pass"]:
            mark = "PASS"
        else:
            mark = "FAIL"
            all_pass = False
        print("  " + mark
              + "  " + record["label"]
              + " (idx=" + str(record["index"]) + ")"
              + "  DOM=" + str(record["dom_alerts"])
              + "  GT=" + str(record["gt_alerts"])
              + "  chart_defect=" + str(record["chart_defect"])
              + "/" + str(record["expected_defect"]))
    print()
    if all_pass:
        print("모든 A 지점에서 DOM 알람 카운트 · Chart.js 데이터 개수 · 정답 일치.")
    else:
        print("A 판정에 불일치 지점이 있다. 위 표에서 FAIL 항목 확인.")

    print()
    print("=" * 60)
    print("capture/ 파일 목록")
    print("=" * 60)
    files = []
    for path in sorted(CAPTURE_DIR.glob("*.png")):
        files.append(path)
    for path in files:
        size = os.path.getsize(path)
        print("  " + path.name + "  (" + str(size) + " bytes)")


if __name__ == "__main__":
    main()
