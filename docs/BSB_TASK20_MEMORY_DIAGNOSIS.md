# BSB Task 20 — FastAPI 메모리 과다 사용 원인 분석 및 개선 설계 보고서

> **작업 성격**: 1차 진단 전용 보고서 (운영 코드 무변경, 운영 DB 무변경, 프로세스 무재시작)  
> **작성일**: 2026-11-17  
> **대상**: `uvicorn manual_assistant.app:app` (FastAPI / Python 3.12 / SQLite)

---

## 1. Executive Summary

운영 서버에서 FastAPI 프로세스(`uvicorn manual_assistant.app:app`)가 **RAM 2.41 GiB(`VmRSS: 2,531,152 kB`), Swap 3.32 GiB(`VmSwap: 3,482,184 kB`), 고점 3.17 GiB(`VmHWM: 3,324,496 kB`), 스레드 32개**를 점유하는 원인을 정적 코드 분석, 로컬 SQLite(`504 MB`) 읽기 전용 점검, 그리고 `tracemalloc` 격리 재현을 통해 진단했다.

핵심 결론은 다음과 같다.
1. **순수 Python 힙(`Market` 400종목 + `RollingScanner` 10만 키 + `AnalyticsLedger` 1만 체결)만으로는 약 `27.7 MB`(피크 `37.6 MB`) 수준**이며, `RollingScanner.seen`(`100,000` 제한)이나 `Market.trade_queue`(`maxsize=30000`) 자체에는 무한 누적 버그가 없다.
2. 그럼에도 `VmRSS 2.41 GiB / VmSwap 3.32 GiB`까지 치솟는 원인은 크게 3가지의 복합 작용이다.
   - **(P0) `asyncio.to_thread` 32개 워커 스레드 × 초당 수십 회 `sqlite3.connect()` 생성/종료로 인한 glibc `malloc` 아레나 단편화 및 C 힙(`PrivateDirty`) 미반환**: 스레드 수 `32`는 Python 3.12 기본 `ThreadPoolExecutor` 상한(`min(32, cpu + 4)`)과 정확히 일치한다.
   - **(P0) `detail_analysis` (2초 주기 × 최대 64종목) 및 `AnalyticsSyncService` (60초 주기)의 대량 `fetchall()`·JSON 직렬화·`replay_candles` 폭증**: `market.py:599-625`가 2초마다 최대 64종목의 `load_contiguous(limit=100)`(6,400 캔들 딕셔너리)와 `archive_candles`/`BOOK` 이벤트를 반복 생성하며, `analytics_sync.py:386`·`analytics_ledger.py:188, 479`가 전체 `private_orders`/`private_fills`/`exchange_fills`/`trade_lot_allocations`를 반복 적재한다.
   - **(P1) `trade_review.py` / `strategy_report.py`의 무제한 `SELECT *` 전체 적재 및 `Market` 캐시(`detail_cache`, `latest`, `observations`) 만료 부재**: 대용량 DB(2.4GB)에서 리포트/복기 API 호출 시 수백 MB의 임시 딕셔너리가 할당되고, glibc 아레나에 `PrivateDirty`로 잔류한 뒤 Swap으로 밀려난다.

---

## 2. 현재 시스템 메모리 사용 현황

### 2.1 운영 서버 프로세스 상태 (문제 발생 시점 기준)

| 지표 | 측정값 | 환산 | 비고 |
|---|---:|---:|---|
| `VmRSS` | 2,531,152 kB | **2.41 GiB** | 물리 RAM(3.8 GiB)의 약 63% 점유 |
| `VmHWM` | 3,324,496 kB | **3.17 GiB** | 과거 피크 RSS |
| `VmSwap` | 3,482,184 kB | **3.32 GiB** | Swap(4.0 GiB)의 약 83% 점유 |
| `VmSize` | 7,235,352 kB | **6.90 GiB** | 가상 주소 공간 (32스레드 스택 + mmap/아레나 포함) |
| `PrivateDirty` | 2,530,972 kB | **2.41 GiB** | RSS의 99.99%가 공유 불가 익명 힙/아레나 메모리 |
| `Threads` | 32 | 32개 | `asyncio.to_thread` 기본 `ThreadPoolExecutor` 상한 |

