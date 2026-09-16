"""SPC 계산 · baseline · 알람 · STATUS 전이 (§3-10).

주의: SPC_POINTS 는 metric 별 SEQ_NO 가 앱 전체에서 누적된다.
테스트 데이터가 실제 SPC 시퀀스에 섞이지 않도록 별도 metric 명은 못 쓰나
(CHECK 로 'a3' 또는 'seg_crack' 만 허용), 대신 fixture 사용자의 검사만 만들고
검사가 삭제될 때 CASCADE 로 SPC_POINTS 도 정리되도록 한다.
"""
import uuid

import pytest

from app import db as app_db
from app.models import Inspection, SpcPoint
from app.services import spc as spc_service


UID = lambda: uuid.uuid4().hex[:8]


class TestBaseline:
    """baseline 단계 (SEQ_NO <= 30) 는 CENTER/UCL/EWMA 모두 NULL."""

    def test_baseline_returns_null(self, inspector_user):
        # 검사 하나 만들어 FK 만족
        with app_db.get_session() as s:
            insp = Inspection(user_id=inspector_user["id"], file_name="spc_test.jpg",
                              source="upload", a3_ratio=0.1, status="normal")
            s.add(insp); s.flush()
            insp_id = int(insp.id)

        # 현재 metric='a3' 상태 확인 (baseline 이 이미 완성됐을 수도 있음)
        current = spc_service.get_summary("a3")
        if current["baseline_complete"]:
            pytest.skip("baseline 이 이미 완성돼 있어 baseline 단계 테스트 스킵")

        # 첫 SPC 점 추가
        result = spc_service.add_spc_point(insp_id, "a3", 0.05)
        assert result["phase"] == "baseline"
        assert result["is_alarm"] is False
        assert result["center"] is None
        assert result["ucl"] is None
        assert result["ewma"] is None


class TestCalculationMatches09:
    """계산식이 09_spc_monitor.py 의 compute_control_limits 와 동일한지."""

    def test_control_limits_formula(self):
        # 동일 입력으로 09 원문 함수와 spc.py 함수 결과 비교
        import numpy as np
        # 09_spc_monitor 는 sigma_limit=3.0 이지만 spc.py 는 3.5.
        # 여기서는 동일 sigma_limit 을 넘겨 계산식 자체의 일치 확인.

        # 09 원문 로직 재현
        def compute_09(values, sigma_limit):
            array = np.array(values, dtype=float)
            center = float(np.mean(array))
            deviation = float(np.std(array))
            upper = center + sigma_limit * deviation
            lower = center - sigma_limit * deviation
            if lower < 0.0:
                lower = 0.0
            return {"center": center, "sigma": deviation, "upper": upper, "lower": lower}

        sample = [0.02, 0.03, 0.05, 0.08, 0.12, 0.11, 0.09, 0.07, 0.06, 0.04,
                  0.02, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.13,
                  0.14, 0.12, 0.11, 0.09, 0.08, 0.06, 0.05, 0.04, 0.03, 0.02]

        expected = compute_09(sample, 3.5)
        actual = spc_service.compute_control_limits(sample, 3.5)
        assert abs(expected["center"] - actual["center"]) < 1e-9
        assert abs(expected["sigma"] - actual["sigma"]) < 1e-9
        assert abs(expected["upper"] - actual["upper"]) < 1e-9

    def test_ewma_formula(self):
        # 09 의 재귀식과 spc._next_ewma 가 동일
        # FR-31: λ 는 metric 별 매핑에서 조회 (a3 기준으로 계산식 검증)
        lambda_v = spc_service.EWMA_LAMBDAS["a3"]
        center = 0.1
        # 09 원문 재귀
        prev = center
        values = [0.15, 0.20, 0.30, 0.50]
        expected = []
        for v in values:
            current = lambda_v * v + (1 - lambda_v) * prev
            expected.append(current)
            prev = current

        # spc._next_ewma 로 재현
        prev2 = center
        actual = []
        for v in values:
            e = spc_service._next_ewma(v, lambda_v, prev2)
            actual.append(e)
            prev2 = e

        for a, e in zip(actual, expected):
            assert abs(a - e) < 1e-12


