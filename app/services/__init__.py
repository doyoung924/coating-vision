"""
services 서브패키지 — 여러 ml 스테이지를 조립하는 파이프라인 계층.

import 방향: services → ml → store → config (역방향 금지).
routes 계층에서 이 파이프라인을 호출한다.
"""
