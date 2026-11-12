# Market Collector — Task 02

구현 위치: `services/common/bsb_service/collector`. 공통 Python 패키지에 설치하되 이 서비스만 별도 `collector.app` 진입점으로 실행합니다. 다른 Python 서비스는 Task 01 템플릿을 유지합니다.

기본 `COLLECTOR_SOURCE=mock`은 외부 접속 없는 유휴 모드이며 샘플 체결을 생성하지 않습니다. `bithumb_public`은 인증 없는 실제 시세 수집입니다. 두 모드 모두 실제 주문은 불가능합니다.

```sh
docker compose up -d --build --wait market-collector
COLLECTOR_SOURCE=bithumb_public docker compose up -d --no-deps --wait --wait-timeout 120 market-collector
python3 tests/integration/live_collector_smoke.py
# 다시 기본 mock으로 전환 (.env에서 별도로 지정했다면 해당 값도 변경)
COLLECTOR_SOURCE=mock docker compose up -d --no-deps --wait market-collector
```

지속 사용 시 `.env`의 `COLLECTOR_SOURCE=bithumb_public`으로 지정합니다. `DATA_SOURCE`는 다른 서비스의 Task 01 mock 설정이므로 변경하지 않습니다. 기존 웹 화면은 아직 실제 시세를 표시하지 않습니다.

## 조회

- `GET http://localhost:8000/health`: DB/Redis 및 live일 때 수집 전송 상태, 불완전하면 503
- `GET /collector/status`: 출처·메시지 수·중복/오류 수·shard 연결·재연결·종목 신선도
- `GET /collector/markets`: 전체 조회된 KRW 종목 및 갱신 시간
- `GET /collector/latest/KRW-BTC`: Redis에 남은 최근 현재가·체결·호가, fresh 여부

`fresh_symbols`는 현재 연결 세대에서 세 종류의 REALTIME 이벤트가 모두 신선한 종목입니다. 저유동성 종목은 목록에 없을 수 있습니다. `transport_ready`는 각 shard의 수신 및 마켓 메타데이터 갱신 상태이며, 모든 종목의 데이터 완전성이나 거래 가능성을 뜻하지 않습니다. 이 단계는 `entries_allowed=false`, `history_complete=false`를 항상 반환합니다.

## Redis 이벤트

- `bsb:bithumb_public:events`: 공통 이벤트 Stream (`payload` JSON), 기본 최대 100,000개 정확히 유지
- `bsb:bithumb_public:latest:KRW-BTC:market.trade` (ticker/orderbook도 동일): 최근 이벤트, 기본 TTL 15초
- `bsb:bithumb_public:markets`: 종목 메타데이터, 갱신 주기의 2배 TTL

이벤트에 출처·고유 ID·연결 세대·스냅샷 구분·UTC 시간·Decimal 문자열이 들어갑니다. 계약은 `packages/contracts/market-*.schema.json`입니다. Stream 추가와 latest 갱신은 Redis MULTI/EXEC로 묶습니다. 저장 실패는 ACK/중복 캐시에 반영하지 않고 연결을 복구합니다.

중복 캐시는 프로세스 메모리의 최근 50,000개 이벤트 ID입니다. 재시작을 넘는 exactly-once나 누락 복원은 제공하지 않습니다. Stream도 유한 버퍼이며 Task 03에서 PostgreSQL 영속화·캔들·재처리와 누락 보정을 구현해야 합니다. 비정상 프레임은 버리고 신선도 추적을 초기화하며, 구독 오류는 재연결합니다. 모드 간 출처 키를 혼용하지 않습니다.

공식 문서와 제한 확인 내용은 `docs/bithumb-public-api.md`를 참고하세요.