### 2.2 SQLite 테이블 크기 분석 (로컬 `data/manual-assistant.sqlite3`, 504 MB 읽기 전용 측정)

운영 DB(`2.4 GB`)와 동일 스키마를 가진 로컬 스냅샷(`504 MB`, 총 38개 테이블)을 `?mode=ro`로 조회한 결과 상위 테이블은 다음과 같다.

| 순위 | 테이블명 | 행 수 | 크기 (MB) | 주요 생성 경로 |
|---:|---|---:|---:|---|
| 1 | `replay_candles` | 426,649 | **207.12** | `review_events.archive_candles` (`market.py:478`, `candles.py:128`) |
| 2 | `market_candles` | 580,191 | **101.96** | `candles.py:105, 180` |
| 3 | `market_events` | 50,103 | **32.71** | `candles.py:154` (체결 원시 JSON 저장) |
| 4 | `scanner_observations` | 8,955 | **11.02** | `market.py:634` (50,000건 상한 유지) |
| 5 | `lab_datasets` | 9 | **2.62** | `lab.py` |
| 6 | `decision_events` | 747 | **2.48** | `review_events.append_event` (`market.py:483` 호가 전체 JSON) |
| 7 | `market_issues` | 39,985 | **3.09** | `candles.py:269` (`gap()` 기록) |
| 8 | `lab_jobs` / `lab_runs` | 6 / 4 | **0.76** | `lab.py` |
| 9 | `signals` | 337 | **0.34** | `market.py:539`, `engine.py` |
| 10 | `private_fills` / `private_orders` | 547 / 617 | **0.26** | `private_sync.py` |

> **핵심 시사점**: 로컬 504MB DB에서도 `replay_candles` + `market_candles` + `market_events` 3개 테이블이 전체의 **68%(341.79 MB)**를 차지한다. 운영 서버 `2.4 GB` DB 역시 해당 아카이브/캔들 테이블이 대다수를 차지할 가능성이 높다.

---

## 3. 원인 후보별 분석

