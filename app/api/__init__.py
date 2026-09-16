"""
api 서브패키지 — JSON 응답을 반환하는 blueprint 그룹.

현재 blueprint:
  sequence   /api/sequence/<seq_id>

url_prefix="/api" 는 blueprint 인스턴스에 부여되므로 라우트 자체는
'/sequence/<seq_id>' 로 정의된다.
"""