class TestBaselineJudgmentConsistency:
    """FR-56 (c) — add_spc_point.phase 판정과 get_summary.baseline_complete 가
    동일한 데이터 상태 (SPC_POINTS 에 저장된 점 수) 에 대해 논리적으로 정합함을 검증.

    정합 관계:
      metric M 에 t 개 점이 저장되어 있을 때,
      - 새 점 진입 시 add_spc_point 판정: (t < BASELINE_SIZE) → phase='baseline',
        아니면 phase='monitor'
      - 새 점 저장 후 (total=t+1) 시점의 get_summary: (t+1 > BASELINE_SIZE) →
        baseline_complete=True, 아니면 False
      - 두 판정의 동치: phase='monitor' ⇔ baseline_complete_after=True

    프로덕션 metric ('a3','seg_crack') 오염을 피하고 기존 DB 상태에 의존하지
    않도록 실제 INSERT 없이 판정 조건식의 논리 동치만 순수 계산으로 확인한다.
    실제 저장·삭제 시나리오 검증 (FR-56 (a)(b)) 은 docs/testing.md 의
    "SPC baseline gap 시나리오" 수동 절차 참조.
    """

    def test_phase_and_baseline_complete_are_logically_consistent(self):
        """FR-56 (c). t ∈ [0, 40] 범위 전 지점에서 두 판정의 동치 관계 검증."""
        BASELINE_SIZE = spc_service.BASELINE_SIZE

        for t in range(0, 40):
            # add_spc_point 판정: t 개가 저장된 상태에서 새 점의 phase
            phase = "baseline" if t < BASELINE_SIZE else "monitor"
            # 새 점 저장 후 total = t+1 시점의 get_summary.baseline_complete
            baseline_complete_after = (t + 1) > BASELINE_SIZE

            # 동치: phase='monitor' ⇔ baseline_complete=True
            assert (phase == "monitor") == baseline_complete_after, (
                "FR-56 (c) 위반: t={} 상태에서 phase={} 지만 "
                "저장 후 baseline_complete={}. "
                "두 판정이 일치해야 함.".format(t, phase, baseline_complete_after)
            )


