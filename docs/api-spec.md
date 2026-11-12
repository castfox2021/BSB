# API — Task 01

`GET /api/v1/system/health` — NestJS 및 DB/Redis readiness. 성공 200, 의존성 장애 503. `status`, `service`, `mode: paper`, `dataSource: mock`, `tradingEnabled: false`를 반환합니다.

`GET /api/health` — 웹 liveness, `GET /health` — 각 Python readiness.

이 단계에는 관리·설정·주문 API가 없습니다. 후속 관리 API에는 인증과 관리자 권한 검사를 적용한 후 노출합니다.


## Task 02 Collector API (port 8000)

- `GET /collector/status`: 출처, 전송 상태, 전체 종목 수, shard별 연결·재시도·시계 차이, 메시지 카운터, 신선 종목 목록
- `GET /collector/markets`: 종목 메타데이터·갱신 시간·유효 여부
- `GET /collector/latest/{symbol}`: TTL 내 최근 세 스트림 이벤트, 종목별 fresh, 진입 금지 상태. 잘못된 코드 400, 구독 없는 종목 404, Redis 실패 503.
- `GET /health`: DB/Redis 및 실제 수집 전송 상태. mock 유휴 모드는 DB/Redis만 검사.

변경 API 없이 로컬호스트에만 노출합니다. 기존 NestJS API와 웹 Health는 mock 템플릿 상태를 표시하며 수집기 데이터를 대시보드에 연결하는 작업은 Task 08 범위입니다.
