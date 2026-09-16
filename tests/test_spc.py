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
        lambda_v = 0.1
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