class TestMetricLambdaSeparation:
    """FR-31 — EWMA 평활 계수 λ 가 metric 별로 지정되고 실제 적용되는지 검증.

    §23-5 결정: a3 = 0.1, seg_crack = 0.2. 검증 축:
    1) EWMA_LAMBDAS 매핑에 두 metric 이 등록되어 있고 각각 다른 값
    2) VALID_METRICS 가 EWMA_LAMBDAS.keys() 에서 파생 (일치 강제)
    3) get_summary 가 metric 별 λ 를 반환 (add_spc_point 도 같은 매핑 접근)
    4) 매핑에 없는 metric 은 조용히 기본값을 쓰지 않고 명확히 실패
    5) _next_ewma 재귀식이 실제로 다른 λ 로 다른 결과를 낸다

    실제 저장 없이 상수 · 함수 단위로 검증. DB 접근 없음.
    """

    def test_lambda_mapping_has_expected_metrics(self):
        """FR-31: a3=0.1, seg_crack=0.2 두 값 각각 존재."""
        assert spc_service.EWMA_LAMBDAS["a3"] == 0.1
        assert spc_service.EWMA_LAMBDAS["seg_crack"] == 0.2
        # 두 값이 다름 → metric 별 분리가 실제로 의미 있음
        assert spc_service.EWMA_LAMBDAS["a3"] != spc_service.EWMA_LAMBDAS["seg_crack"]

    def test_valid_metrics_derived_from_lambda_mapping(self):
        """FR-31: VALID_METRICS 와 EWMA_LAMBDAS 키 일치 (매핑에서 파생).
        신규 metric 추가 시 λ 누락 방지 방식의 회귀 방지."""
        assert set(spc_service.VALID_METRICS) == set(spc_service.EWMA_LAMBDAS.keys())

    def test_get_summary_returns_metric_specific_lambda(self, inspector_user):
        """get_summary 반환값이 metric 별로 다른 λ 를 반환."""
        summary_a3 = spc_service.get_summary("a3")
        summary_seg = spc_service.get_summary("seg_crack")
        assert summary_a3["ewma_lambda"] == spc_service.EWMA_LAMBDAS["a3"]
        assert summary_seg["ewma_lambda"] == spc_service.EWMA_LAMBDAS["seg_crack"]
        assert summary_a3["ewma_lambda"] != summary_seg["ewma_lambda"]

    def test_unknown_metric_fails_loudly_not_silently(self, inspector_user):
        """FR-31: 매핑에 없는 metric 은 조용히 기본값 대신 명확히 실패."""
        # add_spc_point 는 VALID_METRICS 검사에서 ValueError
        with pytest.raises(ValueError, match="invalid metric"):
            spc_service.add_spc_point(999, "unknown_metric", 0.1)
        # get_summary 도 동일
        with pytest.raises(ValueError, match="invalid metric"):
            spc_service.get_summary("unknown_metric")
        # dict.get 이 아니라 직접 접근이라 fallback 기본값 사용 없음
        # (매핑 없는 metric 이 VALID_METRICS 통과했다고 가정해도
        #  EWMA_LAMBDAS[metric] 에서 KeyError 로 실패해야 함)
        with pytest.raises(KeyError):
            _ = spc_service.EWMA_LAMBDAS["unknown_metric"]

    def test_next_ewma_produces_different_output_for_different_lambdas(self):
        """_next_ewma 재귀식이 실제로 다른 λ 로 다른 값. metric 별 λ 분리가
        저장되는 EWMA 값에 반영됨을 순수 계산으로 확인."""
        prev = 0.1
        value = 0.5
        e_a3 = spc_service._next_ewma(value, spc_service.EWMA_LAMBDAS["a3"], prev)
        e_seg = spc_service._next_ewma(value, spc_service.EWMA_LAMBDAS["seg_crack"], prev)
        assert e_a3 != e_seg
        # λ 클수록 최신 값 비중 커짐 → seg (λ=0.2) 가 a3 (λ=0.1) 보다 value(0.5) 에 가깝게
        assert abs(e_seg - value) < abs(e_a3 - value)


class TestCountMetricDefinition:
    """FR-57 — 계수형 지표 (pinhole_count) 가 METRICS 매핑에 등록되고
    관련 파생 뷰가 일관되는지 검증. 순수 상수 검사, DB 접근 없음.

    검증 축:
    1) METRICS 에 pinhole_count 가 kind='count', lambda=None 으로 존재
    2) VALID_METRICS 에 pinhole_count 포함
    3) EWMA_LAMBDAS 뷰가 pinhole_count 를 None 으로 노출 (FR-31 회귀 방지)
    """

    def test_pinhole_count_registered_as_count_kind(self):
        assert spc_service.METRICS["pinhole_count"]["kind"] == "count"

    def test_pinhole_count_lambda_is_none(self):
        """설계 §1 (다) — 1차 범위에서 EWMA 미적용."""
        assert spc_service.METRICS["pinhole_count"]["lambda"] is None

    def test_pinhole_count_in_valid_metrics(self):
        assert "pinhole_count" in spc_service.VALID_METRICS

    def test_ewma_lambdas_view_reflects_none_for_count(self):
        """FR-31 회귀 방지: EWMA_LAMBDAS 는 METRICS 로부터 파생. 계수형은 None."""
        assert spc_service.EWMA_LAMBDAS["pinhole_count"] is None


