# TASK 18 — 현행 시스템 사전 분석 (`TASK18_CURRENT_SYSTEM_ANALYSIS`)

## 1. 프로젝트 디렉터리 및 기술 스택
- **모노레포 구조**:
  - `apps/web`: Next.js 16 (`react` 19, Tailwind CSS 4, TypeScript). `/api/manual/[...path]` 프록시 라우트를 통해 백엔드 API(`127.0.0.1:18000`)와 통신합니다.
  - `services/manual-assistant`: Python 3.12 + FastAPI + Uvicorn + SQLite (`manual_assistant/`).
  - `scripts/`: `run-manual.mjs`, `manual-background.mjs`로 백엔드(`18000`) 및 웹(`13000`) 프로세스를 호스트에서 직접 기동·관리합니다.

## 2. 주문 실행 서비스와 빗썸 API 연동 모듈
- `private_auth.py`: `BithumbAuth` (JWT 서명, query hash SHA-512), `load_credentials()` (`data/.env`, 권한 `0600` 검증).
- `private_client.py`: `PrivateClient` (`GET` 전용 allowlist: `/v1/accounts`, `/v1/orders/chance`, `/v1/order`, `/v1/orders`, 속도 제한 및 429 백오프).
- `private_sync.py`: `PrivateSync` (계좌 잔고, 미체결/체결 내역 REST 동기화 및 Private WebSocket 연동).
- `manual_orders.py`: `OrderTransport` (`POST /v2/orders`, `DELETE /v1/order`), `ManualOrders` (수동 승인 및 `auto_buy_only` 1호가 지정가 매수 실행).

## 3. 실거래 · 가상거래 · 수동거래 구분 방식
- **실거래(자동매수)**: `engine.py` `_execute_auto_buys()`에서 `auto_buy_only=True`일 때 `bsb-ab-...` `client_order_id`로 주문하며, `system_events`에 `auto_buy_submitted` 감사 로그를 기록합니다.
- **실거래(수동/외부)**: `private_fills`, `private_orders`에 동기화되며 `client_order_id` 매핑이 없는 건은 수동(`MANUAL`) 또는 미확정(`UNKNOWN`)으로 분류됩니다.
- **가상거래(PAPER)**: `paper_trades` (`simulated=true, channel='PAPER'`) 및 `reversion_positions`에 격리 저장되며 실거래 계좌 잔고와 혼합되지 않습니다.

## 4. 주문 및 체결 데이터베이스 구조
- SQLite 단일 DB (`data/manual-assistant.sqlite3`, `PRAGMA user_version=10` + 하위 호환 `CREATE TABLE IF NOT EXISTS` 마이그레이션).
- 기존 주요 테이블: `private_accounts`, `private_orders`, `private_fills`, `manual_orders`, `paper_trades`, `reversion_positions`, `position_exit_policy`, `position_exit_leg`, `position_exit_event`, `signals`, `market_candles`, `system_events`, `strategy_reports`.

## 5. 매수·매도 전략 판단 로그
- 메인 전략: `signals`, `paper_trades` (`reasons`, `strategy_snapshot`, `basis`).
- 급락 반등: `reversion_events`, `position_exit_event`, `system_events` (`auto_buy_submitted`, `auto_buy_skipped`, `auto_buy_limit_canceled`).

## 6. Task 16 및 Task 17 데이터 현황
- **Task 16 (`reversion.py`, `reversion_schema.py`)**: 구조적 저점 손절, 최대 `-1.5%` 손절 한도, 10분 반등 실패(`REBOUND_FAILURE`) 청산 데이터가 `reversion_positions`에 저장됩니다.
- **Task 17 (`exit_engine.py`, `reversion_schema.py`)**: `partial_tp_trailing` 정책의 분할 익절 단계(`TP1`, `TP2`, `RUNNER`, `STOP`) 및 상태 전이(`position_exit_policy`, `position_exit_leg`, `position_exit_event`)가 저장됩니다.

## 7. 기존 수익률 계산 로직
- `reversion.py`: `Decimal` 기반 정밀 계산(수수료 `0.04%`, 슬리피지 `0.1%` 반영), `reversion_accounts`에 실현손익(`realized`)과 `max_drawdown`을 유지합니다.
- `strategy_report.py`: Task 18 읽기 전용 ZIP/미리보기 리포트 브리지가 구축되어 있습니다.

## 8. 서버 배포 및 인증·스케줄러
- `app.py` lifespan에서 `market.run()`, `private.run()`, `engine.run()` 비동기 루프가 상시 실행됩니다.
- 인증: 로컬/허용 Origin + `X-BSB-Local` 헤더 및 웹 세션 미들웨어로 `/api/*` 접근을 통제합니다.

## 9. Task 18 / 18-A 통합 영향 범위 및 격리 원칙
- 기존 매매 엔진(`engine.py`, `reversion.py`, `exit_engine.py`, `manual_orders.py`)은 일체 수정하지 않습니다.
- 신규 통합 분석 모듈(`analytics_schema.py`, `analytics_sync.py`, `analytics_ledger.py`)은 읽기 전용 API 조회 및 별도 원장 테이블에만 기록하며, 주문/취소 엔드포인트를 보유하지 않습니다.
