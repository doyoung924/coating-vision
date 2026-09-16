# 테스트 (§3-10)

작성 시점: 2026-09-14. pytest + pytest-cov 기반 통합 테스트.

## 실행 방법

```bash
# 개발 의존성 설치 (최초 1회)
source .venv/bin/activate
pip install -r requirements-dev.txt

# Oracle Instant Client (thick 모드) 세팅. run.py 와 동일하게 이 export 가 필요.
# 실 경로는 .env 의 ORACLE_CLIENT_LIB 값 (예: /opt/oracle/instantclient_19_32)
export ORACLE_CLIENT_LIB=<Oracle Instant Client 경로>
export LD_LIBRARY_PATH=$ORACLE_CLIENT_LIB

# 전체 실행
pytest -v

# 커버리지
pytest --cov=app --cov-report=term-missing

# 특정 파일만
pytest tests/test_auth.py -v
```

**주의**: pytest 프로세스는 `LD_LIBRARY_PATH` 를 미리 export 해야 한다. 앱의
`ensure_ld_library_path()` 는 execvpe 로 재실행하는 방식이라 pytest 컨텍스트를
잃는다. `tests/conftest.py` 가 export 여부를 확인하고 없으면 오류로 종료한다.

## 테스트 구조

```
tests/
├── __init__.py
├── conftest.py               # app · client · 사용자 fixture · fake_inspection_result
├── test_auth.py              # 회원가입 · 로그인 · 비밀번호 변경 · 프로필
├── test_permissions.py       # 역할별 접근 매트릭스 (parametrize)
├── test_board.py             # 게시판 CRUD · CASCADE · XSS · 페이지네이션
├── test_inspection.py        # 검사 저장 · 상세 · 삭제 · CSV export
├── test_spc.py               # SPC 계산식 · baseline · 알람
└── test_admin.py             # 계정 관리 · 안전장치 · 저장소 정리
```

## DB 격리 전략

**선택**: (a) 실제 Oracle 에 테스트 전용 데이터 + finalizer 로 삭제.

### 근거

- **실제 DDL·CHECK·CASCADE·SEQUENCE·TRIGGER 동작 검증**: 테스트 전용 SQLite 등 다른 엔진은 Oracle 11g 특유 규약 (IDENTITY 없음 → SEQUENCE+TRIGGER, BOOLEAN 없음 → NUMBER(1)+CHECK) 을 검증하지 못한다. 이 프로젝트의 스키마 결정 중 상당 부분이 Oracle 11g 제약 대응이므로, 프로덕션 엔진과 동일 엔진에서 테스트해야 실효성이 있다.
- **테스트 사용자·게시글·검사는 UUID 접미사로 고유하게 생성**: `pytest_inspector_a1b2c3` 형태. 기존 계정 (admin/bob/manager1) 과 이름 충돌 없음.
- **각 fixture 의 finalizer 가 자신이 만든 데이터를 명시적으로 삭제**: `_delete_test_user(user_id)` 가 소유 검사 · 게시글 · 댓글 · reviewed_by 참조까지 정리 후 계정 삭제.
- **기존 데이터는 절대 건드리지 않는다**: 삭제·수정 대상은 `pytest_*` 접두사 사용자와 그 소유 데이터로 한정 (`user_id` 로 필터). 기존 admin(id=1), bob(id=2), manager1(id=3) 은 read-only.

### 격리 검증

3 회 연속 실행 후 DB 상태:

| 테이블 | 잔여 |
|---|---|
| `pytest_*` 접두사 사용자 | **0** |
| INSPECTIONS | 0 |
| POSTS | 0 |
| COMMENTS | 0 |
| SPC_POINTS | 0 |

기존 USERS 3계정 (admin/bob/manager1) 은 그대로 유지 (`is_active=1`, role 변경 없음).

## Fixture 요약

