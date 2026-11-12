# BSB (Bithumb Strategy Lab) 코덱스(Codex) 인수인계 문서

> **작성일**: 2026-11-11  
> **대상**: 다음 개발자 및 AI 에이전트(Codex)  
> **목적**: 모의/연습 단계를 넘어선 **실전 자동매매 시스템**의 아키텍처 이해, 운영 가이드 및 후속 개발 인수인계

---

## 1. 프로젝트 개요 및 핵심 철학

본 프로젝트(**BSB**, Bithumb Trading Strategy Lab)는 빗썸(Bithumb) 원화(KRW) 시장을 대상으로 **전체 마켓 스캐닝, 실시간 캔들 생성, 전략 백테스트/검증, 그리고 승인된 신호 기반의 실제 자동매매(Auto-Trading) 및 수동 주문**을 로컬 환경에서 안전하게 수행하는 시스템입니다.

### 핵심 설계 철학
1. **연습/모의를 넘어선 실전 자동매매**:
   - Strategy Lab에서 검증(Validation) 및 사용자 승인(Approval)을 통과한 전략 버전만 실전 매매에 진입합니다.
   - 미검증 관찰 신호(`WATCH_ENTRY`, `EARLY_SURGE`)는 참고용으로만 표시되며 자동 주문되지 않습니다.
2. **Fail-Closed 안전 최우선 원칙**:
   - 운용 자본 **150,000원**, 1회 매수 수수료 포함 **최대 50,000원**, 일일 손실 한도 **3,000원**, 최소 현금 여유 기준을 서버 레벨에서 하드코딩 검증합니다.
   - 단일 프로세스/단일 세션 직렬화: 처리 중이거나 결과가 불분명한 주문(`SUBMITTING`, `ACKNOWLEDGED`, `UNKNOWN`)이 1건이라도 존재하면 새로운 주문 전송이 즉시 차단됩니다.
   - 주문 결과 불명(`UNKNOWN`)이나 거래소 거절(`REJECTED`) 발생 시 자동매매는 즉시 안전 정지(`STOPPED`)됩니다.
3. **접수(Ack)와 체결(Fill)의 철저한 분리**:
   - 거래소 접수 응답(`ACKNOWLEDGED`)만으로 원장 잔고나 포지션을 조작하지 않습니다.
   - Private WebSocket 및 REST 체결 복구 파이프라인을 통해 실제 체결 ID(`trade_id`), 체결 가격, 수량, 수수료가 검증된 내역만 전략 원장(`manual_fills`)에 반영합니다.
4. **보안 무결성**:
   - API 키는 오직 권한 600의 로컬 `.env` 파일에서만 읽으며 브라우저 입력, 환경 변수 노출, DB 저장을 전면 차단합니다.

---

## 2. 전체 시스템 아키텍처

```mermaid
flowchart TD
    subgraph Bithumb_Exchange ["빗썸 거래소 (Bithumb Exchange)"]
        PUB_WS["Public WebSocket v1\n(Trade / Ticker)"]
        PUB_REST["Public REST API\n(Candles / Market Info)"]
        PRIV_REST["Private REST API v1/v2\n(/v2/orders, /v1/orders/chance)"]
        PRIV_WS["Private WebSocket v2\n(myOrder 체결 스트림)"]
    end

    subgraph Backend ["Python 3.12 백엔드 (services/manual-assistant)"]
        MKT["Market Collector & Candle Builder\n(전체 KRW 1/3/5분봉 생성)"]
        ENG["Strategy & Risk Engine\n(포지션 감시, TP/SL, 신호 생성)"]
        AUTO["Automation Engine\n(신호 검증, 예산·쿨다운·안전장치)"]
        ORD["Manual & Live Orders\n(OrderTransport, 서명, 단일 주문 제어)"]
        SYNC["Private Sync\n(잔고/체결 동기화, WebSocket 복구)"]
        DB[("SQLite3 DB (Schema 6)\ndata/manual-assistant.sqlite3")]
    end

    subgraph Frontend ["Next.js 프론트엔드 (apps/web)"]
        UI_AUTO["신호 자동매매 패널\n(시작/정지, 상태 모니터링)"]
        UI_SCAN["전체 KRW 스캐너\n(급등 탐지, 랭킹)"]
        UI_CHART["차트 리플레이 & 복기"]
        UI_HIST["실시간 체결 / 이력 페이징"]
    end

    PUB_WS -->|실시간 시세| MKT
    PUB_REST -->|초기 캔들/종목| MKT
    MKT -->|완료 분봉 & 시세| ENG
    ENG -->|BUY / SELL / RISK_EXIT| DB
    DB -->|신호 감지| AUTO
    AUTO -->|주문 요청 (Preview/Submit)| ORD
    ORD -->|POST /v2/orders (HMAC-SHA256 서명)| PRIV_REST
    PRIV_WS -->|체결 이벤트 (myOrder)| SYNC
    PRIV_REST -->|조회 복구 (/v1/order)| SYNC
    SYNC -->|체결 저장 & 원장 대조| DB
    ORD -.->|체결 즉시 wake & recover| DB
    DB <-->|REST API (:18000)| Frontend
```