| 우선순위 | 파일/함수 | 원인 | 증거 | 재현 여부 | 개선 방법 |
|---|---|---|---|---|---|
| **P0** | `storage.py:Storage.connect` + `market.py` / `engine.py` (`asyncio.to_thread`) | 32개 워커 스레드에서 매 쿼리마다 `sqlite3.connect()` 생성/해제 및 대량 C 버퍼 할당으로 glibc `malloc` 아레나 단편화(`PrivateDirty 2.41 GiB`) | 운영 `Threads: 32`, `PrivateDirty: 2530972 kB`; `market.py:603` 2초×64회 + `candle_clock` 1초 + `engine.tick` 1초 스레드 풀 경합 | **부분 확인** (스레드/호출 빈도 코드 확인, Linux glibc 아레나는 운영 환경 특유) | 전용 소규모 `ThreadPoolExecutor(max_workers=4)` 제한, `MALLOC_ARENA_MAX=2` + 주기적 `ctypes.CDLL("libc.so.6").malloc_trim(0)`, 스레드 로컬 또는 풀링된 읽기 연결 재사용 |
| **P0** | `market.py:analysis_loop` (`590-626`) / `detail_analysis` (`469-513`) / `review_events.archive_candles` (`28-58`) | 2초마다 최대 64종목에 대해 `load_contiguous(100)`(6,400 딕셔너리) + `replay_candles` 중복 검사(`json.loads`) + `BOOK` 호가 전체 JSON 적재 | `replay_candles`가 504MB 중 `207.12 MB(426,649행)` 차지; 매 사이클 수천 개 `dict`/JSON 할당 | **확인** (코드 및 DB 용량 실측) | `detail_analysis` 주기 완화(예: 5~10초) 및 변동 종목만 선별, `WS_DETAIL_SNAPSHOT`의 `replay_candles` 상시 아카이빙 중단(신호/주문 시점만 보관), `BOOK` 이벤트 호가 상위 5호가만 경량 저장 |
| **P0** | `analytics_sync.py:ingest_existing_private_tables` (`275-345`) & `analytics_ledger.py:recalculate` (`172-200`), `query_analytics` (`479`) | 60초 백그라운드 동기화마다 `private_orders`·`private_fills` 전체 `fetchall()`, 재계산 시 `exchange_fills` 전체 적재 + `trade_lot_allocations` 전체 조회 | `tracemalloc`에서 1만 건 재계산+조회만으로 피크 **37.60 MB** 증가; 60초마다 반복 실행 | **확인** (`tracemalloc` 재현 완료) | `ingest_existing_private_tables` 증분 커서(`updated_at`/`filled_at` 기준) 적용, `sync_history`에서 신규 체결 0건이면 재계산 생략, `query_analytics` SQL `WHERE` 필터링·페이지네이션 적용 |
| **P1** | `trade_review.py` (`55-95`), `strategy_report.py` (`282-539`) | 복기/리포트 조회 시 `manual_fills`, `signals`, `decision_events`, `reversion_positions`, `private_fills` 전체 `SELECT *` 적재 | `trade_review.py:55-94` 5개 테이블 무제한 조회, `strategy_report.py:282-539` 4개 테이블 전체 조회 | **확인** (정적 호출 추적) | 기간(`LIMIT`/날짜 범위) 조건부 조회 및 스트리밍/커서 배치 처리 |
| **P1** | `market.py:self.latest`, `self.detail_cache`, `self.seeds`, `analysis_loop` (`observations`, `signaled`) | `unavailable` 또는 `detail_targets` 이탈 종목의 메모리 캐시 미삭제 | `market.py:39, 74, 567-568, 792`에 `pop`/`del` 없음 | **확인** (정적 코드 확인, 400종목 기준 상한은 ~5-15MB) | `update_universe` 시 제외 종목 `latest`/`detail_cache`/`seeds`/`rolling.buckets` 정리 및 `detail_cache` TTL(60초) 만료 |
| **P1** | `scanner.py:RollingScanner.seen` (`49-51`) | 중복 제거용 `OrderedDict` 상한 100,000건 유지 | `tracemalloc` 실측 **13.5 MB** (`OrderedDict` 8.93 MB + 키 4.58 MB) | **확인** (`tracemalloc` 재현 완료) | 상한을 `20,000`건(최근 수 분치) 또는 `(symbol, timestamp)` 기반 슬라이딩 윈도우로 축소 (~11MB 절감) |
| **P2** | `app.py:231-251` 백그라운드 태스크 수명주기 | `lifespan`에서 4개 태스크 생성, `jobs` set에 `done_callback(jobs.discard)` 적용됨. 중복 생성 없음 | `app.py:231-251, 1117-1118` 정상 취소 및 예외 처리 확인 | **확인 (정상)** | 현행 유지 (단, `market.supervise` 재시도 간격 유지) |
| **P2** | `market.py:trade_queue` (`67`), `pending_times` (`69, 851`) | `trade_queue`는 `maxsize=30000`으로 제한됨. 큐 포화 시 `QueueFull`로 드랍 후 `mark_gap` 처리 | `market.py:67, 848-856` 확인 | **확인 (제한됨)** | `maxsize=10000` 하향 검토 (메모리 피크 ~10MB 추가 절감) |

---

## 4. 소스 코드 증거 및 호출 경로

### 4.1 FastAPI 시작/종료 (`app.py`)
- `app.py:230-251`: `lifespan` 진입 시 `evaluator(engine.run)`, `order_monitor(orders.run)`, `automation_runner(automation.run)`, `analytics_task(analytics_sync.run)` 4개 태스크를 1회 생성하며, 종료 시 `cancel()` + `asyncio.gather(..., return_exceptions=True)`로 정상 정리된다.
- `app.py:1116-1118`: 백테스트 `lab_jobs`는 `jobs.add(task); task.add_done_callback(jobs.discard)`로 완료 후 참조가 해제된다.
- **따라서 태스크 중복 생성이나 완료 태스크 참조 누수는 원인이 아니다.**

