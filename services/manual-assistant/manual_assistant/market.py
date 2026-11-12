import asyncio
import contextlib
import hashlib
import json
import logging
import random
import re
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from .candles import Candles
from .ledger import decimal_text
from .liquidity import assess
from .normalize import InvalidMessage, SubscriptionError, normalize
from .scanner import RollingScanner
from .scanner_model import ScannerModel

D = Decimal
logger = logging.getLogger("bsb.manual.market")
REST = "https://api.bithumb.com"
WS = "wss://ws-api.bithumb.com/websocket/v1"


class Market:
    def __init__(self, store, enabled=True):
        self.store, self.enabled = store, enabled
        self.book = Candles(store)
        self.rolling = RollingScanner()
        self.model = ScannerModel(store)
        self.latest = {}
        self.connections = {}
        self.coverage = {}
        self.universe = []
        self.market_metadata = {}
        self.analysis_budget = None
        self.unavailable = set()
        self.seeds = {}
        self.pending = set()
        self.queue = asyncio.PriorityQueue()
        self.seed_priorities = {}
        self.seed_counter = 0
        self.inflight = set()
        self.tasks = []
        self.feeds = {}
        self.books = {}
        self.detail_sources = {}
        self.positions = set()
        self.real_positions = set()
        self.paper_positions = set()
        self.policy_at = 0.0
        self.policy_cache = None
        self.manual_detail = {}
        self.detail_targets = set()
        self.detail_capacity = 64  # CPU work budget, not a market-universe limit.
        self.detail_cursor = 0
        self.detail_last_switch = 0.0
        self.batch_enabled = False
        self.trade_queue = asyncio.Queue(maxsize=30000)
        self.trade_waiting = {}
        self.pending_times = {}
        self.loss_minute = {}
        self.seed_retry = {}
        self.gaps = set()
        self.rows = []
        self.detail_cache = {}
        self.invalid_until = {}
        self.receive_timeout = 30.0
        self.shard_size = 100
        self.universe_refresh_seconds = 300
        self.last_universe_refresh = None
        self.last_received = None
        self.error = None
        self.rest_lock = asyncio.Lock()
        self.ws_lock = asyncio.Lock()
        self.rest_next = 0.0
        self.rest_rps = 5
        self.ws_next = 0.0
        self.received_at = {}
        self.dirty = set()
        self.http = None
        self.loop = None
        self.loop_thread = None
        self.overloaded = False
        self.last_detail_analysis = 0.0
        self.metrics = dict(
            events=0,
            duplicates=0,
            invalid=0,
            reconnects=0,
            rest_calls=0,
            signal_compute_ms=None,
            queue_drops=0,
            scan_ms=0.0,
            detail_ms=0.0,
            event_loop_lag_ms=0.0,
            detail_checks=0,
            batch_errors=0,
        )

    async def request(self, path, params=None):
        async with self.rest_lock:
            await asyncio.sleep(max(0, self.rest_next - time.monotonic()))
            self.rest_next = time.monotonic() + 1 / self.rest_rps
            self.metrics["rest_calls"] += 1
            client = self.http or httpx.AsyncClient(
                timeout=12, follow_redirects=False, trust_env=False
            )
            try:
                response = await client.get(REST + path, params=params)
                if response.status_code in (418, 429):
                    try:
                        delay = max(
                            60,
                            min(600, float(response.headers.get("Retry-After", "60"))),
                        )
                    except ValueError:
                        delay = 60
                    self.rest_next = time.monotonic() + delay
                response.raise_for_status()
                return json.loads(response.text, parse_float=D)
            finally:
                if self.http is None:
                    await client.aclose()

    def seed_later(self, symbol, priority=None):
        if self.loop and threading.get_ident() != self.loop_thread:
            self.loop.call_soon_threadsafe(self.seed_later, symbol, priority)
            return
        if symbol in self.unavailable or symbol in self.inflight:
            return
        if self.seed_retry.get(symbol, (0, 0))[0] > time.monotonic():
            return
        priority = (
            priority if priority is not None else 0 if symbol in self.positions else 1
        )
        if symbol in self.pending and priority >= self.seed_priorities.get(
            symbol, priority
        ):
            return
        self.pending.add(symbol)
        self.seed_priorities[symbol] = priority
        self.seed_counter += 1
        self.queue.put_nowait((priority, self.seed_counter, symbol))

    async def seed_worker(self):
        while True:
            priority, _, symbol = await self.queue.get()
            if (
                symbol not in self.pending
                or self.seed_priorities.get(symbol) != priority
                or symbol in self.unavailable
            ):
                self.queue.task_done()
                continue
            self.inflight.add(symbol)
            try:
                raw = await self.request(
                    "/v1/candles/minutes/1", {"market": symbol, "count": 200}
                )
                starts = await asyncio.to_thread(
                    self.book.seed, symbol, raw, time.time()
                )
                bars = await asyncio.to_thread(self.book.load, symbol, 60, 80)
                self.seeds[symbol] = max(starts) if starts else None
                self.rolling.seed(symbol, bars)
                missing = self.loss_minute.get(symbol)
                if missing is not None and missing not in starts:
                    self.seed_retry[symbol] = (time.monotonic() + 30, priority)
                else:
                    self.loss_minute.pop(symbol, None)
                    self.seed_retry.pop(symbol, None)
                    self.dirty.discard(symbol)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.error = f"캔들 복구 실패: {type(error).__name__}"
                self.seeds.pop(symbol, None)
                self.dirty.add(symbol)
                self.seed_retry[symbol] = (time.monotonic() + 15, priority)
                await asyncio.sleep(3)
            finally:
                self.inflight.discard(symbol)
                self.pending.discard(symbol)
                self.seed_priorities.pop(symbol, None)
                self.queue.task_done()

    def set_positions(self, symbols, paper_symbols=()):
        if self.loop and threading.get_ident() != self.loop_thread:
            self.loop.call_soon_threadsafe(
                self.set_positions, set(symbols), set(paper_symbols)
            )
            return
        self.real_positions = set(symbols)
        self.paper_positions = set(paper_symbols)
        self.positions = self.real_positions | self.paper_positions
        for symbol in self.positions:
            if symbol not in self.seeds or symbol in self.dirty:
                self.seed_later(symbol, 0)

    def request_detail(self, symbol):
        self.manual_detail[symbol] = time.monotonic() + 60
        if symbol not in self.seeds or symbol in self.dirty:
            self.seed_later(symbol, 0)

    def set_analysis_budget(self, budget):
        self.analysis_budget = budget
        self.detail_cache = {}

    def policy(self):
        if self.policy_cache is None or time.monotonic() - self.policy_at > 1:
            self.policy_cache = self.store.settings()
            self.policy_at = time.monotonic()
        return self.policy_cache

    def basic_guard(self, symbol):
        if (
            not self.enabled
            or not self.connections.get(symbol)
            or symbol in self.unavailable
        ):
            return False, "연결 끊김 또는 거래 지원 중단"
        if self.invalid_until.get(symbol, 0) > time.monotonic():
            return False, "이상 시세·손상 데이터 안정화 대기"
        streams = self.latest.get(symbol, {})
        for kind in ("trade", "ticker"):
            event = streams.get(kind)
            if not event or time.monotonic() - event["_received_mono"] > 15:
                return False, f"{kind} 오래됨/미수신"
            if event.get("stream_type") != "REALTIME":
                return False, "실시간 스트림 대기"
            if not -1000 <= event["_exchange_lag_ms"] <= 15000:
                return False, "시계 차이 또는 수신 지연"
        ticker = streams["ticker"]
        meta = self.market_metadata.get(symbol, {})
        if (
            meta.get("market_state") not in (None, "ACTIVE", "active")
            or meta.get("market_warning", "NONE") != "NONE"
        ):
            return False, "거래 대상 목록의 지원 상태·유의 정보 확인 필요"
        if (
            ticker["market_state"] != "ACTIVE"
            or ticker["is_trading_suspended"] is not False
            or ticker["market_warning"] != "NONE"
        ):
            return False, "거래 상태·유의 정보 확인 필요"
        waiting = self.trade_waiting.get(symbol)
        if waiting is not None and time.monotonic() - waiting > 2:
            return False, "체결 저장 지연·누락 복구 대기"
        if symbol in self.dirty:
            return False, "API 데이터 누락·연결 복구 중"
        return True, "정상"

    def trade_guard(self, symbol):
        ok, reason = self.basic_guard(symbol)
        if not ok:
            return ok, reason
        book = self.latest.get(symbol, {}).get("orderbook")
        if (
            not book
            or time.monotonic() - book["_received_mono"] > 15
            or book.get("stream_type") != "REALTIME"
            or not -1000 <= book["_exchange_lag_ms"] <= 15000
        ):
            return False, "orderbook 오래됨/미수신 또는 시계 차이"
        return True, "정상"

    def safe(self, symbol):
        ok, reason = self.trade_guard(symbol)
        if not ok:
            return ok, reason
        policy = self.policy()
        ok, reason, _ = assess(
            self.latest[symbol]["orderbook"],
            self.analysis_budget or policy["max_entry_krw"],
            policy["execution_slippage_rate"],
        )
        if not ok:
            return ok, reason
        if symbol not in self.seeds or symbol in self.pending:
            return False, "초기 로딩·연결 복구 중"
        return True, "정상"

    def quote(self, symbol):
        streams = self.latest.get(symbol, {})
        book = streams.get("orderbook")
        if (
            not book
            or time.monotonic() - book["_received_mono"] > 15
            or not self.connections.get(symbol)
            or symbol in self.unavailable
            or self.invalid_until.get(symbol, 0) > time.monotonic()
            or not -1000 <= book["_exchange_lag_ms"] <= 15000
        ):
            return None
        if symbol in self.detail_sources and not self.detail_sources[symbol]:
            return None
        ticker = streams.get("ticker")
        if self.market_metadata.get(symbol, {}).get("market_state") not in (
            None,
            "ACTIVE",
            "active",
        ):
            return None
        if (
            not ticker
            or ticker["market_state"] != "ACTIVE"
            or ticker["is_trading_suspended"] is not False
            or time.monotonic() - ticker["_received_mono"] > 15
            or not -1000 <= ticker["_exchange_lag_ms"] <= 15000
        ):
            return None
        return book

    def scan_basic(self):
        started = time.perf_counter()
        symbols = self.universe or sorted(self.latest)
        rows = []
        for symbol in symbols:
            streams = self.latest.get(symbol, {})
            ticker = streams.get("ticker")
            stats = self.rolling.measure(symbol, time.time())
            fresh = bool(
                streams.get("trade")
                and streams["trade"].get("stream_type") == "REALTIME"
                and time.monotonic() - streams["trade"]["_received_mono"] <= 15
                and -1000 <= streams["trade"]["_exchange_lag_ms"] <= 15000
                and ticker
                and ticker.get("stream_type") == "REALTIME"
                and time.monotonic() - ticker["_received_mono"] <= 15
                and -1000 <= ticker["_exchange_lag_ms"] <= 15000
                and self.connections.get(symbol)
            )
            meta = self.market_metadata.get(symbol, {})
            trading = bool(
                meta.get("market_state") in (None, "ACTIVE", "active")
                and meta.get("market_warning", "NONE") == "NONE"
                and ticker
                and ticker.get("market_state") == "ACTIVE"
                and ticker.get("is_trading_suspended") is False
                and ticker.get("market_warning") == "NONE"
                and symbol not in self.unavailable
            )
            candidate = bool(
                stats["candidate"]
                and fresh
                and trading
                and self.invalid_until.get(symbol, 0) <= time.monotonic()
            )
            detail = self.detail_cache.get(symbol, {})
            book = streams.get("orderbook")
            spread = (
                decimal_text(
                    (D(book["asks"][0][0]) - D(book["bids"][0][0]))
                    / D(book["bids"][0][0])
                )
                if book
                else None
            )
            reason = (
                "상세 분석 대기"
                if candidate
                else "관찰"
                if fresh and trading
                else "시세·거래 상태 확인 필요"
            )
            ready = False
            if detail and symbol in self.detail_targets:
                ok, safe_reason = self.safe(symbol)
                ready = bool(
                    ok
                    and detail.get("contiguous")
                    and time.monotonic() - detail.get("at", 0) < 30
                )
                reason = (
                    safe_reason
                    if not ok
                    else "정상"
                    if ready
                    else "연속 완료 캔들 부족"
                )
            stats["candidate"] = candidate
            rows.append(
                dict(
                    symbol=symbol,
                    korean_name=meta.get("korean_name") or symbol.removeprefix("KRW-"),
                    english_name=meta.get("english_name") or symbol.removeprefix("KRW-"),
                    price=ticker["price"] if ticker else None,
                    turnover_24h=ticker["turnover_24h"] if ticker else None,
                    **stats,
                    volume_ratio=detail.get("volume_ratio", "0"),
                    spread=spread,
                    ready=ready,
                    reason=reason,
                    data_at=ticker["exchange_at"] if ticker else None,
                    experimental=True,
                    state="READY"
                    if ready
                    else "DETAIL"
                    if candidate
                    else "WATCH"
                    if fresh
                    else "STALE",
                    monitored=bool(self.connections.get(symbol)),
                    fresh=fresh,
                    trading_available=trading,
                    detail_selected=symbol in self.detail_targets,
                    held=symbol in self.real_positions,
                    paper_held=symbol in self.paper_positions,
                    expected_slippage=detail.get("expected_slippage"),
                    ai=detail.get("ai"),
                    ai_state="ADVISORY"
                    if detail.get("ai")
                    else "WARMUP"
                    if candidate
                    else "NOT_REQUESTED",
                )
            )
        rows.sort(key=lambda r: (-D(r["score"]), r["symbol"]))
        self.rows = rows
        self.metrics["scan_ms"] = (time.perf_counter() - started) * 1000
        return rows

    def candidates(self):
        # The signal engine only performs expensive checks on scheduled candidates.
        return [
            r
            for r in self.rows
            if r["detail_selected"]
            and (r["candidate"] or r["held"] or r.get("paper_held"))
        ]

    def detail_selection(self):
        held = self.positions - set(self.unavailable)
        requested = {
            s
            for s, until in self.manual_detail.items()
            if until > time.monotonic() and s not in self.unavailable
        }
        pool = [
            r["symbol"] for r in self.rows if r["candidate"] and r["symbol"] not in held
        ]
        slots = max(
            0,
            (
                max(8, self.detail_capacity // 4)
                if self.overloaded
                else self.detail_capacity
            )
            - len(requested),
        )
        # Half highest momentum, half a fair rotating queue: no permanent top-N exclusion.
        selected = set(pool[: slots // 2])
        rest = [s for s in pool if s not in selected]
        if rest and slots > len(selected):
            offset = self.detail_cursor % len(rest)
            selected.update((rest + rest)[offset : offset + slots - len(selected)])
            self.detail_cursor += max(1, slots // 2)
        return held, ((selected | requested) - held)

    def detail_analysis(self, symbol, book, is_candidate):
        from .review_events import append_event, archive_candles

        with self.store.connect() as conn:
            archive_candles(
                conn,
                conn.execute(
                    "SELECT * FROM market_candles WHERE symbol=? AND timeframe=60 ORDER BY start DESC LIMIT 2",
                    (symbol,),
                ).fetchall(),
                source="WS_DETAIL_SNAPSHOT",
            )
            append_event(
                conn,
                "BOOK",
                f"{symbol}:{int(time.time()) // 60}",
                symbol,
                datetime.now(UTC).isoformat(),
                {"orderbook": book, "candidate": is_candidate},
            )
        bars = self.book.load_contiguous(symbol, limit=100)
        contiguous = (
            len(bars) >= 25
            and time.time() - (bars[-1]["start"] + 60) <= 120
        )
        ratio = D(0)
        if len(bars) >= 21:
            mean = sum(D(b["volume"]) for b in bars[-21:-1]) / 20
            ratio = D(bars[-1]["volume"]) / mean if mean else D(0)
        policy = self.store.settings()
        _, _, slip = assess(
            book,
            self.analysis_budget or policy["max_entry_krw"],
            policy["execution_slippage_rate"],
        )
        ai = self.model.inference(symbol, bars, True) if is_candidate else None
        return dict(
            contiguous=contiguous,
            volume_ratio=decimal_text(ratio),
            expected_slippage=slip,
            ai=ai,
            at=time.monotonic(),
        )

    def save_early_signals(self, rows):
        at = datetime.now(UTC)
        with self.store.connect() as conn:
            for row in rows:
                sid = hashlib.sha256(
                    f"surge:{row['symbol']}:{int(at.timestamp()) // 60}".encode()
                ).hexdigest()
                payload = dict(
                    id=sid,
                    symbol=row["symbol"],
                    type="EARLY_SURGE",
                    strategy_version="scanner-observation-v1",
                    reference_price=row["price"],
                    reasons=[
                        "전체 KRW 급등 초기 관찰 · 자동 주문 제외",
                        f"5분 거래량 배수 {row['volume_ratio_5m'] or '기준 0·신규 거래'}",
                        f"5분 가격 변화 {D(row['price_change_5m']) * 100:.2f}%",
                    ],
                    generated_at=at.isoformat(),
                    expires_at=(at + timedelta(seconds=30)).isoformat(),
                    data_at=row["data_at"],
                    channel="OBSERVATION",
                    source="bithumb_public",
                )
                inserted = conn.execute(
                    "INSERT OR IGNORE INTO signals VALUES(?,?,?,?,?,?,?)",
                    (
                        sid,
                        row["symbol"],
                        "EARLY_SURGE",
                        "scanner-observation-v1",
                        json.dumps(payload),
                        payload["generated_at"],
                        payload["expires_at"],
                    ),
                )

                if inserted.rowcount:
                    from .review_events import capture_signal

                    capture_signal(
                        conn,
                        self,
                        payload,
                        self.policy(),
                        {
                            "basic_guard": self.basic_guard(row["symbol"]),
                            "observation_only": True,
                        },
                    )

    async def analysis_loop(self):
        observations = {}
        signaled = {}
        while True:
            wake = time.monotonic()
            self.overloaded = (
                self.trade_queue.qsize() > 3000
                or self.metrics["event_loop_lag_ms"] > 100
                or (self.metrics.get("signal_compute_ms") or 0) > 300
            )
            self.scan_basic()
            minute = int(time.time()) // 60
            early = [
                r
                for r in self.rows
                if r["candidate"]
                and self.basic_guard(r["symbol"])[0]
                and signaled.get(r["symbol"]) != minute
            ]
            if early:
                await asyncio.to_thread(self.save_early_signals, early)
                for row in early:
                    signaled[row["symbol"]] = minute
            interval = 10 if self.overloaded else 2
            if wake - self.last_detail_analysis >= interval:
                self.last_detail_analysis = wake
                started = time.perf_counter()
                flags = {r["symbol"]: r["candidate"] for r in self.rows}
                batch = []
                # Held positions are never demoted by detail load shedding.
                ordered = sorted(
                    self.detail_targets, key=lambda s: (s not in self.positions, s)
                )
                for symbol in ordered:
                    book = self.quote(symbol)
                    if book is None:
                        continue
                    result = await asyncio.to_thread(
                        self.detail_analysis,
                        symbol,
                        book,
                        flags.get(symbol, False) and self.basic_guard(symbol)[0],
                    )
                    self.detail_cache[symbol] = result
                    self.metrics["detail_checks"] += 1
                    minute = int(time.time()) // 60
                    if flags.get(symbol) and observations.get(symbol) != minute:
                        observations[symbol] = minute
                        batch.append(
                            (
                                symbol,
                                datetime.now(UTC).isoformat(),
                                json.dumps(self.rolling.measure(symbol, time.time())),
                                json.dumps(
                                    {k: v for k, v in result.items() if k != "at"}
                                ),
                            )
                        )
                if batch:
                    await asyncio.to_thread(self.save_observations, batch)
                self.metrics["detail_ms"] = (time.perf_counter() - started) * 1000
            await asyncio.sleep(1)
            self.metrics["event_loop_lag_ms"] = max(
                0, (time.monotonic() - wake - 1) * 1000 - self.metrics["detail_ms"]
            )

    def save_observations(self, batch):
        with self.store.connect() as conn:
            conn.executemany(
                "INSERT INTO scanner_observations(symbol,created_at,features,analysis) VALUES(?,?,?,?)",
                batch,
            )
            conn.execute(
                "DELETE FROM scanner_observations WHERE id NOT IN(SELECT id FROM scanner_observations ORDER BY id DESC LIMIT 50000)"
            )

    async def model_loop(self):
        while True:
            if self.model.model is None:
                self.model.training = True
                try:
                    version, model, metrics = await asyncio.to_thread(
                        self.model.train_local
                    )
                    self.model.id, self.model.model, self.model.metrics = (
                        version,
                        model,
                        metrics,
                    )
                    self.model.last_error = None
                except Exception:
                    self.model.last_error = "학습 표본·시간 분리 검증 데이터 준비 중"
                finally:
                    self.model.training = False
            await asyncio.sleep(300)

    def mark_gap(self, symbol):
        self.dirty.add(symbol)
        self.loss_minute[symbol] = int(time.time()) // 60 * 60
        self.coverage[symbol] = time.time()
        self.gaps.add(symbol)

    async def seed_retry_loop(self):
        while True:
            for symbol, (at, priority) in list(self.seed_retry.items()):
                if at <= time.monotonic():
                    self.seed_retry.pop(symbol, None)
                    self.seed_later(symbol, priority)
            await asyncio.sleep(1)

    async def writer_loop(self):
        while True:
            item = await self.trade_queue.get()
            batch = [item]
            for _ in range(min(199, self.trade_queue.qsize())):
                batch.append(self.trade_queue.get_nowait())
            try:
                gaps = list(self.gaps)
                self.gaps.difference_update(gaps)
                for symbol in gaps:
                    await asyncio.to_thread(
                        self.book.gap,
                        symbol,
                        self.loss_minute.get(symbol, int(time.time()) // 60 * 60),
                        "체결 누락·과부하",
                    )
                repairs = await asyncio.to_thread(
                    self.book.tick_batch, [(e, c, t) for e, c, t, _ in batch]
                )
                for symbol in repairs:
                    self.dirty.add(symbol)
                    self.seed_later(symbol)
            except Exception:
                self.metrics["batch_errors"] += 1
                for e, _, _, _ in batch:
                    self.mark_gap(e["symbol"])
                    self.seed_later(e["symbol"])
            finally:
                for e, _, _, mono in batch:
                    self.trade_queue.task_done()
                    pending = self.pending_times.get(e["symbol"])
                    if pending and pending[0] == mono:
                        pending.popleft()
                    if pending:
                        self.trade_waiting[e["symbol"]] = pending[0]
                    else:
                        self.trade_waiting.pop(e["symbol"], None)
                        self.pending_times.pop(e["symbol"], None)

    async def candle_clock(self):
        while True:
            await asyncio.to_thread(self.book.finalize_all, time.time())
            await asyncio.sleep(1)

    async def details_loop(self):
        while True:
            if time.monotonic() - self.detail_last_switch >= (
                10 if self.overloaded else 2
            ):
                self.detail_last_switch = time.monotonic()
                held, selected = self.detail_selection()
                self.detail_targets = held | selected
                await self.reconcile_shards(
                    self.books, {"held": held, "candidate": selected}, ("orderbook",)
                )
                for symbol in self.detail_targets:
                    if symbol not in self.seeds or symbol in self.dirty:
                        self.seed_later(symbol, 0 if symbol in held else 1)
            await asyncio.sleep(1)

    async def reconcile_shards(self, registry, groups, types):
        desired = {}
        for label, symbols in groups.items():
            ordered = sorted(symbols)
            for i in range(0, len(ordered), self.shard_size):
                codes = tuple(ordered[i : i + self.shard_size])
                desired[(label, codes)] = codes
        retired = [key for key in registry if key not in desired]
        if types == ("orderbook",):
            protected = (self.positions - self.unavailable) & {
                s for key in retired for s in key[1]
            }
            counts = {s: len(self.detail_sources.get(s, set())) for s in protected}
            for key, codes in desired.items():
                if key not in registry or registry[key].done():
                    registry[key] = asyncio.create_task(self.shard(list(codes), types))
            until = time.monotonic() + 3
            while (
                protected
                and any(
                    len(self.detail_sources.get(s, set())) <= counts[s]
                    for s in protected
                )
                and time.monotonic() < until
            ):
                await asyncio.sleep(0.1)
            waiting = {
                s
                for s in protected
                if len(self.detail_sources.get(s, set())) <= counts[s]
            }
            retired = [key for key in retired if not waiting.intersection(key[1])]
        for key in retired:
            registry[key].cancel()
        await asyncio.gather(*(registry[k] for k in retired), return_exceptions=True)
        for key in retired:
            del registry[key]
        for key, codes in desired.items():
            if key not in registry or registry[key].done():
                registry[key] = asyncio.create_task(self.shard(list(codes), types))

    async def update_universe(self, markets):
        if not isinstance(markets, list):
            raise ValueError("invalid market universe")
        universe = sorted(
            {
                m["market"]
                for m in markets
                if isinstance(m, dict)
                and isinstance(m.get("market"), str)
                and re.fullmatch(r"KRW-[A-Z0-9]+", m["market"])
            }
        )
        if not universe:
            raise ValueError("empty universe")
        previous = set(self.universe)
        self.unavailable.update(previous - set(universe))
        self.unavailable.difference_update(universe)
        self.universe = universe
        self.market_metadata = {
            m["market"]: m
            for m in markets
            if isinstance(m, dict) and m.get("market") in set(universe)
        }
        self.last_universe_refresh = datetime.now(UTC).isoformat()
        for symbol in universe:
            if symbol not in previous:
                self.seed_later(symbol, 2)
        await self.reconcile_shards(
            self.feeds, {"all": set(universe)}, ("trade", "ticker")
        )

    def receive_event(self, event, received, session, codes):
        symbol = event["symbol"]
        if symbol not in codes:
            raise InvalidMessage("unsubscribed symbol")
        kind = event["event_type"].split(".")[1]
        streams = self.latest.setdefault(symbol, {})
        old = streams.get(kind)
        event["_received_mono"] = time.monotonic()
        event["_exchange_lag_ms"] = (
            received
            - datetime.fromisoformat(event["exchange_at"].replace("Z", "+00:00"))
        ).total_seconds() * 1000
        self.received_at[symbol] = time.time()
        self.last_received = received.isoformat()
        valid = -1000 <= event["_exchange_lag_ms"] <= 15000
        if kind == "trade":
            clean = {k: v for k, v in event.items() if not k.startswith("_")}
            if not self.rolling.tick(clean):
                self.metrics["duplicates"] = self.rolling.duplicates
                return
        if (
            kind == "trade"
            and old
            and event["exchange_at"] >= old["exchange_at"]
            and valid
            and old.get("price")
            and abs(D(event["price"]) / D(old["price"]) - 1) > D(".2")
        ):
            self.invalid_until[symbol] = time.monotonic() + 30
            self.dirty.add(symbol)
            self.seed_later(symbol)
        if not old or event["exchange_at"] >= old["exchange_at"]:
            streams[kind] = event
        self.metrics["events"] += 1
        if kind != "trade":
            return
        clean = {k: v for k, v in event.items() if not k.startswith("_")}
        if self.batch_enabled:
            mono = time.monotonic()
            try:
                self.trade_queue.put_nowait(
                    (clean, self.coverage.get(symbol, time.time()), time.time(), mono)
                )
                self.pending_times.setdefault(symbol, deque()).append(mono)
                self.trade_waiting.setdefault(symbol, mono)
            except asyncio.QueueFull:
                self.metrics["queue_drops"] += 1
                self.mark_gap(symbol)
                self.seed_later(symbol)
        else:
            if self.book.tick(
                clean, self.coverage.get(symbol, time.time()), time.time()
            ):
                self.dirty.add(symbol)
                self.seed_later(symbol)

    async def shard(self, codes, types=("trade", "ticker")):
        attempt = 0
        while True:
            session = str(uuid4())
            try:
                async with self.ws_lock:
                    await asyncio.sleep(max(0, self.ws_next - time.monotonic()))
                    self.ws_next = time.monotonic() + 1
                async with connect(
                    WS,
                    open_timeout=10,
                    close_timeout=5,
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=64,
                    max_size=2**20,
                ) as ws:
                    for symbol in codes:
                        if "trade" in types:
                            self.connections[symbol] = True
                            self.coverage[symbol] = time.time()
                            self.rolling.connected(symbol, time.time())
                            for kind in types:
                                self.latest.setdefault(symbol, {}).pop(kind, None)
                            if symbol in self.seeds:
                                self.dirty.add(symbol)
                                self.seed_later(symbol)
                        if "orderbook" in types:
                            self.detail_sources.setdefault(symbol, set()).add(session)
                    await ws.send(
                        json.dumps(
                            [
                                {"ticket": session},
                                *[{"type": kind, "codes": codes} for kind in types],
                                {"format": "DEFAULT"},
                            ]
                        )
                    )
                    connected = time.time()
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), self.receive_timeout)
                        received = datetime.now(UTC)
                        started = time.perf_counter()
                        try:
                            event = normalize(raw, received, session)
                        except SubscriptionError:
                            raise
                        except InvalidMessage:
                            self.metrics["invalid"] += 1
                            try:
                                bad = json.loads(raw).get("code")
                            except Exception:
                                bad = None
                            for symbol in [bad] if bad in codes else codes:
                                self.invalid_until[symbol] = time.monotonic() + 30
                                if "trade" in types:
                                    self.dirty.add(symbol)
                                    self.seed_later(symbol)
                            continue
                        if event is not None:
                            self.receive_event(event, received, session, codes)
                        self.metrics["processing_ms"] = (
                            time.perf_counter() - started
                        ) * 1000
                        if time.time() - connected > 60:
                            attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.error = f"WebSocket 복구: {type(error).__name__}"
                self.metrics["reconnects"] += 1
                if isinstance(error, InvalidStatus) and error.response.status_code in (
                    418,
                    429,
                ):
                    self.ws_next = max(self.ws_next, time.monotonic() + 600)
            finally:
                for symbol in codes:
                    if "trade" in types:
                        self.connections[symbol] = False
                        self.mark_gap(symbol)
                        await asyncio.to_thread(
                            self.book.gap,
                            symbol,
                            self.received_at.get(symbol, time.time()) - 30,
                            "WebSocket 재연결/종료",
                        )
                    if "orderbook" in types:
                        self.detail_sources.setdefault(symbol, set()).discard(session)
            attempt += 1
            await asyncio.sleep(min(60, 2 ** min(attempt - 1, 6)) + random.random())

    def status(self):
        events = [e for streams in self.latest.values() for e in streams.values()]
        lags = [
            e["_exchange_lag_ms"]
            for e in events
            if e.get("stream_type") == "REALTIME"
            and time.monotonic() - e["_received_mono"] < 15
        ]
        return dict(
            source="bithumb_public" if self.enabled else "OFFLINE",
            connected_markets=sum(bool(self.connections.get(s)) for s in self.universe),
            market_count=len(self.universe),
            last_received_at=self.last_received,
            new_buy_signals_enabled=False,
            seeded_markets=len(self.seeds),
            recovery_pending=len(self.pending),
            clock_skew_detected=any(x < -1000 for x in lags),
            lag_ms_min=min(lags) if lags else None,
            lag_ms_max=max(lags) if lags else None,
            error=self.error,
            metrics=dict(self.metrics),
            scanner=dict(
                scope="ALL_KRW",
                turnover_floor=None,
                analysis_budget_krw=self.analysis_budget
                or self.policy()["max_entry_krw"],
                watch_count=len(self.rows),
                fresh_count=sum(r["fresh"] for r in self.rows),
                candidate_count=sum(r["candidate"] for r in self.rows),
                detail_count=len(self.detail_targets),
                order_ready_count=sum(r["ready"] for r in self.rows),
                held_count=len(self.real_positions),
                paper_held_count=len(self.paper_positions),
                overloaded=self.overloaded,
                detail_interval_seconds=10 if self.overloaded else 2,
                trade_queue_size=self.trade_queue.qsize(),
                universe_refreshed_at=self.last_universe_refresh,
                model=self.model.status(),
            ),
        )

    async def supervise(self, label, worker):
        while True:
            try:
                await worker()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.error = f"{label} 복구: {type(error).__name__}"
                self.metrics["worker_errors"] = self.metrics.get("worker_errors", 0) + 1
                await asyncio.sleep(1)

    async def run(self):
        self.batch_enabled = True
        self.tasks = [
            asyncio.create_task(self.supervise(f.__name__, f))
            for f in (
                self.seed_worker,
                self.writer_loop,
                self.candle_clock,
                self.analysis_loop,
                self.details_loop,
                self.model_loop,
                self.seed_retry_loop,
            )
        ]
        try:
            while True:
                try:
                    await self.update_universe(
                        await self.request("/v1/market/all", {"isDetails": "true"})
                    )
                    await asyncio.sleep(self.universe_refresh_seconds)
                except Exception as error:
                    self.error = f"종목 조회: {type(error).__name__}"
                    await asyncio.sleep(10)
        finally:
            all_tasks = [*self.tasks, *self.feeds.values(), *self.books.values()]
            for task in all_tasks:
                task.cancel()
            await asyncio.gather(*all_tasks, return_exceptions=True)
            self.batch_enabled = False

    @contextlib.asynccontextmanager
    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.loop_thread = threading.get_ident()
        self.http = httpx.AsyncClient(
            timeout=12, follow_redirects=False, trust_env=False
        )
        task = asyncio.create_task(self.run()) if self.enabled else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await self.http.aclose()
            self.http = None
