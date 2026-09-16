"""
ml 서브패키지 — 모델 로드와 스테이지별 추론 로직.

구성 (§2단계 (b) 지시):
  loader.py   YOLO · seg 가중치 lazy 로드 + 실패 시 에러 문자열 슬롯
  anomaly.py  A3 밝기 표준편차 이상 탐지
  detect.py   YOLO 핀홀 검출
  segment.py  U-Net 시맨틱 세그 (crack · delam)
  shape.py    검출 박스 종횡비 · roundness 및 defect_map 참조
  advisor.py  3층 규칙 기반 리포트 조립

import 방향: ml → store → config (역방향 금지)
계산 로직은 legacy app.py 에서 그대로 옮겨왔다. 수정 없음.
"""
