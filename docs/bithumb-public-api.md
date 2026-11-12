# Bithumb Public API — Task 02

공식 문서 확인일: 2026-11-08. 이 수집기는 인증 없는 시세 조회만 사용합니다.

| 용도 | 사용 경로 / 형식 |
|---|---|
| 거래 대상 조회 | `GET https://api.bithumb.com/v1/market/all?isDetails=true` |
| Public WebSocket | `wss://ws-api.bithumb.com/websocket/v1` |
| 구독 | ticket(UUID), ticker/trade/orderbook 각각의 codes 목록, format DEFAULT |
| 종목 필터 | `KRW-` 코드만, 대문자 |

요청의 snapshot/realtime 필터는 생략해 두 종류를 모두 받고 `stream_type`으로 구분합니다. 구독 예제는 `services/common/bsb_service/collector/runner.py`의 `subscription` 함수입니다. Private WebSocket과 주문 REST는 구현하지 않습니다.

## 단위와 정밀도

체결 `trade_timestamp`와 현재가 `timestamp`는 밀리초입니다. 호가 `timestamp`는 공식 문서와 실제 샘플 모두 마이크로초였습니다. 호가는 과거/다른 배포의 밀리초 값도 크기로 구분해 허용하며, 원시 타임스탬프와 해석한 단위를 이벤트에 남깁니다. 비정상 단위·범위·미래 시간은 거부합니다.

JSON 숫자를 Decimal로 직접 읽어 가격/수량을 문자열로 내보냅니다. 서버 수신 시간은 WebSocket recv 직후 기록하며 UTC `exchange_at`과 `received_at`을 구분합니다. 체결 방향 BID는 BUY, ASK는 SELL, 미상은 UNKNOWN입니다. `sequential_id`는 중복 판단용이며 연속성·순서를 보장하는 번호로 해석하지 않습니다.

## 제한 및 재연결

공식 WebSocket 연결 요청 한도는 IP당 초당 10회입니다. 내부적으로 모든 shard 연결 시도를 초당 1회 이하로 직렬화하며, 지수 백오프와 jitter를 적용합니다. WS 429/418은 프로세스 전체 연결 시도를 최소 600초 보류합니다. 라이브러리 PING/PONG(20초)으로 유휴 연결을 유지하고, 30초간 데이터 프레임이 없으면 재연결합니다.

공식 Public REST 제한 안내는 초당 150회이며, 수집기는 전체 마켓 조회를 기본 300초마다 한 번 요청합니다. REST 429/418은 Retry-After와 최소 60초 대기를 적용합니다. 공지에 따라 한도가 변경될 수 있습니다.

한 연결의 기본 종목 수 100은 내부 분할 정책입니다. 확인한 문서에는 Public codes의 고정 최대 개수가 명시되어 있지 않아 공식 한도라고 주장하지 않습니다. 실제 전체 KRW 구독 검증은 별도 보고서에 기록합니다. IP 단위 한도이므로 여러 프로세스를 실행하면 외부에서 공통 한도를 관리해야 합니다.

## 공식 근거

- [거래 대상 목록 조회](https://apidocs.bithumb.com/reference/거래-대상-목록-조회)
- [WebSocket 기본 정보](https://apidocs.bithumb.com/reference/기본-정보)
- [요청 방법 및 포맷](https://apidocs.bithumb.com/reference/요청-포맷)
- [연결 관리](https://apidocs.bithumb.com/reference/연결-관리)
- [현재가](https://apidocs.bithumb.com/reference/현재가-ticker)
- [체결](https://apidocs.bithumb.com/reference/체결-trade)
- [호가](https://apidocs.bithumb.com/reference/호가-orderbook)
- [Public API 요청 제한 안내](https://content.bithumb.com/apidocs/ratelimit3.html)
