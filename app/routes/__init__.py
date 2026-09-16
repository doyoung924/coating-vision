"""
routes 서브패키지 — HTML 렌더링 blueprint 5개.

blueprint:
  main       /, /figures/<name>, /frame/<name>
  stream     /stream
  inspect    /inspect, /detection
  benchmark  /benchmark

api 는 별도 서브패키지 (app/api/).
create_app() 에서 이 파일들의 blueprint 인스턴스를 등록한다.

import 방향: routes → services → ml → store → config
"""