| fixture | 반환 | 정리 |
|---|---|---|
| `app` (session) | Flask app 인스턴스 | 없음 (세션 스코프) |
| `client` | 익명 test client | 없음 |
| `anon_client` | 익명 test client | 없음 |
| `inspector_user` | `{id, username, password, role, client}` (로그인된 client 포함) | 소유 데이터 CASCADE 삭제 + 계정 삭제 |
| `manager_user` | 위와 동일, role=manager | 위와 동일 |
| `admin_user` | 위와 동일, role=admin | 위와 동일. 기존 admin 하나 이상 있으므로 활성 admin ≥ 2 (안전장치 3 회피) |
| `fake_inspection_result` | pipeline.run_inspection 결과 fake dict | 없음 (in-memory) |

## 모델 로드·실제 추론 회피

- `save_inspection` 은 결과 dict 만 받으므로 **모델 로드 없이** `fake_inspection_result` fixture 를 그대로 인자로 전달
- `/inspect` POST 를 통과하는 테스트는 없음 (파이프라인 시간 오래 걸림). 대신 `services.inspection_store.save_inspection` 을 직접 호출해 저장 로직 검증
- test_client 는 `pytest_*` 사용자로 로그인 후 `/history/*`, `/board/*` 라우트를 실측

## 커버리지

`pytest --cov=app --cov-report=term-missing` 실행 결과:

