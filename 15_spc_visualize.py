"""
15. SPC 시각화 - 시퀀스별 관리도 및 요약 도표

목적:
  09/10 이 산출한 results_spc.csv, results_spc_alerts.csv 를 읽어
  8개 시퀀스의 관리도를 그린다.
  텍스트 표만으로는 "900um 이상에서 공정능력 붕괴" 라는 결론이
  숫자 몇 줄에 압축되어 있어 임팩트가 약하다.
  갭이 증가할수록 관리도 자체가 무의미해지는 모습을 시각적으로 보여준다.

데이터 실체 (반드시 인지):
  results_spc.csv 의 행은 프레임이 아니라 480x640 이미지 단위다.
  CoatingVision 이 한 비디오 프레임에서 여러 이미지를 추출했기 때문에
  (run_id, coating_gap, position, frame_number) 는 유일하지 않다.
  관측 순서 = CSV 내 행 순서 (09 스크립트가 frame_number 로 정렬 후 EWMA 계산).
  따라서 x 축은 관측 인덱스(0..N-1) 를 쓴다. frame_number 는 5개 tick 에만 표시.

공정능력 판정 (experiment_log 9-4):
  center < 0.10           GOOD
  0.10 <= center < 0.25   MARGINAL
  center >= 0.25          INCAPABLE
"""

import csv
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches


RESULT_PATH = "results_spc.csv"
ALERT_PATH = "results_spc_alerts.csv"
FIGURE_DIR = "figures"

CAPABILITY_GOOD = 0.10
CAPABILITY_MARGINAL = 0.25

COLOR_SCATTER = "#B0B0B0"
COLOR_EWMA = "#1f4e79"
COLOR_CENTER = "#2ca02c"
COLOR_UCL = "#d62728"

ALERT_STYLE = {}
ALERT_STYLE["rule1_upper"] = {"marker": "x", "color": "#d62728", "label": "Rule 1: 3.5σ 이탈"}
ALERT_STYLE["rule1_lower"] = {"marker": "x", "color": "#d62728", "label": "Rule 1: 3.5σ 이탈"}
ALERT_STYLE["rule2_run9"] = {"marker": "s", "color": "#ff7f0e", "label": "Rule 2: 연속 9점"}
ALERT_STYLE["rule3_trend_up"] = {"marker": "^", "color": "#bcbd22", "label": "Rule 3: 단조 증감"}
ALERT_STYLE["rule3_trend_down"] = {"marker": "v", "color": "#bcbd22", "label": "Rule 3: 단조 증감"}
ALERT_STYLE["ewma_upper"] = {"marker": "o", "color": "#9467bd", "label": "EWMA 이탈"}
ALERT_STYLE["ewma_lower"] = {"marker": "o", "color": "#9467bd", "label": "EWMA 이탈"}


# ===============================================================
# 데이터 로딩
# ===============================================================

def load_spc_records(path):
    """
    CSV 를 행 순서대로 읽어 시퀀스별로 나눈다.
    각 시퀀스 안에서 CSV 순서 = 관측 순서.
    반환: sequences 딕셔너리, key = (run_id, gap, position), value = record 리스트
    """
    sequences = {}
    csv_file = open(path, "r", encoding="utf-8")
    reader = csv.DictReader(csv_file)
    for row in reader:
        key = (row["run_id"], int(row["coating_gap"]), row["position"])
        if key not in sequences:
            sequences[key] = []
        record = {}
        record["frame_number"] = int(row["frame_number"])
        record["defect_ratio"] = float(row["defect_ratio"])
        record["moving_average"] = float(row["moving_average"])
        record["ewma"] = float(row["ewma"])
        record["ucl"] = float(row["ucl"])
        record["center"] = float(row["center"])
        sequences[key].append(record)
    csv_file.close()
    return sequences


def load_alerts(path):
    """
    알람 CSV 를 읽어 시퀀스별로 (frame_number, rule) 리스트로 만든다.
    같은 (frame_number, rule) 이 여러 번 등장할 수 있으나 중복 제거하지 않는다.
    시각화 단계에서 관측 인덱스 매칭 후 첫 매칭 기준으로만 표시한다.
    """
    alerts_by_sequence = {}
    csv_file = open(path, "r", encoding="utf-8")
    reader = csv.DictReader(csv_file)
    for row in reader:
        key = (row["run_id"], int(row["coating_gap"]), row["position"])
        if key not in alerts_by_sequence:
            alerts_by_sequence[key] = []
        alert = {}
        alert["frame_number"] = int(row["frame_number"])
        alert["rule"] = row["rule"]
        alert["value"] = float(row["value"])
        alerts_by_sequence[key].append(alert)
    csv_file.close()
    return alerts_by_sequence


