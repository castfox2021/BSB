# Task 01 구현 보고

## 구현 범위

Next.js·TypeScript·Tailwind 초기 화면, NestJS(Fastify) Health API, Python 3.12 공통 서비스 템플릿과 7개 독립 서비스 컨테이너, PostgreSQL 16·Redis 7, Docker Compose, GitHub Actions를 구성했습니다.

Health는 DB `SELECT 1`과 Redis `PING`을 검증하고 실패 시 503을 반환합니다. 각 Python 서비스는 현재 Health만 제공하며 도메인 로직은 없습니다. 설정 검사·기본 로그·예외 처리와 mock/paper 표시를 추가했습니다. JSON Schema·TypeScript 이벤트 타입과 Python Decimal 검증기를 마련했습니다.

## 실행

저장소 루트에서:

```sh
cp .env.example .env
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 240
docker compose ps
python3 tests/integration/smoke.py
```

상세 테스트와 운영 명령은 README 및 operations 문서에 있습니다.

## 검증

- TypeScript: ESLint, typecheck, Jest 2개 suite / 3개 테스트
- Python 3.12 컨테이너: Ruff, mypy(3개 소스 파일), Pytest 8개 테스트 통과
- Next.js와 NestJS production build
- Compose config, 전체 11개 컨테이너 healthy
- Web/API/Python HTTP smoke 및 기본 화면 HTTP 200·PAPER/MOCK 표시 확인
- `npm audit --omit=dev`: 알려진 런타임 취약점 0건
- 실제 빗썸 주문 API와 인증키 저장 코드 없음

GitHub Actions 워크플로를 작성했습니다. 원격 저장소에 push하거나 원격 CI 실행 결과를 확인한 것은 아닙니다.

## 알려진 제한

- Task 02 이후 수집·캔들·스캐너·매매·위험관리·업무 DB 테이블은 미구현입니다.
- 실시간 시장 데이터·실거래·백테스트·성과 검증을 수행하지 않았습니다.
- 호스트 Python은 3.9여서 Python 검증은 지정된 3.12 Docker 이미지에서 수행했습니다.
- 호스트의 Turbopack 내부 포트 생성 제한을 피하도록 Next.js production build를 Webpack으로 설정했습니다.
- Python TestClient에서 외부 라이브러리의 httpx 관련 deprecation 경고 1건이 있습니다.
- Python 의존성은 허용 버전 범위로 관리하며 완전한 hash lock은 아직 없습니다.
- 개발 도구(Jest/ESLint) 하위 의존성의 audit 경고는 후속 갱신이 필요합니다. 실행 이미지에서는 개발 의존성을 제거합니다.
- 기본 DB 비밀번호는 로컬 개발 전용입니다. 인증·TLS·운영 모니터링은 후속 범위입니다.

## Task 02 진행 전

사용자 승인을 받은 뒤 공식 빗썸 Public API 문서에서 REST·WebSocket 메시지 형식, 구독 수, 호출 제한과 체결 방향 의미를 확인합니다. 재연결·누락·중복·데이터 신선도 검증을 구현하고, live와 mock 출처를 구분해야 합니다. 현재 실시간 연동은 완료되지 않았습니다.

초기화 시 참고: [Next.js 설치 문서](https://nextjs.org/docs/app/getting-started/installation), [NestJS 시작 문서](https://docs.nestjs.com/first-steps).

## 생성·수정 파일

```text
.dockerignore
.env.example
.github/workflows/ci.yml
.gitignore
README.md
apps/api/package.json
apps/api/src/health.spec.ts
apps/api/src/health.ts
apps/api/src/main.ts
apps/api/tsconfig.json
apps/web/app/api/health/route.spec.ts
apps/web/app/api/health/route.ts
apps/web/app/globals.css
apps/web/app/layout.tsx
apps/web/app/page.tsx
apps/web/next-env.d.ts
apps/web/package.json
apps/web/postcss.config.mjs
apps/web/tsconfig.json
docker-compose.yml
docs/api-spec.md
docs/architecture.md
docs/operations.md
docs/task-01-report.md
docs/trading-rules.md
eslint.config.mjs
infra/docker/node.Dockerfile
infra/docker/python.Dockerfile
infra/migrations/README.md
jest.config.cjs
package-lock.json
package.json
packages/contracts/market-trade.schema.json
packages/contracts/package.json
packages/contracts/src/index.ts
packages/contracts/tsconfig.json
packages/shared/package.json
packages/shared/src/index.ts
packages/strategy-configs/defaults.json
ruff.toml
services/backtester/README.md
services/candle-builder/README.md
services/common/bsb_service/__init__.py
services/common/bsb_service/app.py
services/common/bsb_service/contracts.py
services/common/pyproject.toml
services/common/tests/test_contracts.py
services/common/tests/test_health.py
services/market-collector/README.md
services/paper-engine/README.md
services/risk-engine/README.md
services/scanner/README.md
services/signal-engine/README.md
tests/e2e/README.md
tests/integration/smoke.py
```