### 4.2 시장 데이터 처리 (`market.py`, `candles.py`, `scanner.py`, `review_events.py`)
1. `Market.run()` (`market.py:1011-1040`) → `supervise()`로 7개 워커(`seed_worker`, `writer_loop`, `candle_clock`, `analysis_loop`, `details_loop`, `model_loop`, `seed_retry_loop`)와 `feeds`/`books` WebSocket 샤드 태스크 실행.
2. `reconcile_shards()` (`market.py:736-775`): `retired` 샤드는 `cancel()` + `gather()` + `del registry[key]`로 정상 정리된다.
3. **`analysis_loop()` (`market.py:566-630`)**:
   - 1초마다 `scan_basic()` 실행 → 400+ 종목의 `self.rows` 리스트 재생성.
   - 2초(`overloaded` 시 10초)마다 `self.detail_targets` (최대 64개) 각각에 대해 `await asyncio.to_thread(self.detail_analysis, symbol, book, ...)` 호출.
   - `detail_analysis()` (`market.py:469-513`) → `archive_candles()` + `append_event("BOOK", ...)` + `load_contiguous(limit=100)` 실행.
4. `Market.trade_queue` (`market.py:67`): `asyncio.Queue(maxsize=30000)`. `writer_loop` (`676-713`)에서 200건씩 배치 소비하며, `finally`에서 `pending_times[symbol].popleft()` 수행.

### 4.3 거래 기록 및 분석 동기화 (`analytics_sync.py`, `analytics_ledger.py`)
1. `AnalyticsSyncService.run()` (`analytics_sync.py:721-729`): 60초마다 `sync_history()` 호출.
2. `sync_history()` (`347-536`) → `ingest_existing_private_tables(conn)` (`275-345`):
   - 매번 `SELECT * FROM private_orders` 및 `SELECT * FROM private_fills` 전체를 `.fetchall()`로 읽어 `INSERT OR IGNORE` 반복.
3. `AnalyticsLedger.recalculate()` (`analytics_ledger.py:172-196`):
   - `exchange_fills` 전체를 `SELECT * ... ORDER BY filled_at ASC, id ASC`로 Python 리스트 적재 후 `Decimal` 계산.
4. `AnalyticsLedger.query_analytics()` (`analytics_ledger.py:479`):
   - 필터링 전 `SELECT * FROM trade_lot_allocations` 전체를 읽어 Python에서 필터링.

### 4.4 매매 엔진 (`engine.py`, `reversion.py`, `manual_orders.py`)
- `engine.run()` (`engine.py:840-851`): 1초마다 `await asyncio.to_thread(self.tick)` 실행 → `reversion.tick()` 호출.
- `reversion.py:878`: `self.watch`에 등록된 각 종목마다 2초 주기로 `load_contiguous(symbol, limit=100)` 호출.
- `pending_limit_cancels` (`engine.py:780, 804-838`): 20초 타임아웃 후 `still_waiting`으로 리스트 교체되므로 누적되지 않음.

---

## 5. 실제 재현 여부 (`tracemalloc` 격리 측정)

로컬 Python 3.12 환경에서 임시 SQLite(`/tmp`)와 `TestClient`를 이용해 모듈별 Python 힙 할당량을 측정했다.

| 측정 단계 | `current` (MB) | `peak` (MB) | 주요 할당 지점 |
|---|---:|---:|---|
| `start` | 0.00 | 0.00 | - |
| `fastapi_lifespan` (`market.enabled=False`) | **3.67** | **3.68** | FastAPI 라우팅 테이블(`178 KiB`), 모듈 임포트 |
| `market_400syms_100k_seen` (400종목 + 10만 틱 + `scan_basic`) | **19.01** | **19.06** | `scanner.py:49` `self.seen` (`8.93 MB` + 키 `4.58 MB`) |
| `analytics_10k_fills_recalc_query` (1만 체결 `recalculate` + `query_analytics`) | **27.68** | **37.60** | `analytics_ledger.py:533, 530` lot 딕셔너리 리스트 |

---

## 6. 확정 원인과 미확정 가설 구분

### 확정 원인 (코드·DB·`tracemalloc`으로 검증됨)
1. **`AnalyticsSyncService` / `AnalyticsLedger`의 주기적 전체 테이블 적재**: 매 60초 전체 `private_orders`/`private_fills` 스캔 및 `recalculate`/`query_analytics`의 `exchange_fills`/`trade_lot_allocations` 전체 적재.
2. **`detail_analysis` (2초×64종목) 및 `replay_candles` 아카이빙 과부하**: `replay_candles` 42만 행(`207 MB`) 폭증 및 초당 수십 회 `asyncio.to_thread` DB·JSON 작업 생성.
3. **`trade_review.py` / `strategy_report.py`의 무제한 `SELECT *` 조회**: 대용량 DB에서 복기/리포트 호출 시 대량 딕셔너리 급증.
4. **`RollingScanner.seen` 10만 건(`13.5 MB`) 및 `Market` 캐시 미만료**.