# ===============================================================
# 알람 -> 관측 인덱스 매핑
# ===============================================================

def match_alerts_to_indices(records, alerts):
    """
    알람에는 frame_number 만 있으므로 관측 인덱스로 매핑한다.
    같은 frame_number 를 갖는 관측이 여러 개일 수 있으므로
    "이 규칙에 대해 아직 사용되지 않은 첫 관측" 을 소진 방식으로 골라준다.
    이렇게 하면 같은 프레임에서 여러 규칙이 동시에 뜬 것과
    실제로 여러 관측에서 규칙이 각각 뜬 것을 구분할 수 있다.
    """
    # rule 별로 사용된 인덱스를 추적
    used_by_rule = {}
    matched = []

    for alert in alerts:
        target_frame = alert["frame_number"]
        target_rule = alert["rule"]

        if target_rule not in used_by_rule:
            used_by_rule[target_rule] = set()

        chosen_index = None
        index = 0
        while index < len(records):
            if records[index]["frame_number"] == target_frame:
                if index not in used_by_rule[target_rule]:
                    chosen_index = index
                    break
            index = index + 1

        if chosen_index is not None:
            used_by_rule[target_rule].add(chosen_index)
            matched.append({"index": chosen_index, "rule": target_rule, "value": alert["value"]})

    return matched


# ===============================================================
# 공정능력 판정
# ===============================================================

def judge_capability(center):
    if center < CAPABILITY_GOOD:
        return "GOOD"
    if center < CAPABILITY_MARGINAL:
        return "MARGINAL"
    return "INCAPABLE"


def capability_color(label):
    if label == "GOOD":
        return "#2ca02c"
    if label == "MARGINAL":
        return "#ff7f0e"
    return "#d62728"


# ===============================================================
# 그림 그리기
# ===============================================================

def format_sequence_title(sequence_key, center, capability_label):
    run_id = sequence_key[0]
    gap = sequence_key[1]
    position = sequence_key[2]
    text = run_id + " / " + str(gap) + "µm / " + position
    text = text + "  —  " + capability_label + " (center=" + format(center, ".3f") + ")"
    return text


