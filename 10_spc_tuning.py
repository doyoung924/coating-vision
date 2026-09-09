"""
10. SPC 파라미터 튜닝 및 공정능력 판정

문제:
  09 실행 결과 알람이 1,000건 나왔다. EWMA 만으로 시퀀스당 161건이 울린다.
  실무에서 이 정도 알람은 무시당한다 (alarm fatigue).

  동시에 09 에서 중요한 사실이 드러났다.

  | 시퀀스 | 중심선 | 알람시점 크랙률 | 비알람 |
  | 600um  | 0.042 | 0.940 | 0.173 |  <- 변별력 5.4배
  | 800um  | 0.112 | 0.956 | 0.699 |
  | 900um  | 0.360 | 0.935 | 0.960 |  <- 변별력 없음
  | 1100um | 0.288 | 1.000 | 0.977 |  <- 변별력 없음

  갭이 커질수록 관리도가 무력해진다. 1100um 는 크랙률이 98.2% 이므로
  거의 모든 프레임이 결함이다. **전체가 불량인 공정에는 "이상"을 정의할 수 없다.**

  이것은 SPC 의 실패가 아니라 정확한 진단이다. 공정능력이 붕괴한 상태이며,
  이럴 때 필요한 것은 모니터링이 아니라 공정 조건 변경이다.
  시스템이 이 둘을 구분해서 말해줄 수 있어야 한다.

이 스크립트가 하는 일:
  1) 여러 파라미터 조합에서 알람 수와 변별력을 측정
  2) 공정능력 판정 - 관리도 적용이 유효한 구간인지 자동 판별
  3) 권장 설정 도출

변별력 지표:
  lift = (알람 시점 크랙 라벨 비율) / (비알람 시점 크랙 라벨 비율)
  1.0 이면 알람이 무의미하고, 클수록 알람이 실제 결함 증가를 짚은 것이다.

사용법:
  python3 10_spc_tuning.py
"""

import csv
import os
import numpy as np


SPC_RESULT_PATH = "results_spc.csv"
OUTPUT_PATH = "results_spc_tuning.csv"

# 공정능력 판정 기준
# 중심선(평균 이상 패치 비율)이 이 값을 넘으면 관리도 적용이 무의미하다고 본다
CAPABILITY_THRESHOLD = 0.25


# ===============================================================
# 데이터 로딩
# ===============================================================

def load_spc_results(path):
    """09 가 저장한 프레임별 결과를 시퀀스별로 묶는다."""
    sequences = {}

    result_file = open(path, "r", encoding="utf-8")
    reader = csv.DictReader(result_file)
    for row in reader:
        key = (row["run_id"], int(row["coating_gap"]), row["position"])
        if key not in sequences:
            sequences[key] = []

        record = {}
        record["frame_number"] = int(row["frame_number"])
        record["defect_ratio"] = float(row["defect_ratio"])
        record["label_crack"] = int(row["label_crack"])
        record["label_delam"] = int(row["label_delam"])
        record["label_pinhole"] = int(row["label_pinhole"])
        sequences[key].append(record)
    result_file.close()

    for key in sequences:
        sequences[key].sort(key=lambda item: item["frame_number"])

    return sequences


# ===============================================================
# 관리도 계산 (09 와 동일 로직)
# ===============================================================

def compute_control_limits(baseline_values, sigma_limit):
    array = np.array(baseline_values, dtype=float)
    center = float(np.mean(array))
    deviation = float(np.std(array))

    upper = center + sigma_limit * deviation
    lower = center - sigma_limit * deviation
    if lower < 0.0:
        lower = 0.0

    result = {}
    result["center"] = center
    result["sigma"] = deviation
    result["upper"] = upper
    result["lower"] = lower
    return result


def compute_ewma(values, lambda_value, initial_value):
    output = []
    previous = float(initial_value)
    index = 0
    while index < len(values):
        current = lambda_value * float(values[index]) + (1.0 - lambda_value) * previous
        output.append(current)
        previous = current
        index = index + 1
    return output


