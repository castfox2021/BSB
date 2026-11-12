# Task 02 — Bithumb Market Connector

검증일: 2026-11-08 (KST). Task 02만 구현했습니다.

## 구현 결과

- 공식 Public REST에서 전체 KRW 마켓 자동 조회 및 주기적 갱신
- 전체 종목을 연결당 최대 100개로 분할해 현재가·체결·호가 동시 구독
- 연결 시도 직렬화, PING/PONG, 무응답 timeout, 지수 백오프·jitter, 429/418 대기, 재구독
- Decimal 문자열, UTC 거래소/수신 시간, 연결 세대, SNAPSHOT/REALTIME, 원시 타임스탬프/단위 정규화
- 체결 ID 기반 bounded 메모리 중복 제거, 잘못된 프레임 거부, 재연결 시 신선도 초기화
- Redis bounded Stream + TTL 최근 이벤트 캐시, 원자적 갱신, 저장 실패 시 ACK 금지
- 수집기 Health·상태·종목 목록·최근 이벤트 조회 API
- 지연과 미래 방향의 시계 차이를 구분해 집계하고 신선도 판단에서 제외

실제 주문 API, 인증키, 계좌 조회는 없습니다. `entries_allowed=false`, `history_complete=false`를 항상 유지합니다.

## 실행

```sh
# 최초 빌드 및 기본 mock 기동
docker compose up -d --build --wait
# 수집기만 실제 공개 시세 사용
COLLECTOR_SOURCE=bithumb_public docker compose up -d --no-deps --wait --wait-timeout 120 market-collector
python3 tests/integration/live_collector_smoke.py
curl http://localhost:8000/collector/status
# 기본 모드 복원
COLLECTOR_SOURCE=mock docker compose up -d --no-deps --wait market-collector
```

지속 실행은 `.env`의 COLLECTOR_SOURCE를 변경합니다. 다른 서비스 DATA_SOURCE는 mock을 유지합니다. 자세한 계약과 설정은 `services/market-collector/README.md`, `docs/operations.md`에 있습니다.

## 테스트 결과

| 검증 | 결과 |
|---|---|
| Python 3.12 Ruff / mypy | 통과, 타입 검사 10개 소스 파일 |
| Pytest | 40개 통과 (기존 8개 포함) |
| TypeScript lint / typecheck | 통과 |
| Jest | 2개 suite, 3개 테스트 통과 |
| Compose config 및 수집기 Docker build | 통과 |
| 기본 mock Web/API/Python HTTP smoke | 통과 |
| 실제 Public REST | 전체 KRW 488개 조회 |
| 실제 Public WebSocket | 5개 shard에 총 488개 구독, ticker/trade/orderbook REALTIME 수신 |
| Redis 전달 | 실제 이벤트 저장 후 카운트 ACK 및 BTC 최근 세 종류 조회 확인 |
| 장애 테스트 | 로컬 실제 소켓 서버 연결 종료→재연결→재구독, 중복 제거, 취소/종료, 저장 실패 ACK 방지 통과 |

관측 수치와 카운터는 `task-02-live-verification.json`에 저장했습니다. 종목 수는 검증 시점 값이며 이후 달라질 수 있습니다. 외부 네트워크를 사용하는 live smoke는 CI 기본 검사에서 분리했습니다. 기존 CI가 새 Python 테스트를 자동 수집하며, 원격 GitHub Actions 실행은 수행하지 않았습니다.

## 알려진 제한 및 관측

- 거래소 시각이 이 환경의 로컬 수신 시각보다 약 2.1초 앞서는 차이가 관측됐습니다. 원인이 어느 시계에 있는지 확정하지 않았습니다. 해당 이벤트는 clock_skew로 집계하고 신선 판정에서 제외했습니다. 시계를 임의 보정하거나 위험 조건을 완화하지 않았습니다.
- 시계 차이가 있어도 전송 자체는 정상일 수 있습니다. `transport_ready`는 거래 허가나 모든 종목의 데이터 완전성을 뜻하지 않습니다. 수집 데이터가 자동매매에 바로 사용 가능한 상태라고 간주하면 안 됩니다.
- 일부 프레임은 정규화 검증에서 거부됐으며 나머지 스트림 수신은 계속됐습니다. 해당 건은 invalid_messages로 표시합니다.
- REST 목록은 거래지원 상태 전체를 보장하지 않습니다. 유의 정보 및 ticker의 정지/상태 정보를 전달하며, 매매 적격 판정은 후속 엔진이 담당합니다.
- 중복 캐시는 최근 50,000개를 메모리에 유지합니다. 재시작 간 중복 방지·영속 저장·누락 복구·역순 처리·확정 캔들은 Task 03 범위입니다. Redis Stream은 유한 버퍼이고 exactly-once를 보장하지 않습니다.
- 웹은 Task 01 초기 mock 화면입니다. 실시간 시세는 수집기 API에서 조회하며 대시보드 연결은 Task 08 범위입니다.
- 기존 외부 라이브러리 TestClient의 httpx deprecation 경고 1건이 남아 있습니다.
- 실제 수집 검증 후 기본 mock 모드로 복원했습니다. 임시 테스트 컨테이너는 제거했습니다.

## 생성·수정 파일

```text
.env.example
docker-compose.yml
README.md
docs/architecture.md
docs/api-spec.md
docs/operations.md
docs/bithumb-public-api.md
docs/task-02-report.md
docs/task-02-live-verification.json
packages/contracts/src/index.ts
packages/contracts/market-trade.schema.json
packages/contracts/market-ticker.schema.json
packages/contracts/market-orderbook.schema.json
services/market-collector/README.md
services/common/pyproject.toml
services/common/bsb_service/collector/__init__.py
services/common/bsb_service/collector/app.py
services/common/bsb_service/collector/config.py
services/common/bsb_service/collector/normalize.py
services/common/bsb_service/collector/rest.py
services/common/bsb_service/collector/runner.py
services/common/bsb_service/collector/state.py
services/common/tests/test_collector.py
services/common/tests/fixtures/public-ws.json
tests/integration/live_collector_smoke.py
```

## Task 03 전 확인

PostgreSQL 수집 저장과 영구 고유 키, 재처리, 누락/역순 감지, 캔들 경계·확정 여부, 재시작 복구를 구현해야 합니다. 거래소/로컬 시계 차이를 운영 환경에서 먼저 점검해야 합니다. Task 03은 아직 진행하지 않았습니다.

공식 API 출처와 제한 검토는 `bithumb-public-api.md`에 있습니다.