### 미확정 가설 (운영 서버 `glibc` / 32스레드 환경에서만 최종 검증 필요)
- **glibc `malloc` 32-스레드 아레나 단편화 가설**: Python 힙이 ~30~100MB 수준인데 `PrivateDirty`가 `2.41 GiB`, `VmSwap`이 `3.32 GiB`에 달한 현상은 Ubuntu 22.04 `glibc`가 32개 `asyncio.to_thread` 워커 스레드별로 별도 아레나를 유지하며 해제된 C/SQLite/Python 버퍼를 OS에 반환하지 않기 때문으로 강하게 추정된다. 운영 서버에서 `/proc/3122184/smaps_rollup` 및 `MALLOC_ARENA_MAX=2` 적용 전후 비교로 최종 확정한다.

---

## 7. 개선 우선순위

1. **P0 — `asyncio.to_thread` 스레드 수 제한 + glibc 메모리 반환 + DB 읽기 연결 최적화**
   - `app.py` 시작 시 기본 executor를 `ThreadPoolExecutor(max_workers=6)`으로 제한 (32스레드 아레나 방지).
   - 환경변수 `MALLOC_ARENA_MAX=2` 적용 및 주기적(예: 5분) `gc.collect()` + Linux `malloc_trim(0)` 호출.
2. **P0 — `detail_analysis` 경량화 및 `replay_candles` 상시 적재 중단**
   - `WS_DETAIL_SNAPSHOT` 경로의 `archive_candles` 호출 제거(신호/주문 결정 시점에만 아카이빙 유지).
   - `BOOK` 이벤트 저장 시 30호가 전체 대신 최우선 5호가 요약만 저장.
   - `detail_analysis` 실행 주기를 `2s → 5s` (과부하 시 `15s`)로 완화.
3. **P0 — `AnalyticsSyncService` 증분 동기화 및 `AnalyticsLedger` SQL 필터링**
   - `ingest_existing_private_tables`: 마지막 동기화 이후 변경분만 조회(`WHERE updated_at > ?` / `filled_at > ?`).
   - `query_analytics`: `trade_lot_allocations`를 `WHERE closed_at >= ? AND market = ?` 등 SQL 레벨에서 필터링·제한.
4. **P1 — `RollingScanner.seen` 상한 축소(`100,000 → 20,000`) 및 `Market` 캐시 TTL/정리**
   - `detail_cache` 60초 TTL 만료, `update_universe` 시 이탈 종목 `latest`/`seeds`/`rolling.buckets` 정리.
5. **P1 — `trade_review.py` / `strategy_report.py` 기간/LIMIT 배치 조회 전환**
   - `SELECT *` 제거, 최근 N일 또는 `LIMIT` 기반 페이지네이션 적용.

---

## 8. 수정 대상 파일

| 파일 | 변경 목적 |
|---|---|
| `services/manual-assistant/manual_assistant/app.py` | `ThreadPoolExecutor(max_workers=6)` 설정, 주기적 메모리 정리(`gc.collect` + `malloc_trim(0)`) 태스크 |
| `services/manual-assistant/manual_assistant/market.py` | `detail_analysis` 주기 조정, `WS_DETAIL_SNAPSHOT` 아카이빙 제거, `BOOK` 호가 요약, `detail_cache`/이탈 종목 정리 |
| `services/manual-assistant/manual_assistant/scanner.py` | `self.seen` 상한 `100,000 → 20,000` 축소 |
| `services/manual-assistant/manual_assistant/analytics_sync.py` | `ingest_existing_private_tables` 증분 조회 및 무변경 시 재계산 스킵 |
| `services/manual-assistant/manual_assistant/analytics_ledger.py` | `query_analytics` SQL 조건부 필터링 및 `recalculate` 메모리 최적화 |
| `services/manual-assistant/manual_assistant/trade_review.py` | `SELECT *` 전체 적재 제거 및 최근 기간/건수 제한 |
| `services/manual-assistant/manual_assistant/strategy_report.py` | 리포트 생성 시 기간 필터링 및 배치 조회 |