def compute_ewma_limits(center, sigma, lambda_value, sigma_limit, length):
    upper_list = []
    lower_list = []
    index = 0
    while index < length:
        step = index + 1
        factor = lambda_value / (2.0 - lambda_value)
        factor = factor * (1.0 - (1.0 - lambda_value) ** (2 * step))
        width = sigma_limit * sigma * np.sqrt(factor)
        upper_list.append(center + width)
        lower_value = center - width
        if lower_value < 0.0:
            lower_value = 0.0
        lower_list.append(lower_value)
        index = index + 1
    return upper_list, lower_list


def detect_alerts(values, limits, lambda_value, sigma_limit, use_ewma, min_gap):
    """
    알람을 감지하되 min_gap 프레임 이내의 연속 알람은 하나로 묶는다.

    09 에서 알람이 폭증한 주된 이유는 한 번 이탈하면
    그 상태가 지속되는 동안 매 프레임 알람이 울렸기 때문이다.
    실무에서는 "이상 구간 진입" 한 번만 알리면 된다.
    """
    raw_alerts = []

    index = 0
    while index < len(values):
        if values[index] > limits["upper"]:
            raw_alerts.append((index, "shewhart_upper"))
        index = index + 1

    if use_ewma is True:
        ewma_values = compute_ewma(values, lambda_value, limits["center"])
        upper_list, lower_list = compute_ewma_limits(
            limits["center"], limits["sigma"], lambda_value, sigma_limit, len(values)
        )
        index = 0
        while index < len(ewma_values):
            if ewma_values[index] > upper_list[index]:
                raw_alerts.append((index, "ewma_upper"))
            index = index + 1

    raw_alerts.sort()

    # 연속 알람 병합
    merged = []
    last_index = -999
    for alert in raw_alerts:
        if alert[0] - last_index >= min_gap:
            merged.append(alert)
            last_index = alert[0]

    return merged, raw_alerts


# ===============================================================
# 평가
# ===============================================================

def evaluate_setting(records, sigma_limit, lambda_value, use_ewma,
                     min_gap, baseline_fraction, alert_window):
    """
    한 파라미터 설정에 대해 알람 수와 변별력을 계산한다.

    alert_window: 알람 시점 전후 몇 프레임을 "알람 영향 구간"으로 볼 것인가.
                  알람은 이상 구간의 시작을 알리므로, 그 시점 하나만 보면
                  변별력이 과소평가된다.
    """
    values = []
    for record in records:
        values.append(record["defect_ratio"])

    if len(values) < 30:
        return None

    baseline_count = int(len(values) * baseline_fraction)
    if baseline_count < 20:
        baseline_count = 20

    limits = compute_control_limits(values[:baseline_count], sigma_limit)

    merged_alerts, raw_alerts = detect_alerts(
        values, limits, lambda_value, sigma_limit, use_ewma, min_gap
    )

    # 알람 영향 구간 표시
    alert_flags = np.zeros(len(values), dtype=bool)
    for alert in merged_alerts:
        start = alert[0] - alert_window
        if start < 0:
            start = 0
        end = alert[0] + alert_window + 1
        if end > len(values):
            end = len(values)
        alert_flags[start:end] = True

    # 변별력
    alert_crack = 0
    alert_total = 0
    normal_crack = 0
    normal_total = 0

    index = 0
    while index < len(records):
        if alert_flags[index] is np.True_ or alert_flags[index] == True:
            alert_crack = alert_crack + records[index]["label_crack"]
            alert_total = alert_total + 1
        else:
            normal_crack = normal_crack + records[index]["label_crack"]
            normal_total = normal_total + 1
        index = index + 1

    alert_rate = 0.0
    if alert_total > 0:
        alert_rate = alert_crack / float(alert_total)
    normal_rate = 0.0
    if normal_total > 0:
        normal_rate = normal_crack / float(normal_total)

    lift = 0.0
    if normal_rate > 0:
        lift = alert_rate / normal_rate
    elif alert_rate > 0:
        lift = float("inf")

    result = {}
    result["n_frames"] = len(values)
    result["center"] = limits["center"]
    result["sigma"] = limits["sigma"]
    result["ucl"] = limits["upper"]
    result["n_alerts_raw"] = len(raw_alerts)
    result["n_alerts_merged"] = len(merged_alerts)
    result["alert_rate"] = alert_rate
    result["normal_rate"] = normal_rate
    result["lift"] = lift
    result["coverage"] = alert_total / float(len(values))
    return result