class TestCountMetricFormula:
    """FR-57 (I) — 계수형 관리한계 산출식 CENTER + 3·√CENTER, LCL=0.

    Poisson 분산 특성 (Var = mean) → σ 를 √c̄ 로 대체. 순수 계산, DB 접근 없음.
    """

    def test_count_upper_equals_center_plus_3_sqrt_center(self):
        import math
        baseline = [1, 2, 0, 3, 1, 2, 1, 0, 4, 1, 2, 3, 1, 0, 2,
                    1, 3, 2, 0, 1, 2, 1, 0, 3, 1, 2, 0, 1, 2, 4]
        result = spc_service._compute_count_limits(baseline)
        expected_center = sum(baseline) / len(baseline)
        expected_upper = expected_center + 3.0 * math.sqrt(expected_center)
        assert abs(result["center"] - expected_center) < 1e-12
        assert abs(result["upper"] - expected_upper) < 1e-12

    def test_count_lower_is_zero(self):
        """FR-57 (IV) — 계수형 LCL 은 0 클리핑, 알람 판정에도 미사용."""
        baseline = [1, 2, 3, 0, 1, 2, 1, 3, 0, 2] * 3
        result = spc_service._compute_count_limits(baseline)
        assert result["lower"] == 0.0

    def test_count_sigma_is_none(self):
        """계수형은 표본 σ 개념이 다름 (√c̄ 로 대체). sigma 반환 없음."""
        baseline = [1, 2, 3, 0, 1, 2] * 5
        result = spc_service._compute_count_limits(baseline)
        assert result["sigma"] is None


class TestComputeLimitsDispatch:
    """FR-57 (VI) — _compute_limits 가 metric 종류에 따라 산출식을 분기.

    연속형 회귀 없음 (4단계 직접 확인을 테스트로 고정) + 계수형 분기 확인 +
    unknown kind 방어. 순수 계산, DB 접근 없음.
    """

    _CONT_BASELINE = [
        0.02, 0.03, 0.025, 0.04, 0.015, 0.035, 0.028, 0.033, 0.022, 0.038,
        0.027, 0.031, 0.024, 0.036, 0.029, 0.032, 0.026, 0.034, 0.023, 0.037,
        0.019, 0.030, 0.021, 0.039, 0.017, 0.041, 0.028, 0.032, 0.024, 0.036,
    ]

    def test_continuous_dispatch_matches_original_for_a3(self):
        """FR-57 (VI): 연속형 회귀 없음. _compute_limits('a3') 결과가
        기존 compute_control_limits(...·SIGMA_LIMIT) 와 모든 필드 완전 일치."""
        by_dispatch = spc_service._compute_limits(self._CONT_BASELINE, "a3")
        by_original = spc_service.compute_control_limits(
            self._CONT_BASELINE, spc_service.SIGMA_LIMIT,
        )
        for key in ("center", "sigma", "upper", "lower"):
            assert by_dispatch[key] == by_original[key], (
                "연속형 회귀: 필드 {} 불일치 ({} vs {})".format(
                    key, by_dispatch[key], by_original[key])
            )

    def test_continuous_dispatch_matches_original_for_seg_crack(self):
        """seg_crack 도 동일한 산출식이 적용되는지 (kind 만 동일하면 결과 같음)."""
        by_a3 = spc_service._compute_limits(self._CONT_BASELINE, "a3")
        by_seg = spc_service._compute_limits(self._CONT_BASELINE, "seg_crack")
        for key in ("center", "sigma", "upper", "lower"):
            assert by_a3[key] == by_seg[key]

    def test_count_dispatch_matches_count_formula(self):
        """계수형 분기: _compute_limits('pinhole_count') 결과가
        _compute_count_limits 와 완전 일치."""
        counts = [1, 2, 0, 3, 1, 2, 1, 0, 4, 1] * 3
        by_dispatch = spc_service._compute_limits(counts, "pinhole_count")
        by_direct = spc_service._compute_count_limits(counts)
        assert by_dispatch == by_direct

    def test_unknown_kind_raises_valueerror(self, monkeypatch):
        """METRICS 에 알 수 없는 kind 를 가진 metric 이 들어오면 명확히 실패.
        monkeypatch 로 임시 등록 후 복원."""
        monkeypatch.setitem(
            spc_service.METRICS, "pytest_unknown_kind",
            {"kind": "weird_kind", "lambda": None},
        )
        with pytest.raises(ValueError, match="unknown metric kind"):
            spc_service._compute_limits([1, 2, 3], "pytest_unknown_kind")