def frame_number_tick_labels(records, n_ticks):
    """
    관측 인덱스 축에 frame_number 를 5개 정도 표시한다.
    시퀀스가 짧으면 그만큼만.
    """
    if len(records) == 0:
        return [], []
    positions = []
    labels = []
    if len(records) <= n_ticks:
        step = 1
    else:
        step = (len(records) - 1) // (n_ticks - 1)
        if step < 1:
            step = 1
    index = 0
    while index < len(records):
        positions.append(index)
        labels.append(str(records[index]["frame_number"]))
        index = index + step
    # 마지막 점 보정: 이전 tick 과 충분히 떨어졌을 때만 추가
    min_distance = max(len(records) // (n_ticks * 2), 5)
    if len(records) - 1 - positions[-1] >= min_distance:
        positions.append(len(records) - 1)
        labels.append(str(records[-1]["frame_number"]))
    return positions, labels


def draw_control_chart(axis, sequence_key, records, matched_alerts, show_incapable_banner,
                       compact=False):
    """
    한 시퀀스의 관리도를 axis 에 그린다.
    compact=True 는 요약 격자용 - 제목/배너/폰트를 줄이고 라벨을 생략한다.
    """
    if len(records) == 0:
        axis.text(0.5, 0.5, "no data", ha="center", va="center", transform=axis.transAxes)
        return

    x_values = []
    y_defect = []
    y_ewma = []
    index = 0
    while index < len(records):
        x_values.append(index)
        y_defect.append(records[index]["defect_ratio"])
        y_ewma.append(records[index]["ewma"])
        index = index + 1

    center_value = records[0]["center"]
    ucl_value = records[0]["ucl"]
    capability_label = judge_capability(center_value)

    # y 축 상한: max(defect_ratio, UCL) 여유
    y_max = max(max(y_defect), ucl_value) * 1.10
    if y_max < 0.05:
        y_max = 0.05

    if compact:
        scatter_size = 4
        ewma_width = 1.2
        marker_size = 20
    else:
        scatter_size = 8
        ewma_width = 1.6
        marker_size = 45

    # 원 데이터 산점도
    axis.scatter(x_values, y_defect, s=scatter_size, color=COLOR_SCATTER, alpha=0.55,
                 label="이상 패치 비율", zorder=1)

    # EWMA 실선
    axis.plot(x_values, y_ewma, color=COLOR_EWMA, linewidth=ewma_width, label="EWMA (λ=0.2)",
              zorder=3)

    # 중심선 / UCL
    axis.axhline(center_value, color=COLOR_CENTER, linestyle="--", linewidth=1.0,
                 alpha=0.9, label="Center = " + format(center_value, ".3f"))
    axis.axhline(ucl_value, color=COLOR_UCL, linestyle="--", linewidth=1.0,
                 alpha=0.9, label="UCL = " + format(ucl_value, ".3f"))

    # 알람 표시 (규칙별로 그룹화하여 한 번씩 그린다 - 범례 중복 방지)
    alerts_by_rule = {}
    for alert in matched_alerts:
        rule_name = alert["rule"]
        if rule_name not in alerts_by_rule:
            alerts_by_rule[rule_name] = []
        alerts_by_rule[rule_name].append(alert)

    plotted_labels = set()
    for rule_name in ALERT_STYLE:
        if rule_name not in alerts_by_rule:
            continue
        style = ALERT_STYLE[rule_name]
        alert_x = []
        alert_y = []
        for alert in alerts_by_rule[rule_name]:
            observation_index = alert["index"]
            alert_x.append(observation_index)
            # y 는 원 defect_ratio 위치에 찍는다 (알람이 어느 값에서 발생했는지 보이도록)
            alert_y.append(records[observation_index]["defect_ratio"])

        label_text = style["label"]
        if label_text in plotted_labels:
            legend_label = None
        else:
            legend_label = label_text
            plotted_labels.add(label_text)

        if style["marker"] == "x":
            axis.scatter(alert_x, alert_y, marker=style["marker"], color=style["color"],
                         s=marker_size, linewidths=1.4, label=legend_label, zorder=5)
        else:
            axis.scatter(alert_x, alert_y, marker=style["marker"], color=style["color"],
                         s=marker_size, edgecolors="black", linewidths=0.6,
                         label=legend_label, zorder=5)

    # 축 스타일
    axis.set_ylim(0.0, y_max)
    axis.set_xlim(-len(records) * 0.02, len(records) * 1.02)
    axis.grid(True, alpha=0.25)

    if compact:
        n_ticks = 3
        tick_fontsize = 7
        axis.set_ylabel("이상 비율", fontsize=8)
        axis.tick_params(axis="both", labelsize=tick_fontsize)
    else:
        n_ticks = 5
        axis.set_ylabel("이상 패치 비율")
        axis.set_xlabel("frame_number (관측 순서로 배치, 불균등 간격)")

    # x 축에 frame_number 표시
    positions, labels = frame_number_tick_labels(records, n_ticks)
    axis.set_xticks(positions)
    axis.set_xticklabels(labels)

    # 제목 - compact 에서는 짧게
    if compact:
        run_id = sequence_key[0]
        gap = sequence_key[1]
        # position 은 생략, run+gap+판정만
        title_short = run_id + " / " + str(gap) + "µm — " + capability_label
        title_short = title_short + " (c=" + format(center_value, ".2f") + ")"
        axis.set_title(title_short, fontsize=10, loc="left",
                       color=capability_color(capability_label), fontweight="bold")
    else:
        title = format_sequence_title(sequence_key, center_value, capability_label)
        axis.set_title(title, fontsize=11, loc="left",
                       color=capability_color(capability_label), fontweight="bold")

    # INCAPABLE 배너
    if show_incapable_banner and capability_label == "INCAPABLE":
        if compact:
            banner_text = "공정능력 붕괴"
            banner_fontsize = 7
        else:
            banner_text = "공정능력 붕괴 — 관리도 기반 모니터링 무의미"
            banner_fontsize = 9
        axis.text(0.5, 0.94, banner_text,
                  transform=axis.transAxes, ha="center", va="top",
                  fontsize=banner_fontsize, color="white", fontweight="bold",
                  bbox=dict(facecolor="#d62728", edgecolor="none",
                            boxstyle="round,pad=0.35", alpha=0.85))


def draw_alert_strip(axis, records, matched_alerts):
    """
    각 관측 인덱스에서 어떤 규칙이 발동했는지를 색 마커로 표시하는 아래 스트립.
    """
    if len(records) == 0:
        return

    # upper/lower 를 같은 라벨로 묶는다 - 같은 규칙의 상하 방향은 같은 행에 표시
    rule_order = ["rule1_upper", "rule1_lower", "rule2_run9", "rule3_trend_up",
                  "rule3_trend_down", "ewma_upper", "ewma_lower"]

    y_positions = {}
    y_labels = []
    label_to_position = {}
    y_index = 0
    for rule_name in rule_order:
        # 실제 발생한 규칙만 표시 (스트립을 콤팩트하게)
        has_this_rule = False
        for alert in matched_alerts:
            if alert["rule"] == rule_name:
                has_this_rule = True
                break
        if has_this_rule is False:
            continue

        label_text = ALERT_STYLE[rule_name]["label"]
        if label_text in label_to_position:
            y_positions[rule_name] = label_to_position[label_text]
        else:
            y_positions[rule_name] = y_index
            label_to_position[label_text] = y_index
            y_labels.append(label_text)
            y_index = y_index + 1

    if y_index == 0:
        axis.text(0.5, 0.5, "알람 없음", ha="center", va="center",
                  transform=axis.transAxes, fontsize=9, color="#666666")
        axis.set_xticks([])
        axis.set_yticks([])
        return

    for alert in matched_alerts:
        rule_name = alert["rule"]
        if rule_name not in y_positions:
            continue
        style = ALERT_STYLE[rule_name]
        if style["marker"] == "x":
            axis.scatter([alert["index"]], [y_positions[rule_name]],
                         marker=style["marker"], color=style["color"],
                         s=30, linewidths=1.2)
        else:
            axis.scatter([alert["index"]], [y_positions[rule_name]],
                         marker=style["marker"], color=style["color"],
                         s=30, edgecolors="black", linewidths=0.4)

    axis.set_xlim(-len(records) * 0.02, len(records) * 1.02)
    axis.set_ylim(-0.5, y_index - 0.5)
    axis.set_yticks(list(range(y_index)))
    axis.set_yticklabels(y_labels, fontsize=8)
    axis.set_xticks([])
    axis.grid(True, axis="x", alpha=0.2)
    axis.set_title("알람 발생 지점", fontsize=9, loc="left", color="#555555")


def render_single_sequence(sequence_key, records, matched_alerts, output_path):
    """
    시퀀스 한 개를 상단(관리도) + 하단(알람 스트립) 2단 그림으로 저장한다.
    """
    figure = plt.figure(figsize=(11, 5.5))
    grid = figure.add_gridspec(2, 1, height_ratios=[3.0, 1.0], hspace=0.35)

    axis_main = figure.add_subplot(grid[0])
    axis_strip = figure.add_subplot(grid[1])

    draw_control_chart(axis_main, sequence_key, records, matched_alerts,
                       show_incapable_banner=True)
    draw_alert_strip(axis_strip, records, matched_alerts)

    # 범례는 상단 밖에 배치
    handles, labels = axis_main.get_legend_handles_labels()
    if len(handles) > 0:
        axis_main.legend(handles, labels, loc="upper right", fontsize=8,
                         framealpha=0.9, ncol=2)

    figure.savefig(output_path, dpi=140, bbox_inches="tight")
    plt.close(figure)


def render_summary_grid(sequences_ordered, all_records, all_matched_alerts, output_path):
    """
    8개 시퀀스를 2행 4열 격자로 배치한 요약 도표.
    갭 오름차순으로 정렬하여 공정능력이 갭 증가에 따라 붕괴되는 흐름을 보이게 한다.
    """
    n_sequences = len(sequences_ordered)
    n_cols = 4
    n_rows = (n_sequences + n_cols - 1) // n_cols

    figure = plt.figure(figsize=(22, 5.0 * n_rows))
    grid = figure.add_gridspec(n_rows, n_cols, hspace=0.55, wspace=0.32)

    axis_index = 0
    for sequence_key in sequences_ordered:
        row = axis_index // n_cols
        col = axis_index % n_cols
        axis = figure.add_subplot(grid[row, col])

        records = all_records[sequence_key]
        matched_alerts = all_matched_alerts[sequence_key]
        draw_control_chart(axis, sequence_key, records, matched_alerts,
                           show_incapable_banner=True, compact=True)

        # 범례 생략 (격자에서는 공간 부족)
        legend = axis.get_legend()
        if legend is not None:
            legend.remove()

        axis_index = axis_index + 1

    figure.suptitle("SPC 관리도 요약 — 코팅 갭 증가에 따른 공정능력 붕괴",
                    fontsize=14, fontweight="bold", y=0.995)

    # 하단 공용 범례
    legend_handles = []
    legend_handles.append(plt.Line2D([0], [0], marker="o", color=COLOR_SCATTER,
                                     linestyle="", markersize=6, label="이상 패치 비율"))
    legend_handles.append(plt.Line2D([0], [0], color=COLOR_EWMA, linewidth=1.6,
                                     label="EWMA"))
    legend_handles.append(plt.Line2D([0], [0], color=COLOR_CENTER, linestyle="--",
                                     label="Center"))
    legend_handles.append(plt.Line2D([0], [0], color=COLOR_UCL, linestyle="--",
                                     label="UCL"))
    for rule_name in ["rule1_upper", "rule2_run9", "rule3_trend_up", "ewma_upper"]:
        style = ALERT_STYLE[rule_name]
        legend_handles.append(plt.Line2D([0], [0], marker=style["marker"],
                                         color=style["color"], linestyle="",
                                         markersize=7, label=style["label"]))

    figure.legend(handles=legend_handles, loc="lower center",
                  bbox_to_anchor=(0.5, -0.02), ncol=8, fontsize=9,
                  frameon=True)

    figure.savefig(output_path, dpi=130, bbox_inches="tight")
    plt.close(figure)


# ===============================================================
# 실행
# ===============================================================

def resolve_korean_font():
    """
    한글 폰트가 있으면 사용, 없으면 경고만.
    시스템에 나눔고딕/NanumGothic 계열이 흔하다.
    """
    candidates = [
        "NanumGothic",
        "Noto Sans CJK KR",
        "Noto Sans KR",
        "AppleGothic",
        "Malgun Gothic",
    ]
    from matplotlib import font_manager
    available = set()
    for font in font_manager.fontManager.ttflist:
        available.add(font.name)

    for name in candidates:
        if name in available:
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False
            return name
    return None


def format_position_slug(position):
    """파일명에 안전한 형태로 position 정규화"""
    slug = position.replace(" ", "-")
    return slug


def main():
    if os.path.exists(RESULT_PATH) is False:
        print("results_spc.csv 를 찾을 수 없습니다.")
        return
    if os.path.exists(ALERT_PATH) is False:
        print("results_spc_alerts.csv 를 찾을 수 없습니다.")
        return

    if os.path.exists(FIGURE_DIR) is False:
        os.makedirs(FIGURE_DIR)

    font_name = resolve_korean_font()
    if font_name is None:
        print("경고: 한글 폰트를 찾지 못했습니다. 라벨이 깨질 수 있습니다.")
    else:
        print("한글 폰트:", font_name)

    print("SPC 결과 로딩 중...")
    sequences = load_spc_records(RESULT_PATH)
    alerts_by_sequence = load_alerts(ALERT_PATH)

    # 알람을 관측 인덱스로 매핑
    matched_by_sequence = {}
    for key in sequences:
        records = sequences[key]
        if key in alerts_by_sequence:
            alerts_list = alerts_by_sequence[key]
        else:
            alerts_list = []
        matched_by_sequence[key] = match_alerts_to_indices(records, alerts_list)

    # 정렬: 갭 오름차순, 같은 갭이면 run, position 순
    sequence_keys = list(sequences.keys())

    def sort_key(item):
        run_id = item[0]
        gap = item[1]
        position = item[2]
        return (gap, run_id, position)

    sequence_keys.sort(key=sort_key)

    print()
    print("시퀀스 요약:")
    print("-" * 82)
    for key in sequence_keys:
        records = sequences[key]
        center = records[0]["center"]
        capability = judge_capability(center)
        matched_count = len(matched_by_sequence[key])
        text = "  " + str(key[0]) + " / " + str(key[1]) + "µm / " + str(key[2])
        print(text)
        print("    관측:", len(records), "| center:", format(center, ".3f"),
              "| 판정:", capability, "| 알람:", matched_count)
    print("-" * 82)
    print()

    # 개별 그림
    print("개별 시퀀스 그림 저장 중...")
    for key in sequence_keys:
        run_id = key[0]
        gap = key[1]
        position = key[2]
        slug = format_position_slug(position)
        filename = "spc_" + run_id + "_" + str(gap) + "_" + slug + ".png"
        output_path = os.path.join(FIGURE_DIR, filename)

        render_single_sequence(key, sequences[key], matched_by_sequence[key], output_path)
        print("  저장:", output_path)

    # 요약 격자
    print()
    print("요약 격자 저장 중...")
    summary_path = os.path.join(FIGURE_DIR, "spc_summary.png")
    render_summary_grid(sequence_keys, sequences, matched_by_sequence, summary_path)
    print("  저장:", summary_path)

    print()
    print("완료.")


main()
