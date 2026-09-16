# 테스트 (§3-10)

작성 시점: 2026-09-14. pytest + pytest-cov 기반 통합 테스트.

## 실행 방법

```bash
# 개발 의존성 설치 (최초 1회)
source .venv/bin/activate
pip install -r requirements-dev.txt

# Oracle Instant Client (thick 모드) 세팅. run.py 와 동일하게 이 export 가 필요.
export LD_LIBRARY_PATH=/home/doyoung/oracle/instantclient_19_32

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
