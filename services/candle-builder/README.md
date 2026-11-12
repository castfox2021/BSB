# Market Data Pipeline — Task 03

독립 실행 모듈: `bsb_service.pipeline.app`. 수집기의 Redis 이벤트를 PostgreSQL에 영속화하고 1분·5분 OHLCV를 생성합니다. 현재 주문·전략 기능은 없습니다.

```sh
docker compose up -d --build --wait
curl http://localhost:8001/health
curl http://localhost:8001/pipeline/status
curl 'http://localhost:8001/pipeline/candles/KRW-BTC?timeframe=60&closed_only=true'
```

실제 시세를 사용하려면 수집기와 파이프라인의 출처를 함께 바꿉니다.

```sh
COLLECTOR_SOURCE=bithumb_public PIPELINE_SOURCE=bithumb_public docker compose up -d --no-deps --wait --wait-timeout 180 market-collector candle-builder
# 종료 후 기본 모드로 복원
COLLECTOR_SOURCE=mock PIPELINE_SOURCE=mock docker compose up -d --no-deps --wait market-collector candle-builder
```

지속 설정은 `.env`에서 두 변수를 변경합니다. `DATA_SOURCE=mock`은 유지합니다. mock 모드는 외부 접속을 하지 않으며 파이프라인도 가짜 체결을 자동 생성하지 않습니다. `source`는 모든 테이블 키와 API 응답에 포함됩니다.

## 처리 방식

수신 위치는 Redis consumer group 대신 PostgreSQL `pipeline_offsets`에 저장합니다. 이벤트 고유 키 삽입, 숫자 투영, 캔들 갱신, 처리 위치 이동을 한 트랜잭션으로 묶습니다. 재시작은 마지막 커밋 위치부터 XREAD하며, 재전달된 이벤트는 고유 키로 중복 반영을 막습니다. 출처별 DB advisory lock으로 활성 writer를 하나로 제한합니다.

가격과 수량은 NUMERIC이며 API에서는 Decimal 문자열입니다. 호가 배열의 숫자는 JSONB 내부에서도 문자열입니다. 시간은 UTC TIMESTAMPTZ입니다. 불량 이벤트·동일 ID의 다른 내용은 격리하고 품질 이벤트를 기록합니다.

시장 상태 heartbeat와 메타데이터도 같은 Stream에 저장합니다. 수집기 heartbeat는 5초 간격이며, 실제 데이터 수신 신선도와 연결 세대를 전달합니다. 메타데이터의 LISTED는 현재 목록에 존재한다는 의미이며 매매 허가가 아닙니다.

## 캔들 규칙

- 거래소 이벤트 시간 기준 UTC 반개구간 `[시작, 종료)`으로 60초와 300초를 각각 집계합니다.
- 체결은 REALTIME만 합산하며 SNAPSHOT/UNKNOWN은 원본·체결 테이블에 저장하되 OHLCV에서는 제외합니다.
- 시가·종가는 `(exchange_at, event_id)`의 순서로 결정합니다. 같은 시각은 event_id 문자열 순서로 고정하며, 거래소 ID가 체결 순서나 연속성을 보장한다고 가정하지 않습니다.
- 거래량과 거래대금은 PostgreSQL NUMERIC 연산으로 누적합니다. 무체결 분을 가짜 0거래량 캔들로 만들지 않습니다.
- `closed`: 구간 종료와 유예 시간이 지났고 수집 버퍼의 확인된 끝까지 처리했습니다. 기본 유예는 40초입니다(수집기 timeout 30초 + 상태 발행 5초에 여유 포함).
- `complete`: 수집 시작이 구간 이전이며 동일한 관측 연결 구간으로 종료까지 이어졌고, 최근 실제 데이터 수신과 heartbeat가 신선하며 차단 품질 문제가 없는 경우에만 true입니다. 관측 가능한 수집 연속성에 대한 판정이며 거래소가 알리지 않은 내부 누락을 증명할 수는 없습니다.
- 늦은 체결은 OHLCV를 정정하고 revision을 높입니다. 이미 closed이면 `revised_after_close=true`, `complete=false`가 됩니다. 나중에 단절이 확인돼도 해당 캔들의 complete를 철회하고 품질 플래그·revision을 갱신합니다.

따라서 closed를 변경 불가능한 원본으로 취급하면 안 됩니다. 향후 백테스트는 실제 수신 시각과 당시 리비전/사용 이벤트를 기준으로 재생해야 하며, 나중에 정정된 캔들을 과거 판단에 미리 사용하면 안 됩니다.

## 누락과 복구

Redis 보존 범위보다 DB cursor가 앞서면 가능한 유실 구간을 표시합니다. Stream 삭제·ID 초기화도 탐지하고 커서를 재설정하되 DB 고유 키는 유지합니다. 연속된 거래소 ID 차이로 누락을 추정하지 않습니다. 연결 교체, heartbeat 중단, 불량 프레임, 시계 차이, 지연을 품질 이벤트로 남깁니다.

복구 가능한 것은 Redis에 남아 있는 이벤트입니다. 이미 잘렸거나 거래소 연결 중 놓친 원본 체결을 자동 복원하지 않으며, 그런 구간을 완전한 캔들로 꾸미지 않습니다. 과거 REST backfill은 별도 후속 기능입니다.

## API

- `/pipeline/status`: 출처, writer 상태, DB cursor, caught_up, 세션 카운터, 진입 차단
- `/pipeline/candles/{symbol}`: timeframe=60/300, closed_only, complete_only, limit=1..1000
- `/pipeline/issues`: 최근 품질 이벤트(limit=1..1000)
- `/health`: DB/Redis와 writer 주기 상태, 장애 시 503

API는 호스트 127.0.0.1:8001에만 노출됩니다. `entries_allowed=false`는 Task 03에서 계속 유지합니다. health=ok는 시장 데이터가 매매에 적합하다는 뜻이 아닙니다.