def judge_capability(center, threshold):
    """
    관리도 적용이 유효한 구간인지 판정한다.

    중심선(평균 이상 패치 비율)이 높다는 것은 정상 상태 자체가 이미 불량이라는 뜻이다.
    이 경우 "이상"을 정의할 기준이 없으므로 관리도는 무력하다.
    """
    if center < threshold * 0.4:
        return "GOOD", "관리도 적용 유효. 정상 상태가 안정적."
    if center < threshold:
        return "MARGINAL", "관리도 적용 가능하나 변별력 저하. 공정 조건 검토 권장."
    return "INCAPABLE", "공정능력 붕괴. 정상 상태 자체가 불량 수준이므로 관리도 무의미. 모니터링이 아니라 공정 조건 변경이 필요."


# ===============================================================

def main():
    if os.path.exists(SPC_RESULT_PATH) is False:
        print(SPC_RESULT_PATH, "를 찾을 수 없습니다. 09 를 먼저 실행하세요.")
        return

    sequences = load_spc_results(SPC_RESULT_PATH)

    print("=" * 88)
    print("SPC 파라미터 튜닝")
    print("=" * 88)
    print()
    print("문제: 09 기본 설정에서 알람 1,000건. 실무에서 무시되는 수준(alarm fatigue).")
    print("목표: 알람 수를 줄이면서 변별력(lift)은 유지하는 설정 찾기.")
    print()
    print("lift = 알람구간 크랙률 / 비알람구간 크랙률. 1.0 이면 알람이 무의미.")
    print()

    # -----------------------------------------------------------
    # 1. 공정능력 판정
    # -----------------------------------------------------------
    print("=" * 88)
    print("1. 공정능력 판정")
    print("=" * 88)
    print()
    print("시퀀스".ljust(34) + "중심선".ljust(10) + "판정".ljust(13) + "설명")

    capable_keys = []
    sequence_keys = sorted(sequences.keys(), key=lambda item: (str(item[0]), item[1], str(item[2])))

    for key in sequence_keys:
        records = sequences[key]
        values = []
        for record in records:
            values.append(record["defect_ratio"])
        baseline_count = int(len(values) * 0.3)
        if baseline_count < 20:
            baseline_count = 20
        center = float(np.mean(values[:baseline_count]))

        verdict, explanation = judge_capability(center, CAPABILITY_THRESHOLD)

        key_text = str(key[0]) + "/" + str(key[1]) + "um/" + str(key[2])
        if len(key_text) > 32:
            key_text = key_text[:29] + "..."

        line = key_text.ljust(34)
        line = line + str(round(center, 4)).ljust(10)
        line = line + verdict.ljust(13)
        line = line + explanation[:40]
        print(line)

        if verdict != "INCAPABLE":
            capable_keys.append(key)

    print()
    print("관리도 적용 대상:", len(capable_keys), "/", len(sequence_keys), "시퀀스")
    print()

    # -----------------------------------------------------------
    # 2. 파라미터 스윕
    # -----------------------------------------------------------
    print("=" * 88)
    print("2. 파라미터 스윕 (관리도 유효 시퀀스만)")
    print("=" * 88)
    print()

    settings = []
    settings.append(("기본(09)", 3.0, 0.2, True, 1))
    settings.append(("병합만", 3.0, 0.2, True, 10))
    settings.append(("3s+EWMA0.1", 3.0, 0.1, True, 10))
    settings.append(("3.5s+EWMA0.1", 3.5, 0.1, True, 10))
    settings.append(("4s+EWMA0.1", 4.0, 0.1, True, 10))
    settings.append(("3s Shewhart만", 3.0, 0.2, False, 10))
    settings.append(("3.5s Shewhart만", 3.5, 0.2, False, 10))
    settings.append(("4s Shewhart만", 4.0, 0.2, False, 10))

    result_rows = []

    print("설정".ljust(18) + "총알람".ljust(9) + "병합후".ljust(9)
          + "알람구간%".ljust(11) + "알람크랙률".ljust(12) + "비알람".ljust(10) + "lift")
    print("-" * 88)

    for setting_name, sigma_limit, lambda_value, use_ewma, min_gap in settings:
        total_raw = 0
        total_merged = 0
        total_coverage = 0.0
        lift_values = []
        alert_rates = []
        normal_rates = []

        for key in capable_keys:
            evaluation = evaluate_setting(
                sequences[key], sigma_limit, lambda_value, use_ewma,
                min_gap, 0.3, 5
            )
            if evaluation is None:
                continue

            total_raw = total_raw + evaluation["n_alerts_raw"]
            total_merged = total_merged + evaluation["n_alerts_merged"]
            total_coverage = total_coverage + evaluation["coverage"]
            alert_rates.append(evaluation["alert_rate"])
            normal_rates.append(evaluation["normal_rate"])
            if evaluation["lift"] != float("inf"):
                lift_values.append(evaluation["lift"])

        n = len(capable_keys)
        mean_coverage = total_coverage / float(n)
        mean_alert_rate = float(np.mean(alert_rates))
        mean_normal_rate = float(np.mean(normal_rates))
        mean_lift = 0.0
        if len(lift_values) > 0:
            mean_lift = float(np.mean(lift_values))

        line = setting_name.ljust(18)
        line = line + str(total_raw).ljust(9)
        line = line + str(total_merged).ljust(9)
        line = line + str(round(mean_coverage * 100, 1)).ljust(11)
        line = line + str(round(mean_alert_rate, 3)).ljust(12)
        line = line + str(round(mean_normal_rate, 3)).ljust(10)
        line = line + str(round(mean_lift, 2))
        print(line)

        row = {}
        row["setting"] = setting_name
        row["sigma"] = sigma_limit
        row["lambda"] = lambda_value
        row["use_ewma"] = use_ewma
        row["min_gap"] = min_gap
        row["alerts_raw"] = total_raw
        row["alerts_merged"] = total_merged
        row["coverage_pct"] = round(mean_coverage * 100, 1)
        row["alert_crack_rate"] = round(mean_alert_rate, 3)
        row["normal_crack_rate"] = round(mean_normal_rate, 3)
        row["lift"] = round(mean_lift, 2)
        result_rows.append(row)

    print()

    # -----------------------------------------------------------
    # 3. 권장 설정
    # -----------------------------------------------------------
    print("=" * 88)
    print("3. 해석")
    print("=" * 88)
    print()
    print("판단 기준:")
    print("  - 알람 수가 적을수록 좋다 (실무에서 대응 가능한 수준)")
    print("  - lift 가 클수록 좋다 (알람이 실제 결함 증가를 짚음)")
    print("  - 알람구간% 가 지나치게 크면 (50% 초과) 상시 알람이므로 무의미")
    print()
    print("두 지표는 상충한다. 민감하게 잡으면 알람이 늘고, 둔감하게 하면 놓친다.")
    print("실무 선택은 오탐 대응 비용과 미검출 비용의 상대적 크기에 달려 있다.")
    print()

    write_csv(OUTPUT_PATH,
              ["setting", "sigma", "lambda", "use_ewma", "min_gap",
               "alerts_raw", "alerts_merged", "coverage_pct",
               "alert_crack_rate", "normal_crack_rate", "lift"],
              result_rows)
    print("저장:", OUTPUT_PATH)


def write_csv(path, field_names, rows):
    if len(rows) == 0:
        return
    output_file = open(path, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(output_file, fieldnames=field_names)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    output_file.close()


main()