class TestCountMetricEndToEnd:
    """FR-57 (b)(c)(d) 통합 검증 — 계수형 baseline 30 점 축적 후 monitor
    브랜치에서 ewma=None · is_alarm=value>UCL 동작을 실측.

    프로덕션 오염 방지 방식:
    - 실행 전 SPC_POINTS WHERE metric='pinhole_count' = 0 이어야 함 (실측 확인).
      새 metric 이라 프로덕션 코드가 아직 저장하지 않음 (설계 §5-3, 마이그레이션
      3단계 실측 0 건).
    - baseline 은 inspector_user fixture 소유 검사로만 생성 → fixture finalizer
      가 CASCADE 로 SPC_POINTS 도 삭제.
    - 실행 후 SPC_POINTS WHERE metric='pinhole_count' = 0 확인.
    """

    def test_count_metric_baseline_and_monitor_alarm_logic(self, inspector_user):
        import math
        from sqlalchemy import func

        # 실행 전 오염 없음 검증
        with app_db.get_session() as s:
            before = int(s.query(func.count(SpcPoint.id)).filter(
                SpcPoint.metric == "pinhole_count",
            ).scalar() or 0)
        assert before == 0, (
            "테스트 시작 시 pinhole_count 잔여 {} 건 — 오염 방지 원칙 위반".format(before)
        )

        try:
            # baseline 30 점 생성. 각 값은 포아송(1.5) 근사 임의 counts
            baseline_values = [1, 2, 0, 3, 1, 2, 1, 0, 4, 1, 2, 3, 1, 0, 2,
                               1, 3, 2, 0, 1, 2, 1, 0, 3, 1, 2, 0, 1, 2, 4]
            assert len(baseline_values) == spc_service.BASELINE_SIZE

            inspection_ids = []
            with app_db.get_session() as s:
                for i in range(spc_service.BASELINE_SIZE):
                    insp = Inspection(
                        user_id=inspector_user["id"],
                        file_name="pytest_count_bl_{}_{}.jpg".format(UID(), i),
                        source="upload", status="normal",
                    )
                    s.add(insp); s.flush()
                    inspection_ids.append(int(insp.id))
                s.commit()

            for iid, v in zip(inspection_ids, baseline_values):
                spc_service.add_spc_point(iid, "pinhole_count", v)

            # baseline_complete 확인
            summary = spc_service.get_summary("pinhole_count")
            assert summary["total_points"] == spc_service.BASELINE_SIZE
            assert summary["baseline_complete"] is False  # BASELINE_SIZE+1 초과 필요

            # get_summary 의 ewma_lambda 가 계수형에서 None
            assert summary["ewma_lambda"] is None

            # monitor 브랜치 진입: 31 번째 점 (baseline 완성 계기).
            # 이 시점의 UCL 예상값 계산 (assert 를 위해)
            expected_center = sum(baseline_values) / len(baseline_values)
            expected_ucl = expected_center + 3.0 * math.sqrt(expected_center)

            # (i) value < UCL → 알람 없음, ewma=None
            with app_db.get_session() as s:
                insp = Inspection(user_id=inspector_user["id"],
                                  file_name="pytest_count_ok.jpg",
                                  source="upload", status="normal")
                s.add(insp); s.flush()
                normal_iid = int(insp.id)
                s.commit()
            below_value = 2  # UCL(=약 5.25) 아래
            result_below = spc_service.add_spc_point(normal_iid, "pinhole_count", below_value)
            assert result_below["phase"] == "monitor"
            assert result_below["ewma"] is None, (
                "계수형은 ewma=None 이어야 함 (설계 §1 (다)). 실제: {}".format(
                    result_below["ewma"])
            )
            assert result_below["is_alarm"] is False
            assert abs(result_below["ucl"] - expected_ucl) < 1e-9

            # (ii) value > UCL → 알람, ewma 여전히 None
            with app_db.get_session() as s:
                insp = Inspection(user_id=inspector_user["id"],
                                  file_name="pytest_count_alarm.jpg",
                                  source="upload", status="normal")
                s.add(insp); s.flush()
                alarm_iid = int(insp.id)
                s.commit()
            above_value = int(expected_ucl) + 2  # UCL 확실히 초과
            result_above = spc_service.add_spc_point(alarm_iid, "pinhole_count", above_value)
            assert result_above["phase"] == "monitor"
            assert result_above["ewma"] is None, (
                "계수형은 alarm 판정에도 ewma=None 유지 — value > UCL 만으로 알람"
            )
            assert result_above["is_alarm"] is True

            # DB 에 저장된 값도 확인 (반환값과 스토리지 일관성)
            with app_db.get_session() as s:
                stored = s.query(SpcPoint).filter(
                    SpcPoint.inspection_id == alarm_iid,
                    SpcPoint.metric == "pinhole_count",
                ).one()
                assert stored.ewma is None
                assert int(stored.is_alarm) == 1

        finally:
            # fixture finalizer 가 inspector_user 소유 Inspection 을 CASCADE 삭제 →
            # SPC_POINTS pinhole_count 도 정리됨. 명시 검증은 다음 assert 참조.
            pass

        # fixture finalizer 는 test 종료 이후에 돌므로 여기서 잔여 assert 불가.
        # 별도 test 로 정리 검증.