---

## 3. 디렉토리 구조 및 핵심 모듈 맵

```
.
├── apps/
│   ├── web/                     # Next.js 16 (App Router) 프론트엔드
│   │   ├── app/
│   │   │   ├── automatic-trading.tsx # 자동매매 활성화/정지 UI 및 알림 처리
│   │   │   ├── direct-trading.tsx    # 수동 직접 주문 패널
│   │   │   ├── full-market-scanner.tsx # 전체 KRW 실시간 스캐너
│   │   │   ├── trading-review.tsx    # 차트 리플레이 및 매매 복기
│   │   │   └── history-paging.tsx    # 이력(체결, 신호, 주문, 활동) 페이징
│   │   └── package.json
│   └── api/                     # Node.js 마이크로서비스 (보조 헬스체크)
├── services/
│   └── manual-assistant/        # 핵심 파이썬 백엔드 엔진
│       ├── manual_assistant/
│       │   ├── automation.py    # 신호 자동매매 실행기 (Automation 클래스)
│       │   ├── manual_orders.py # 주문 전송, 검증, 복구 (OrderTransport, ManualOrders)
│       │   ├── engine.py        # 전략 실행, 신호 발행, 실시간 위험 감시 (Engine)
│       │   ├── market.py        # 전체 KRW WebSocket/REST 수신 및 캔들 빌더 (Market)
│       │   ├── private_sync.py  # 빗썸 Private WS 및 잔고/체결 동기화 (PrivateSync)
│       │   ├── private_auth.py  # 빗썸 JWT 서명 생성기 (BithumbAuth)
│       │   ├── private_client.py# 빗썸 Private REST HTTP 클라이언트 (PrivateClient)
│       │   ├── ledger.py        # 전략 원장 계산, 체결 반영, 손익 산출
│       │   ├── storage.py       # SQLite 마이그레이션 및 쿼리 관리 (Schema 6)
│       │   ├── trade_review.py  # 사후 거래 복기 및 캔들 리플레이 데이터
│       │   └── app.py           # FastAPI 웹 서버 및 백그라운드 태스크 생명주기
│       └── tests/               # 150여 개의 단위 및 통합 테스트 모음
├── docs/                        # 기술 문서 및 사양서 모음
│   ├── auto-trading.md          # 신호 자동매매 운영 규칙 및 API 규격
│   ├── live-order-design.md     # 주문 실행기 및 복구 상태머신 설계
│   ├── task-10-full-krw-scanner.md # 전체 KRW 스캐너 규격
│   └── task-11-chart-replay-review.md # 차트 리플레이 규격
└── README.md                    # 사용자 실행 안내서
```

---

## 4. 실전 자동매매 파이프라인 상세

### 4.1 신호 감지 및 우선순위 (Execution Priority)
`Automation.tick()`은 1초 주기로 백그라운드에서 동작하며, `signals` 테이블에서 만료되지 않은 유효 신호를 탐색합니다:
1. **0순위 - `RISK_EXIT`**: 보유 포지션의 손절가 이탈(-2.5% 등) 또는 호가 유동성 악화(스프레드 0.3% 초과) 감지 시 즉시 전량 시장가 매도.
2. **1순위 - `SELL_CANDIDATE`**: 목표가 도달(+2.0% 등) 또는 최대 보유 시간(30분 등) 만료 시 시장가 매도.
3. **2순위 - `BUY_CANDIDATE`**: 승인된 전략 버전(`strategy_version == self.version`)과 일치하는 신호에 대해 예산 내 시장가 매수.

### 4.2 중복 실행 방지 및 원자적 예약 (Atomic Claim)
```sql
INSERT OR IGNORE INTO auto_signal_claims 
VALUES (account_id, signal_id, NULL, 'CLAIMED', now(), NULL);
```
- 고유 복합 키 `(account_id, signal_id)`로 `auto_signal_claims`에 선점 예약합니다.
- 이미 처리되었거나 처리 중인 신호는 재실행되지 않으며, 서버 재시작 시에도 복제 주문이 발생하지 않습니다.
- 동일 종목 매수 후 **최소 60초 쿨다운**이 적용됩니다.

