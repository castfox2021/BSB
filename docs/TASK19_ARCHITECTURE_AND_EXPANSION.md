# TASK 19 — 공개 플랫폼 전환, 레거시 정리 및 향후 확장 설계

## 1. 현행 구조 및 레거시 컴포넌트 조사
- **활성 컴포넌트 (신규 메뉴 재배치)**:
  - `reversion-panel.tsx`, `quiet-board.tsx` → `/admin` (운영 대시보드)
  - `trading-review.tsx`, `private-account.tsx`, `manual-fill-panel.tsx` → `/admin/trading` (자동매매 관제)
  - `quiet-details.tsx`, `full-market-scanner.tsx` → `/admin/market` (시장 분석)
  - `trading-analytics.tsx`, `ai-strategy-report.tsx` → `/admin/analytics` (거래 통계 — 중복되던 `전략 분석 > AI 리포트` 제거 및 통합)
  - `strategy-comparison.tsx`, `strategy-lab.tsx` → `/admin/lab` (전략 연구소)
  - `quiet-settings.tsx` → `/admin/settings` (운영 설정)
- **미사용/과거 레거시 컴포넌트 (참조 검증 후 보존, DB/API 삭제 없음)**:
  - `automatic-trading.tsx`, `direct-trading.tsx`, `history-paging.tsx`: `page.tsx` 및 신규 `/admin/*` 라우트에서 미참조 확인. 기존 파일은 하위 호환/참조용으로 보존하고 네비게이션에서 분리.

## 2. 신규 사이트맵
- **공개 영역 (비로그인 접근)**:
  - `/` — 랜딩페이지 (Hero, 실시간 시장 미리보기, 급락 반등 전략 소개, 공개 성과 미리보기, 주요 기능, 작동 방식, 로드맵, FAQ)
  - `/market` — 실시간 시장
  - `/strategies` — 전략 관찰 (가상매매·익명화 통계)
  - `/performance` — 공개 성과 분석
  - `/login` — 운영자 로그인
- **운영자 영역 (`bsb_session` HttpOnly 세션 필수)**:
  - `/admin` — 운영 대시보드
  - `/admin/trading` — 자동매매 관제
  - `/admin/market` — 시장 분석
  - `/admin/analytics` — 거래 통계 (Task 18/18-A 10개 서브탭)
  - `/admin/lab` — 전략 연구소
  - `/admin/settings` — 운영 설정

## 3. 공개 / 관리자 API 분리
- **공개 전용 DTO API (`/api/public/*`)**: `GET /api/public/market/summary`, `/api/public/market/tickers`, `/api/public/strategies/overview`, `/api/public/performance/summary`, `/api/public/platform/status` (허용 필드만 반환, 계좌·주문ID·API키·설정·내부로그 완전 배제).
- **인증 API (`/api/auth/*`)**: `POST /api/auth/login` (PBKDF2/scrypt 검증 + IP별 시도 제한), `POST /api/auth/logout`, `GET /api/auth/session`.
- **관리자 API (`/api/orders/*`, `/api/private/*`, `/api/trading/*`, `/api/settings`, `/api/reversion/config`, `/api/analytics/*`, `/api/admin/*`, `/api/lab/*`)**: FastAPI 서버 측 `admin_sessions` 테이블 검증 필수 (`Origin` + `X-BSB-Local` 헤더만으로 권한 부여 금지).

## 4. 향후 확장 기반 설계 (Phase 2+)
- **역할 모델 (`ADMIN` / `MEMBER`)**: `users(id, email, password_hash, role, mfa_secret, created_at)` 확장 시 모든 원장·주문·설정 테이블에 `user_id` 외래키 및 행 수준 격리(RLS/WHERE `user_id=?`) 적용.
- **거래소 Adapter 인터페이스**:
  - `ExchangeAdapter` 추상 인터페이스(`fetch_tickers`, `fetch_candles`, `fetch_orders`, `fetch_fills`, `place_order`, `cancel_order`) 아래 `BithumbAdapter`와 `UpbitAdapter`로 분리.
- **API 키 암호화 저장**: AES-256-GCM(또는 KMS envelope encryption)으로 `user_exchange_credentials(id, user_id, exchange, enc_access_key, enc_secret_key, key_version)` 저장, 복호화는 주문/동기화 워커 메모리 내에서만 수행.
- **SQLite → PostgreSQL 마이그레이션**: `storage.py`의 SQLite 연결 계층을 SQLAlchemy/asyncpg 풀로 교체하고, `Decimal` 컬럼을 `NUMERIC(36, 18)`로 1:1 이관.