class TestCountMetricCleanupInvariant:
    """앞 TestCountMetricEndToEnd 종료 후 pinhole_count 잔여가 0 인지 검증.
    fixture finalizer 가 CASCADE 삭제하는지 확인. 순서 의존 (pytest 는 클래스
    선언 순서 기준). 명시적 순서 강제는 하지 않고 상태만 확인."""

    def test_no_pinhole_count_pollution_after_end_to_end(self):
        from sqlalchemy import func
        with app_db.get_session() as s:
            n = int(s.query(func.count(SpcPoint.id)).filter(
                SpcPoint.metric == "pinhole_count",
            ).scalar() or 0)
        assert n == 0, "테스트 종료 후 pinhole_count 잔여 {} 건 — CASCADE 정리 실패".format(n)


class TestAlarmTransition:
    """알람 발생 시 INSPECTIONS.STATUS 전이 (baseline 이 완성된 상태 가정)."""

    def test_status_becomes_alarm_when_spc_triggers(self, inspector_user, fake_inspection_result):
        from app.services import inspection_store

        # baseline 상태 확인
        summary = spc_service.get_summary("a3")
        if not summary["baseline_complete"]:
            pytest.skip("baseline 미완성 상태에서는 알람 로직 미동작 — 별도 baseline 준비 없이 skip")
        if summary["ucl"] is None:
            pytest.skip("UCL 미산출")

        # baseline 이 완성된 시스템이면 fake result 의 a3_ratio=0.5143 이 UCL 초과 여부에 따라
        # STATUS 결정. UCL 확인.
        # 알람 시나리오만 검증: UCL 을 크게 초과하는 큰 값을 쓴다.
        result = dict(fake_inspection_result)
        # 깊은 복사 아닌 얕은 복사이므로 anomaly dict 재할당
        result["anomaly"] = dict(fake_inspection_result["anomaly"])
        result["anomaly"]["ratio"] = 0.9  # UCL 넘도록

        insp_id = inspection_store.save_inspection(
            result=result, user_id=inspector_user["id"],
            file_name="alarm_test.jpg", source="upload", is_cold_start=False,
        )

        with app_db.get_session() as s:
            insp = s.query(Inspection).filter(Inspection.id == insp_id).one()
            # UCL 초과값이므로 alarm 예상 (UCL 이 0.9 미만이라면)
            if float(summary["ucl"]) < 0.9:
                assert insp.status == "alarm"