### 4.3 주문 상태 전이도 (Order State Machine)

```
[ 신호 발생 ]
      │
      ▼
   PREVIEW  ───────> [ 조건 미충족 / 호가 변동 0.3% 초과 ] ──> REJECTED / EXPIRED
      │
      ▼ (BEGIN IMMEDIATE 원자적 트랜잭션)
  SUBMITTING ─────> [ 네트워크 에러 / 응답 유실 / 5xx ] ──> UNKNOWN (조회 대기)
      │
      ▼ (거래소 201 Created 접수)
 ACKNOWLEDGED ────> [ Private WS myOrder / GET /v1/order ] ──> DONE (체결 완료)
```

- **`SUBMITTING` / `UNKNOWN` 발생 시**: 절대 재전송하지 않으며, `GET /v1/order`로 거래소 ID 조회를 통해서만 확인합니다.
- **체결 동기화(`manual_orders.wake`)**: Private WebSocket에서 체결 이벤트(`myOrder`)가 들어오거나 주문 접수 시, `self.wake.set()`을 통해 5초 대기 없이 즉시 `recover()`가 호출되어 원장에 체결 내역이 반영됩니다.

---

## 5. 데이터베이스 스키마 (SQLite Schema 6)

DB 파일 경로: `data/manual-assistant.sqlite3`

| 테이블명 | 역할 |
|---|---|
| `signals` | 시스템이 생성한 모든 매수/매도/손절 신호 (`id`, `symbol`, `type`, `version`, `payload`, `generated_at`, `expires_at`) |
| `auto_signal_claims` | 자동매매 신호 실행 상태 추적 (`account_id`, `signal_id`, `order_intent_id`, `status`, `created_at`, `message`) |
| `manual_orders` | 앱에서 발행된 모든 실제 주문 의도 (`id`, `market`, `side`, `budget`, `quantity`, `parameters`, `details`, `status`, `order_id`, `signal_id`, `expires_at`) |
| `manual_fills` | 전략 원장에 공인된 체결 내역 (`id`, `symbol`, `side`, `quantity`, `price`, `fee_krw`, `filled_at`, `recorded_at`, `source`) |
| `private_orders` | 거래소 계좌의 전체 주문 이력 동기화 스냅샷 |
| `private_fills` | 거래소 계좌의 전체 체결 이력 동기화 스냅샷 (`trade_id`, `order_id`, `fee_krw`, `fee_status`) |
| `private_accounts` | 계좌 잔고 스냅샷 (`currency`, `balance`, `locked`, `avg_buy_price`) |
| `decision_events` | Task 11 차트 복기용 불변 이벤트 로그 (`ORDER_PREVIEW`, `ORDER_REQUEST`, `ORDER_ACK`, `ORDER_STATE`, `FILL` 등) |
| `trading_control` | 전역 매매 통제 (`mode`: `AUTO_APPROVAL` / `MANUAL_APPROVAL`, `state`: `RUNNING` / `STOPPED` / `EMERGENCY_STOP`) |

---

## 6. 리스크 관리 규격 (Safety Rails)

서버(`engine.py`, `manual_orders.py`)는 매 주문 시점마다 다음 조건을 강제 검증합니다:

1. **운용 자본 상한**: 총 전략 자본 150,000원 이하 (수익이 발생해도 주문 금액을 자동 증액하지 않음).
2. **1회 최대 주문**: 신규 매수 시 수수료 포함 최대 50,000원.
3. **일일 손실 킬스위치**: 당일 실현 손익 + 현재 평가 손익이 **-3,000원** 도달 시 신규 매수 즉각 차단.
4. **현금 여유율**: 최소 현금 여유(기본 30,000원) 미만으로 떨어지는 매수 차단.
5. **동일 종목 쿨다운**: 최근 60초 이내 체결 또는 주문 시도된 종목 재진입 금지.
6. **호가 스프레드 제한**: 최우선 매도호가와 매수호가 간 스프레드가 0.3% 초과 시 주문 차단.
7. **호가 깊이 및 슬리피지 검사**: 주문 금액을 체결시킬 매도/매수 잔량이 부족하거나 슬리피지 기준(0.1%) 초과 시 주문 차단.

---

## 7. 실제 빗썸 계좌 연동 운영 가이드 (Runbook)

