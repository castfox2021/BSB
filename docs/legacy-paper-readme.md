# Bithumb Scalping Bot (BSB)

빗썸 KRW 모의 자동매매 플랫폼 — **Task 01–02: Monorepo Bootstrap + Bithumb Market Connector**.
실제 주문 기능과 거래소 API 키는 없습니다. 시장 수집기는 구현했으며 기본 실행은 외부 접속 없는 `mock`입니다. 수집기만 `bithumb_public`으로 전환하면 실제 공개 시세를 수신합니다. 매매·계좌 기능은 아직 없습니다.

## 실행

Docker Engine + Compose v2가 필요합니다. 로컬 개발은 Node.js 22.12+, Python 3.12를 사용합니다.

```sh
cp .env.example .env
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 240
docker compose ps
python3 tests/integration/smoke.py
```

- 화면: http://localhost:3000
- Web Health: http://localhost:3000/api/health
- API Health: http://localhost:3001/api/v1/system/health
- Python Health: http://localhost:8000/health

DB/Redis는 호스트에 포트를 노출하지 않습니다. 웹/API/Python 포트는 로컬호스트에만 연결됩니다.
일반 명령 `docker compose up -d --build`도 지원하며, 건강 상태를 기다리려면 위의 `--wait`를 사용합니다.

## 검증

```sh
npm ci
npm run lint
npm run typecheck
npm test
npm run build
python3.12 -m venv .venv
. .venv/bin/activate
pip install './services/common[dev]'
ruff check services/common tests
mypy services/common/bsb_service
pytest services/common/tests
```

각 Python 서비스 내부 검사:

```sh
docker compose exec scanner python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"
docker compose exec postgres psql -U bsb -d bsb -c 'SELECT 1'
docker compose exec redis redis-cli ping
```

API와 Python Health는 `SELECT 1`과 Redis `PING`이 모두 성공해야 200을 반환하고, 실패하면 503을 반환합니다. Web Health는 웹 프로세스 생존 상태입니다. CI는 lint·typecheck·Jest·Pytest·build·Compose smoke를 실행합니다.

## 구성

- `apps/web`: Next.js App Router + TypeScript + Tailwind CSS, 초기 설정 예시 화면
- `apps/api`: NestJS 12 + Fastify Health API, 설정 검사, 기본 예외 응답 및 로그
- `services/common`: Python 3.12 FastAPI 서비스 템플릿 및 Public 시세 수집기, 의존성 검사·로깅
- `services/market-collector`: Public REST/WS 수집기, Redis 이벤트 전달 및 상태 조회
- 나머지 6개 Python 서비스: 독립 컨테이너, 현재 공통 템플릿만 실행
- `packages/contracts`: JSON Schema와 TypeScript 이벤트 계약
- `packages/shared`: 공통 모드 상수
- `packages/strategy-configs`: Decimal 문자열 기반 비활성 전략 초기값
- `infra`: Docker 및 migration 소유권 규칙
- `tests`: 통합 smoke 및 후속 E2E 위치

설정은 [operations](docs/operations.md), 책임 구분은 [architecture](docs/architecture.md), API는 [api-spec](docs/api-spec.md), 거래 규칙은 [trading-rules](docs/trading-rules.md)를 참고합니다.

## Task 02 실시간 시세 수집

```sh
COLLECTOR_SOURCE=bithumb_public docker compose up -d --no-deps --wait --wait-timeout 120 market-collector
python3 tests/integration/live_collector_smoke.py
curl http://localhost:8000/collector/status
COLLECTOR_SOURCE=mock docker compose up -d --no-deps --wait market-collector
```

처음 실행은 전체 Compose 빌드를 먼저 수행하세요. 지속 사용은 `.env`에 `COLLECTOR_SOURCE=bithumb_public`을 지정합니다. 다른 서비스의 `DATA_SOURCE=mock`은 유지합니다. 현재 대시보드는 초기 화면이며 실제 시장 데이터를 표시하지 않습니다.

[수집기 실행/이벤트 계약](services/market-collector/README.md), [공식 API 검증 근거](docs/bithumb-public-api.md), [Task 02 보고서](docs/task-02-report.md)를 참고하세요. 수집 여부와 무관하게 신규 진입은 비활성입니다. 캔들·영속 저장·누락 보정은 Task 03 범위입니다.
