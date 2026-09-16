"""
63. 라인 속도 환산 (§25)

논문 (Sci Data 2026, DOI 10.1038/s41597-025-06419-1) 확인값 사용:
  - 코팅 속도 0.5 m/min = 8.333 mm/s (실측)
  - 코팅 폭 25 mm (실측)
  - 코팅 거리 75 mm (실측, 참고)

저장소 실측 처리 시간 (docs/metrics.md §3-2):
  - A3 median 10.598 ms/patch, p90 12.651 ms/patch
  - YOLO (infer+전후처리) median 49.895, p90 93.241 ms/patch
  - U-Net Seg CPU median 450.28, p90 487.87 ms/patch

프레임당 patch 수: 3×3 격자 = 9 (§15-6). 인라인은 결함 유무 무관 전 patch 처리
필요하므로 항상 9.

새 추론 없음. 처리 시간·구조 값은 metrics.md·experiment_log 인용.
"""

# =====================================================================
# 입력값
# =====================================================================
# 처리 시간 (ms/patch)
STAGE_MS = {
    "A3":       {"median": 10.598, "p90": 12.651},
    "YOLO":     {"median": 46.827 + 3.068, "p90": 86.764 + 6.477},
    "Seg(CPU)": {"median": 450.28, "p90": 487.87},
    "형태":     {"median": 0.001,  "p90": 0.247},
}
PATCHES_PER_FRAME = 9  # 3×3 격자, §15-6

# 논문 실측
COATING_SPEED_M_PER_MIN = 0.5     # 논문
COATING_SPEED_MM_PER_S  = COATING_SPEED_M_PER_MIN * 1000 / 60  # 8.333
COATING_WIDTH_MM = 25              # 논문

# FOV 시나리오 (mm/frame) — 코팅 폭 25 mm 기준
FOV_SCENARIOS = [10, 25, 50]

# 상용 라인 시나리오 (m/min)
COMMERCIAL_LINES = [20, 50, 80]


def frame_time_ms(stage_ms_per_patch):
    """프레임 처리 시간 = patch 처리 시간 × 9."""
    return stage_ms_per_patch * PATCHES_PER_FRAME


def max_line_speed_mm_per_s(frame_ms, fov_mm):
    """감당 가능한 최대 라인 속도. frame 처리 완료 시 라인이 최대 FOV 만큼 이동."""
    return fov_mm / (frame_ms / 1000.0)


def to_m_per_min(mm_per_s):
    return mm_per_s * 60 / 1000.0


