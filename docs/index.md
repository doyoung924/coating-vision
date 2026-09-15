# 문서 인덱스 (docs/)

작성 시점: 2026-09-14. 이 프로젝트의 기술 문서는 다음과 같이 구성된다.

## 문서 목록

| 문서 | 용도 | 대상 독자 |
|---|---|---|
| [`requirements.md`](requirements.md) | 기능 요구사항 (FR-01~55) · 비기능 요구사항 (NFR-01~29) · 제약사항 · 범위 밖 | 이해관계자 · 시스템 스코프 파악 |
| [`architecture.md`](architecture.md) | 계층 다이어그램 · 요청 흐름 · **ERD (mermaid)** · Oracle 11g 대응 · Lazy 로드 전략 | 개발자 · 시스템 이해 |
| [`schema.md`](schema.md) | 테이블 7개 컬럼 정의 · 시퀀스 · 트리거 · 인덱스 · 관계도 | DB 관리자 · SQL 참조 |
| [`routes.md`](routes.md) | 라우트 인벤토리 36개 (경로 · 메서드 · blueprint · 데코레이터 · DB 접근 여부) · 접근 정책 요약 | API 사용자 · 프론트엔드 개발자 |
| [`uml.md`](uml.md) | Use Case · Class (도메인+애플리케이션) · Sequence 4개 (모두 mermaid) | 발표·보고서 · 개발자 |
| [`testing.md`](testing.md) | pytest 실행 방법 · 테스트 구조 · **DB 격리 전략** · 커버리지 | 개발자 · QA |
| [`metrics.md`](metrics.md) | 정량 수치 실측표 (단일 출처). 라우트·테이블·pytest·커버리지·모델 성능·폐기 수치 통합. 재측정 명령 포함 | 발표·보고서 · 문서 간 수치 대조 |
| [`experiment_log.md`](experiment_log.md) | §1~§22 실험 이력 · 세션별 시도·실패·개선 (외부 문서, 이 인덱스 밖) | 프로젝트 이력 참조 |

## 읽는 순서 추천

**시스템 처음 접하는 사람**:
1. `../README.md` — 프로젝트 목적·데이터셋·결과 요약
2. `requirements.md` — 무엇을 만들었는가
3. `architecture.md` — 어떻게 만들었는가
4. `uml.md` — 시각적 이해
5. `schema.md` · `routes.md` — 상세

**보고서 작성·발표 준비**:
- `requirements.md` §1~4 (FR·NFR·제약·범위 밖)
- `uml.md` §1~3 (Use Case·Class·Sequence, mermaid 그대로 삽입 가능)
- `architecture.md` §2~4 (계층 다이어그램·ERD·요청 흐름 2개)

**QA·테스트 재실행**:
- `testing.md`
- `../pytest.ini`, `../tests/`

**DB 재구축·마이그레이션**:
- `schema.md`
- `../sql/schema.sql` · `../sql/init_db.py`
- `../sql/migrate_3_4.py` · `../sql/migrate_3_5.py`

## 다이어그램 겹침 정리

- **ERD (테이블 관계)** = `architecture.md` §3 (mermaid `erDiagram`). 컬럼 상세는 `schema.md`.
- **Class Diagram (ORM 도메인)** = `uml.md` §2.1 (mermaid `classDiagram`). ERD 와 관점이 다름: ERD 는 DB 스키마, Class 는 SQLAlchemy ORM (`relationship`, `foreign_keys=`, `cascade` 포함).
- **애플리케이션 구조** (계층 · 모듈 의존) = `uml.md` §2.2. 개괄은 `architecture.md` §2 계층 다이어그램.
- **요청 흐름** (`/inspect`, `/board/<id>`) = `architecture.md` §4~5. **Sequence Diagram** (mermaid) = `uml.md` §3.

각각 관점이 다르므로 중복이 아니라 상보적. 개괄과 상세를 분리.

## 커밋 대상 여부

모두 커밋 대상. 기밀·비밀번호 미포함.

`../.env` (DB 비밀번호·SECRET_KEY·ADMIN_PASSWORD 포함) 는 `.gitignore` 등록. `../.env.example` 만 커밋.