| 모듈 | 커버리지 |
|---|---|
| **routes** 평균 | 65~90% |
| **services** 평균 | 66~86% |
| **ml/** (모델 로드 제외) | 4~49% |
| **auth_utils, config, logging_config, store, models** | 90~100% |
| **전체** | **69%** |

`ml/` 계층 커버리지가 낮은 이유: 테스트에서 모델 로드·실제 추론을 회피 (원칙: 느림·GPU/CPU 의존). 이 계층은 §2단계에서 `app_legacy.py` 대비 값 대조로 이미 검증됐고, 파이프라인 흐름은 `test_inspection.py` 가 `fake_inspection_result` 로 검증한다.

`routes/inspect.py` (45%) 는 파일 업로드 경로 · 모델 로드 분기 등이 미커버. `services/pipeline.py` (36%) 도 실제 추론이 미커버 (모델 fixture 없음). 정상.

## 실행 시간

전체 87 케이스, 3 회 연속 실행 결과:

| 실행 | 시간 | 결과 |
|---|---|---|
| 1회차 | 37.64 s | 86 passed, 1 skipped |
| 2회차 | 38.31 s | 86 passed, 1 skipped |
| 3회차 | 38.14 s | 86 passed, 1 skipped |

skipped 1건은 `test_status_becomes_alarm_when_spc_triggers` — baseline 미완성 상태에서는 SPC 알람 로직이 발동하지 않으므로 `pytest.skip()`. baseline 30점 채우기는 테스트 격리 원칙에 어긋나므로 (SPC_POINTS 는 metric 별로 SEQ_NO 가 앱 전체 누적) 스킵 유지. 알람 시나리오 자체는 §3-4 검증에서 실측 완료.

## 원칙

- **기존 데이터 오염 금지**: 모든 삭제는 `pytest_*` 접두사 사용자 소유로 한정
- **모델 로드 없음**: `fake_inspection_result` fixture 로 대체
- **통과시키려고 기능 코드 변경 금지**: 3-10 에서 발견된 버그 (`/admin/users` DEFAULT_PAGE_SIZE=30) 는 수정. 그 외 코드 변경 없음

---

## 수동 검증 시나리오

프로덕션 metric 오염을 피하기 위해 pytest 자동화 밖으로 분리한 시나리오.

### SPC baseline gap 시나리오 (FR-56 (a)(b))

**왜 자동화하지 않았는가**:
- `SPC_POINTS.METRIC` 은 `CHECK (METRIC IN ('a3','seg_crack'))` 로 두 값만 허용. 테스트 전용 metric 사용 불가
- 자동화를 위한 CHECK 완화는 앱을 우회한 경로의 잘못된 metric 유입을 막는 DB 제약의 목적과 배치됨 (`docs/design-spc-baseline.md` §5-3 D-1 탈락 사유)
- 프로덕션 metric='a3' 로 30점을 심으면 SEQ_NO 가 실 시퀀스에 섞임. 시나리오 실행 중 다른 세션이 검사를 저장하면 pytest 픽스처 데이터와 프로덕션 데이터 구분 불가

**실행 조건**:
1. `SPC_POINTS` 에서 `metric='a3'` 데이터가 비어 있는 상태에서만 실행 (실행 전 `SELECT COUNT(*) FROM SPC_POINTS WHERE metric='a3'` = 0 확인)
2. 다른 사용자가 검사를 실행하지 않는 시점 (야간 · 격리 환경) 에서 실행 권장
3. **프로덕션 라이브 DB 에서 실행 금지**. 개발용 로컬 Oracle 11g 인스턴스에서만

**재현 절차** (아래 스크립트를 `tools/manual_test_fr56.py` 로 저장하거나 REPL 에 붙여 실행):

```python
import uuid
from app import create_app
from app import db as app_db
from app.models import Inspection, SpcPoint, User
from app.services import spc as spc_service
from sqlalchemy import func

app = create_app()

# 실행 전 상태 캡처. 이미 데이터가 있으면 중단.
with app_db.get_session() as s:
    admin = s.query(User).filter(User.username == "admin").one()
    before_count = int(s.query(func.count(SpcPoint.id)).filter(
        SpcPoint.metric == "a3").scalar() or 0)
if before_count > 0:
    raise SystemExit("metric='a3' 에 기존 데이터 존재. 시나리오 중단 (오염 방지)")

tag = "fr56_" + uuid.uuid4().hex[:8]
inspection_ids = []
try:
    # 1) 30개 검사 · 30개 SPC 점 생성
    with app_db.get_session() as s:
        for i in range(30):
            insp = Inspection(user_id=admin.id,
                              file_name=tag + "_" + str(i) + ".jpg",
                              source="upload", a3_ratio=0.01 * (i + 1),
                              status="normal")
            s.add(insp); s.flush()
            inspection_ids.append(int(insp.id))
        s.commit()
    for i, iid in enumerate(inspection_ids):
        spc_service.add_spc_point(iid, "a3", 0.01 * (i + 1))

    # 2) 중간 10점 삭제 (CASCADE 로 SPC 도 삭제)
    delete_ids = inspection_ids[10:20]
    with app_db.get_session() as s:
        s.query(Inspection).filter(Inspection.id.in_(delete_ids)).delete(
            synchronize_session=False)
        s.commit()
    inspection_ids = [i for i in inspection_ids if i not in delete_ids]

    # 3) 상태 확인 (get_summary — FR-56 (b))
    sm = spc_service.get_summary("a3")
    print("(b) total_points=%d, baseline_complete=%s" % (
        sm["total_points"], sm["baseline_complete"]))
    assert sm["total_points"] == 20
    assert sm["baseline_complete"] is False

    # 4) 새 점 진입 (FR-56 (a))
    with app_db.get_session() as s:
        new_insp = Inspection(user_id=admin.id, file_name=tag + "_new.jpg",
                              source="upload", a3_ratio=0.5, status="normal")
        s.add(new_insp); s.flush()
        new_iid = int(new_insp.id)
        s.commit()
    inspection_ids.append(new_iid)
    report = spc_service.add_spc_point(new_iid, "a3", 0.5)
    print("(a) phase=%r, center=%s, ucl=%s" % (
        report["phase"], report["center"], report["ucl"]))
    assert report["phase"] == "baseline"

finally:
    # 정리: CASCADE 로 SPC_POINTS 도 삭제
    with app_db.get_session() as s:
        s.query(Inspection).filter(Inspection.id.in_(inspection_ids)).delete(
            synchronize_session=False)
        s.commit()

# 원상복구 확인
with app_db.get_session() as s:
    after = int(s.query(func.count(SpcPoint.id)).filter(
        SpcPoint.metric == "a3").scalar() or 0)
assert after == 0, "정리 실패: metric='a3' 잔여 %d 건" % after
print("정리 완료")
```

**기대 결과**:
- **(b)**: `total_points=20`, `baseline_complete=False`
- **(a)**: `phase='baseline'`, `center=None`, `ucl=None` (baseline 단계이므로 관리한계 미산출)
- 스크립트 종료 후 `metric='a3'` 데이터 0건 (원상복구)

**실측 (2026-09-16)**: 수정 커밋 `115ee0c` 반영 후 실행. 위 기대 결과 그대로 확인. 실행 전 total=0, 실행 중 total=30→20→21, finally 블록 정리 후 total=0. 실행 이력은 커밋 히스토리 (§4단계 보고) 참조.

**주의사항**:
- `finally` 블록이 반드시 실행되도록 예외 처리 유지. `KeyboardInterrupt` 로 강제 종료 시 잔여 데이터 남을 수 있음 — 이 경우 `DELETE FROM INSPECTIONS WHERE file_name LIKE 'fr56_%'` 로 수동 정리
- FR-56 (c) 는 tests/test_spc.py::TestBaselineJudgmentConsistency 로 자동 검증됨
- FR-56 (d) 는 `_process_spc_and_status` 의 try/except 로직 (`inspection_store.py:118-136`) 으로 코드 리뷰 수준 검증. NFR-17 도 동일 방식이며 별도 자동 테스트 없음

### 계수형 baseline UCL 시나리오 (FR-57 (a))

**왜 자동화하지 않았는가**:
- FR-57 검증 기준 (a) 는 baseline 30 점을 심은 뒤 저장 완료된 최신 SpcPoint 행의 UCL 값이 실제로 `c̄ + 3·√c̄` 로 저장되었는지 DB 상에서 확인하는 것이 목적. 산출식 자체는 `tests/test_spc.py::TestCountMetricFormula` 로 순수 계산 검증했으나, "실제로 SPC_POINTS 테이블에 그 값이 저장되는가" 는 트랜잭션·flush·컬럼 정밀도 (NUMBER(10,6)) 문제를 포함한다
- `TestCountMetricEndToEnd` 는 반환값의 UCL 을 assert 하지만, 저장된 컬럼값을 DB 반올림 이후 직접 조회해서 산출식과 일치하는지까지는 검증하지 않음 (30 개 baseline 심고 반환값 비교로 대체)
- 실제 저장 시나리오는 자동화 시 `SPC_POINTS.METRIC='pinhole_count'` 로 30+ 점을 저장하므로 프로덕션 오염 우려 유효. `pinhole_count` 는 계수형 신규 지표라 CHECK 는 허용하나, 자동화 테스트 종료 후 잔여 확인은 `TestCountMetricCleanupInvariant` 로만 한다

**실행 조건**:
1. 실행 전 `SELECT COUNT(*) FROM SPC_POINTS WHERE metric='pinhole_count'` = 0 확인
2. 프로덕션 라이브 DB 에서 실행 금지. 개발용 로컬 Oracle 11g 인스턴스에서만

**재현 절차** (REPL 붙여 실행 또는 `tools/manual_test_fr57.py` 로 저장):

```python
import math
import uuid
from app import create_app
from app import db as app_db
from app.models import Inspection, SpcPoint, User
from app.services import spc as spc_service
from sqlalchemy import func

app = create_app()

with app_db.get_session() as s:
    admin = s.query(User).filter(User.username == "admin").one()
    before = int(s.query(func.count(SpcPoint.id)).filter(
        SpcPoint.metric == "pinhole_count").scalar() or 0)
if before > 0:
    raise SystemExit("metric='pinhole_count' 잔여 %d 건 — 시나리오 중단" % before)

tag = "fr57_" + uuid.uuid4().hex[:8]
baseline_values = [1, 2, 0, 3, 1, 2, 1, 0, 4, 1, 2, 3, 1, 0, 2,
                   1, 3, 2, 0, 1, 2, 1, 0, 3, 1, 2, 0, 1, 2, 4]
assert len(baseline_values) == spc_service.BASELINE_SIZE

inspection_ids = []
try:
    with app_db.get_session() as s:
        for i in range(spc_service.BASELINE_SIZE):
            insp = Inspection(user_id=admin.id,
                              file_name=tag + "_" + str(i) + ".jpg",
                              source="upload", status="normal")
            s.add(insp); s.flush()
            inspection_ids.append(int(insp.id))
        s.commit()
    for iid, v in zip(inspection_ids, baseline_values):
        spc_service.add_spc_point(iid, "pinhole_count", v)

    # baseline 완성 계기 (31 번째 점)
    with app_db.get_session() as s:
        insp = Inspection(user_id=admin.id, file_name=tag + "_trigger.jpg",
                          source="upload", status="normal")
        s.add(insp); s.flush()
        trigger_iid = int(insp.id)
        s.commit()
    inspection_ids.append(trigger_iid)
    result = spc_service.add_spc_point(trigger_iid, "pinhole_count", 2)

    # 저장된 SpcPoint 를 DB 에서 직접 조회
    with app_db.get_session() as s:
        row = s.query(SpcPoint).filter(
            SpcPoint.inspection_id == trigger_iid,
            SpcPoint.metric == "pinhole_count").one()
        stored_center = float(row.center)
        stored_ucl = float(row.ucl)
        stored_ewma = row.ewma
        stored_alarm = int(row.is_alarm)

    expected_center = sum(baseline_values) / len(baseline_values)
    expected_ucl = expected_center + 3.0 * math.sqrt(expected_center)

    print("center: stored=%.6f  expected=%.6f" % (stored_center, expected_center))
    print("ucl   : stored=%.6f  expected=%.6f" % (stored_ucl, expected_ucl))
    print("ewma  : %r  (계수형이므로 None 이어야 함)" % (stored_ewma,))
    print("alarm : %d  (value=2 < UCL 이므로 0)" % stored_alarm)

    assert abs(stored_center - expected_center) < 1e-4
    assert abs(stored_ucl - expected_ucl) < 1e-4
    assert stored_ewma is None
    assert stored_alarm == 0

finally:
    with app_db.get_session() as s:
        s.query(Inspection).filter(Inspection.id.in_(inspection_ids)).delete(
            synchronize_session=False)
        s.commit()

with app_db.get_session() as s:
    after = int(s.query(func.count(SpcPoint.id)).filter(
        SpcPoint.metric == "pinhole_count").scalar() or 0)
assert after == 0, "정리 실패: pinhole_count 잔여 %d 건" % after
print("정리 완료")
```

**기대 결과**:
- `stored_center ≈ 1.5333`, `stored_ucl ≈ 5.2482` (c̄=46/30, UCL=c̄+3√c̄)
- `stored_ewma == None` (계수형 EWMA 미적용, 설계 §1 (다))
- `stored_alarm == 0` (value=2 < UCL)
- 스크립트 종료 후 `metric='pinhole_count'` 데이터 0 건

**실측 (2026-09-16)**: 실행 결과 stored_center=1.533333, stored_ucl=5.248168 로 산출식 (c̄=46/30=1.533..., UCL=c̄+3√c̄=5.248...) 과 NUMBER(10,6) 반올림 정밀도 내 일치. stored_ewma=None, stored_alarm=0 확인. finally 블록에서 pinhole_count 잔여 0 건으로 원상복구.

**주의사항**:
- `finally` 블록으로 반드시 정리. `KeyboardInterrupt` 등으로 강제 종료 시 잔여 데이터 남으면 `DELETE FROM INSPECTIONS WHERE file_name LIKE 'fr57_%'` 로 수동 정리
- FR-57 (b)(c)(d) 는 `tests/test_spc.py` 의 `TestCountMetricFormula` · `TestComputeLimitsDispatch` · `TestCountMetricEndToEnd` · `TestCountMetricCleanupInvariant` 로 자동 검증됨
- 계수형 metric 을 UI/API 에 노출하려면 `app/routes/spc.py` · `app/api/spc.py` 의 VALID_METRICS 하드코딩 (`("a3","seg_crack")`) 을 확장하거나 `spc_service.VALID_METRICS` 로 파생해야 함. 이번 5단계 범위 밖 (설계 §7: "UI 변경 없음")