---

## 9. 예상 메모리 절감 효과와 추정 근거

| 개선 항목 | 예상 절감량 | 추정 근거 |
|---|---:|---|
| `ThreadPoolExecutor(max_workers=6)` + `MALLOC_ARENA_MAX=2` + `malloc_trim(0)` | **1.5 ~ 2.5 GiB** (RSS+Swap) | 32개 스레드 glibc 아레나(`PrivateDirty 2.41 GiB`)를 2개 아레나로 축소 및 미사용 C 힙 OS 반환 |
| `detail_analysis` 경량화 + `replay_candles` 아카이빙 중단 | **200 ~ 400 MB** 피크 억제 + 디스크 증가 중단 | 2초×64종목 대량 JSON/딕셔너리 할당 빈도 70% 이상 감소 |
| `AnalyticsSync` 증분화 + `AnalyticsLedger` / `trade_review` SQL 필터 | **100 ~ 500 MB** 피크 억제 | 대용량 테이블 전체 `fetchall()` 제거 |
| `RollingScanner.seen` 축소 + `Market` 캐시 정리 | **15 ~ 25 MB** 상시 절감 | `tracemalloc` 실측 `13.5 MB` → `~2.7 MB` |
| **종합 목표** | **RSS 250 ~ 450 MB 이하, Swap ~0 MB** | 3.8 GiB RAM 환경에서 스왑 없이 안정 운영 가능 |

---

## 10. PostgreSQL 관련 검토 및 회귀 테스트·운영 적용 계획

### 10.1 PostgreSQL 이관 검토 (`pg_migration.py`)
- **현황**: SQLite에는 총 **38개 테이블**이 존재하며, `pg_migration.py`의 `PG_DDL`에는 회원/거래 핵심 **5개 테이블**(`users`, `user_exchange_credentials`, `exchange_orders`, `exchange_fills`, `trade_positions`)만 정의되어 있다.
- **누락 테이블 (이관 우선순위)**:
  - 분석/포지션: `trade_lot_allocations`, `analytics_sync_jobs`, `analytics_reconciliation_issues`, `asset_cost_baselines`, `reversion_positions`, `position_exit_policy`, `position_exit_leg`, `position_exit_event`
  - 운영/감사: `settings`, `system_events`, `signals`, `decision_events`
  - 시세 아카이브(`market.sqlite3` 분리 유지 권장): `market_candles`, `replay_candles`
- **SQLite 전용 문법**: `INSERT OR IGNORE`, `INSERT OR REPLACE`, `SELECT changes()`, `PRAGMA`, `sqlite3.Row` → PostgreSQL 전환 시 `ON CONFLICT DO NOTHING/UPDATE`, `RETURNING`, `psycopg` dict row로 치환 필요.

### 10.2 회귀 테스트 및 운영 적용 계획
1. **보존 필수 기능**: 급락 반등 진입(`reversion.py`), 실시간 시세/호가 안전검사(`market.py`), 자동 손절·분할 익절(`_protect_partial`), `auto_buy_only=True` 지정가 자동매수 및 20초 미체결 취소(`engine.py`), Task 18 손익 분석(`analytics_ledger.py`).
2. **단계적 적용**:
   - **Step 1**: `ThreadPoolExecutor(max_workers=6)` + `MALLOC_ARENA_MAX=2` + `malloc_trim(0)` 적용 (매매 로직 무변경).
   - **Step 2**: `detail_analysis` 아카이빙/주기 최적화 + `scanner.seen` 축소 + `AnalyticsSync` 증분화.
   - **Step 3**: `trade_review.py` / `strategy_report.py` / `analytics_ledger.py` SQL 필터링.
3. **검증**: 기존 `pytest` 81개 전체 통과 확인 후 스테이징/운영 배포, 배포 후 `ps` / `/proc/<pid>/status` (`VmRSS`, `VmSwap`, `Threads`) 24시간 추이 모니터링.