def main():
    print("=" * 100)
    print(f"§25 라인 속도 환산 — 논문 실측 조건 (코팅 속도 0.5 m/min = 8.333 mm/s, 코팅 폭 25 mm)")
    print("=" * 100)

    # ---------------------------------------------------
    # 1. 프레임당 처리 시간 (스테이지별)
    # ---------------------------------------------------
    print()
    print(f"[1] 프레임당 처리 시간 (patch × {PATCHES_PER_FRAME} 개, 인라인 전수 처리)")
    print(f"{'스테이지':<12}{'ms/patch (median)':>20}{'ms/frame (median)':>22}{'ms/frame (p90)':>18}")
    stage_frame_ms = {}
    for name, ms in STAGE_MS.items():
        med_frame = frame_time_ms(ms["median"])
        p90_frame = frame_time_ms(ms["p90"])
        stage_frame_ms[name] = {"median": med_frame, "p90": p90_frame}
        print(f"{name:<12}{ms['median']:>20.3f}{med_frame:>22.2f}{p90_frame:>18.2f}")

    # 풀 파이프라인
    full_med = sum(v["median"] for v in stage_frame_ms.values())
    full_p90 = sum(v["p90"] for v in stage_frame_ms.values())
    stage_frame_ms["Full 파이프라인"] = {"median": full_med, "p90": full_p90}
    print(f"{'Full 파이프라인':<12}{'-':>20}{full_med:>22.2f}{full_p90:>18.2f}")

    # ---------------------------------------------------
    # 2. 0.5 m/min 라인 · FOV 시나리오별 필요 프레임 처리율 vs 현행 능력
    # ---------------------------------------------------
    print()
    print(f"[2] 코팅 속도 {COATING_SPEED_M_PER_MIN} m/min ({COATING_SPEED_MM_PER_S:.3f} mm/s) 라인")
    print(f"    FOV 시나리오별 필요 프레임 처리 시간 (= FOV / 라인속도)")
    print()
    print(f"{'FOV (mm)':<12}{'필요 프레임 주기 (ms)':>25}{'필요 프레임율 (fps)':>22}")
    for fov in FOV_SCENARIOS:
        req_ms = fov / COATING_SPEED_MM_PER_S * 1000
        req_fps = 1000 / req_ms
        print(f"{fov:<12}{req_ms:>25.1f}{req_fps:>22.4f}")

    print()
    print(f"[3] 스테이지별 감당 여유 (FOV 시나리오 × 스테이지). '여유 배수' = 감당 최대 라인속도 / 실측 0.5 m/min")
    print(f"    감당 최대 = FOV / 프레임 처리 시간 (median)")
    print()
    header = f"{'스테이지':<18}"
    for fov in FOV_SCENARIOS:
        header += f"{'FOV=' + str(fov) + ' mm':>22}"
    print(header)
    for name in list(stage_frame_ms.keys()):
        fmed = stage_frame_ms[name]["median"]
        line = f"{name:<18}"
        for fov in FOV_SCENARIOS:
            max_mmps = max_line_speed_mm_per_s(fmed, fov)
            max_mmin = to_m_per_min(max_mmps)
            margin = max_mmin / COATING_SPEED_M_PER_MIN
            marker = "✓" if margin >= 1.0 else "✗"
            line += f"{max_mmin:>10.3f} m/min ({margin:>4.2f}×) {marker}"
        print(line)

    # ---------------------------------------------------
    # 4. 상용 라인 시나리오 (20/50/80 m/min)
    # ---------------------------------------------------
    print()
    print(f"[4] 상용 라인 시나리오 — 필요 처리 시간 · 현행 부족 배수")
    print(f"    FOV=25 mm (= 코팅 폭 전체) 기준")
    print()
    header = f"{'라인속도 (m/min)':<20}{'필요 프레임 주기 (ms)':>25}"
    for stg in stage_frame_ms.keys():
        header += f"{stg + ' (부족 배)':>22}"
    print(header)
    fov = 25
    for line_speed in COMMERCIAL_LINES:
        line_mmps = line_speed * 1000 / 60
        req_ms = fov / line_mmps * 1000
        line = f"{line_speed:<20}{req_ms:>25.2f}"
        for name in stage_frame_ms.keys():
            fmed = stage_frame_ms[name]["median"]
            shortfall = fmed / req_ms  # > 1 이면 부족
            marker = "OK" if shortfall <= 1.0 else "부족"
            line += f"{shortfall:>17.2f}× {marker:<3}"
        print(line)

    # ---------------------------------------------------
    # 5. FOV=10, 50 시나리오도 요약
    # ---------------------------------------------------
    print()
    print(f"[5] 상용 라인 20 m/min 시나리오 — FOV 별 요약")
    for fov in FOV_SCENARIOS:
        line_mmps = 20 * 1000 / 60
        req_ms = fov / line_mmps * 1000
        print(f"  FOV={fov} mm: 필요 {req_ms:.2f} ms/frame")
        for name in stage_frame_ms.keys():
            fmed = stage_frame_ms[name]["median"]
            shortfall = fmed / req_ms
            marker = "OK" if shortfall <= 1.0 else "부족"
            print(f"     {name:<18} 현행 {fmed:>7.1f} ms/frame  →  {shortfall:.2f}× {marker}")


main()