### 7.1 사전 준비
1. 빗썸 API 관리 페이지에서 신규 API 키 발급:
   - **필수 권한**: `자산조회`, `주문조회`, `주문하기` (3가지 모두 필수)
   - **IP 제한**: 개발 머신의 공인 IP 등록
2. 프로젝트 루트에 `.env` 생성 및 권한 설정:
   ```sh
   cat << 'EOF' > .env
   BITHUMB_PRIVATE_ENABLED=true
   BITHUMB_KEYS_ROTATED=true
   BITHUMB_ACCESS_KEY=<YOUR_ACCESS_KEY>
   BITHUMB_SECRET_KEY=<YOUR_SECRET_KEY>
   LIVE_ORDER_ENABLED=false
   EOF

   chmod 600 .env
   ```
   > **주의**: `LIVE_ORDER_ENABLED=false`는 안전 플래그이며, 웹 화면의 명시적 활성화 없이 환경변수만으로 주문이 나가지 않도록 보호합니다.

### 7.2 서비스 실행
```sh
# 프론트엔드 및 백엔드 동시 실행 (Docker 불필요)
npm run manual:dev
```
- 웹 대시보드: `http://127.0.0.1:13000`
- API 헬스체크: `http://127.0.0.1:18000/api/health`

### 7.3 실전 자동매매 시작 절차
1. **계좌 탭**: Private API 연결 상태가 정상인지 확인하고, 잔고 및 체결 내역을 동기화합니다.
2. **Strategy Lab 탭**:
   - 전략 백테스트를 실행하고 검증(Validation) 조건을 통과시킵니다.
   - 전략 버전을 '실전 전략으로 승인(Approve)'합니다. (유효기간: 72시간)
3. **시장 탭 > 신호 자동매매 패널**:
   - 매수 예산(예: 10,000원) 입력
   - "새 로컬 API 키의 주문하기 권한을 확인했습니다" 체크박스 선택
   - 확인 문구 입력: `신호에 따라 실제 자동매매를 시작합니다`
   - `자동매매 시작` 버튼 클릭!
4. **실행 중 모니터링**:
   - 체결 발생 시 상단 토스트 알림 및 브라우저 사운드 알림이 울립니다.
   - 활동 로그 및 주문 상태가 실시간 갱신됩니다.
   - 언제든지 `자동매매 정지` 버튼을 눌러 안전하게 멈출 수 있습니다.

---

## 8. 개발 및 검증 명령어 세트

```sh
# 1. 파이썬 유닛/통합 테스트 (150여 개)
services/manual-assistant/.venv/bin/pytest -q services/manual-assistant/tests

# 2. 코드 스타일 및 린트 검사
services/manual-assistant/.venv/bin/ruff check services/manual-assistant

# 3. 타입 안정성 검사 (Python & TypeScript)
services/manual-assistant/.venv/bin/mypy services/manual-assistant/manual_assistant
npm run typecheck

# 4. 프론트엔드 린트 및 프로덕션 빌드
npm run lint
npm run build -w @bsb/web

# 5. 비밀정보 누출 검사
node scripts/check-secrets.mjs
```

---

## 9. 코덱스(Codex)를 위한 향후 개발 과제 (TODO List)

다음 개발을 이어받을 코덱스는 아래 항목들을 우선적으로 검토 및 구현할 수 있습니다:

1. **지정가 주문(Limit Order) 및 분할 체결 전략 지원**:
   - 현재 시스템은 시장가(`price`, `market`) 체결 기준입니다. 슬리피지를 더 줄이기 위해 최우선 호가 기반 지정가 주문 및 타임아웃 미체결 시 취소/정정 로직 확장을 고려할 수 있습니다.
2. **동적 포지션 사이징 (Dynamic Sizing)**:
   - 현재는 고정 예산(예: 10,000원) 매수 방식입니다. 변동성(ATR)이나 승률에 따른 켈리 공식 기반 동적 사이징 모듈을 `Automation`에 플러그인할 수 있습니다.
3. **외부 텔레그램 / 디스코드 웹훅 알림**:
   - 현재 브라우저 Web Notification 및 Audio 알림 외에, 서버가 백그라운드에서 동작할 때 텔레그램 봇으로 체결 및 긴급 정지 알림을 발송하는 채널 확장이 유용합니다.
4. **다중 전략 동시 운용 모드**:
   - 현재는 1개의 승인된 전략 버전을 고정하여 실행합니다. 전략별 자본 배분 및 다중 전략 슬롯 시스템을 추가할 수 있습니다.
