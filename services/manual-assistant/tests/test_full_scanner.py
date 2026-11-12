import asyncio
import contextlib
import math
import time
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from manual_assistant.app import Settings, create_app
from manual_assistant.candles import Candles
from manual_assistant.market import Market
from manual_assistant.scanner import RollingScanner
from manual_assistant.scanner_model import fit, predict
from manual_assistant.storage import Storage

D = Decimal
BASE = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())


@pytest.fixture
def store(tmp_path):
    db = Storage(tmp_path / "scan.sqlite3")
    db.initialize(Settings().model_dump())
    return db


def iso(at):
    return datetime.fromtimestamp(at, UTC).isoformat()


def trade(symbol, at, number, price="23.34", qty="10"):
    return dict(
        symbol=symbol,
        event_id=f"{symbol}-{number}",
        event_type="market.trade",
        exchange_at=iso(at),
        received_at=iso(at),
        price=price,
        quantity=qty,
        side="BUY",
        stream_type="REALTIME",
        source="bithumb_public",
    )


def streams(market, symbol, price="23.34", turnover="100"):
    base = {
        "_received_mono": time.monotonic(),
        "_exchange_lag_ms": 10,
        "exchange_at": iso(time.time()),
        "stream_type": "REALTIME",
    }
    market.connections[symbol] = True
    market.latest[symbol] = {
        "trade": {**base, "price": price},
        "ticker": {
            **base,
            "price": price,
            "turnover_24h": turnover,
            "market_state": "ACTIVE",
            "is_trading_suspended": False,
            "market_warning": "NONE",
        },
        "orderbook": {
            **base,
            "bids": [["23.33", "100000"]],
            "asks": [["23.34", "100000"]],
        },
    }


def burst(market, symbol, minute):
    for i in range(5):
        market.rolling.tick(
            trade(symbol, minute - 540 + i * 60, f"baseline-{i}", "23.00", "1")
        )
    for i in range(5):
        market.rolling.tick(
            trade(
                symbol,
                minute - 240 + i * 60,
                f"surge-{i}",
                str(D("23.00") + D(i) * D(".085")),
                "10",
            )
        )


def test_entire_universe_has_no_top20_or_turnover_floor(store):
    m = Market(store)
    minute = int(time.time()) // 60 * 60
    m.universe = [f"KRW-LOW{i:03}" for i in range(257)]
    for symbol in m.universe:
        streams(m, symbol, turnover="100")
        burst(m, symbol, minute)
    rows = m.scan_basic()
    assert len(rows) == 257 and all(r["candidate"] for r in rows)
    assert rows[0]["price"] == "23.34"
    assert D(rows[0]["volume_ratio_5m"]) == 10
    assert D(rows[0]["turnover_ratio_5m"]) > 10
    assert all(D(r["score"]) > 0 for r in rows)
    assert all(not r["ready"] for r in rows)  # Monitoring is not order permission.
    m.latest["KRW-LOW000"]["ticker"]["turnover_24h"] = "999999999999"
    assert (
        m.scan_basic()[0]["symbol"] == "KRW-LOW000"
    )  # Ties use symbol, not 24h turnover.


def test_dynamic_universe_and_sharding_have_no_fixed_coin_count(store):
    async def check():
        m = Market(store)
        with patch.object(m, "reconcile_shards", new_callable=AsyncMock) as reconcile:
            first = [{"market": f"KRW-C{i:03}"} for i in range(253)] + [
                {"market": "BTC-NOTKRW"}
            ]
            await m.update_universe(first)
            assert len(m.universe) == 253
            assert reconcile.call_args.args[2] == ("trade", "ticker")
            await m.update_universe(first[1:] + [{"market": "KRW-NEW"}])
            assert "KRW-NEW" in m.universe and "KRW-C000" in m.unavailable
            streams(m, "KRW-C000")
            assert m.quote("KRW-C000") is None
        called = []

        async def fake(codes, types):
            called.append((codes, types))
            await asyncio.Event().wait()

        registry = {}
        with patch.object(m, "shard", fake):
            await m.reconcile_shards(
                registry, {"all": set(m.universe)}, ("trade", "ticker")
            )
            await asyncio.sleep(0)
            assert sorted(len(c) for c, _ in called) == [53, 100, 100]
            assert len({s for c, _ in called for s in c}) == 253
            for task in registry.values():
                task.cancel()
            await asyncio.gather(*registry.values(), return_exceptions=True)

    asyncio.run(check())


def test_candidate_detail_rotation_is_fair_and_held_monitor_is_not_shed(store):
    m = Market(store)
    m.rows = [{"symbol": f"KRW-C{i:03}", "candidate": True} for i in range(257)]
    m.positions = {"KRW-HELD"}
    covered = set()
    for _ in range(12):
        held, selected = m.detail_selection()
        assert held == {"KRW-HELD"} and len(selected) <= 64
        covered.update(selected)
    assert len(covered) == 257
    m.overloaded = True
    held, selected = m.detail_selection()
    assert held == {"KRW-HELD"} and len(selected) <= 16


def test_held_orderbook_survives_move_out_of_candidate_group(store):
    async def check():
        m = Market(store)
        m.positions = {"KRW-HELD"}
        counter = 0
        gaps = []

        async def fake(codes, types):
            nonlocal counter
            counter += 1
            key = str(counter)
            for s in codes:
                m.detail_sources.setdefault(s, set()).add(key)
            try:
                await asyncio.Event().wait()
            finally:
                for s in codes:
                    m.detail_sources[s].discard(key)
                    if s == "KRW-HELD" and not m.detail_sources[s]:
                        gaps.append(s)

        with patch.object(m, "shard", fake):
            old = asyncio.create_task(fake(["KRW-HELD", "KRW-OLD"], ("orderbook",)))
            m.books[("candidate", ("KRW-HELD", "KRW-OLD"))] = old
            await asyncio.sleep(0)
            await m.reconcile_shards(
                m.books,
                {"held": {"KRW-HELD"}, "candidate": {"KRW-NEW"}},
                ("orderbook",),
            )
            assert gaps == [] and m.detail_sources["KRW-HELD"]
            for task in m.books.values():
                task.cancel()
            await asyncio.gather(*m.books.values(), return_exceptions=True)

    asyncio.run(check())


def test_all_markets_generate_1_3_5_minute_candles_without_being_candidates(store):
    book = Candles(store)
    events = []
    for n in range(31):
        symbol = f"KRW-TINY{n}"
        for minute in range(30):
            for tick in range(2):
                at = BASE + minute * 60 + tick * 20
                events.append(
                    (
                        trade(
                            symbol,
                            at,
                            f"{minute}-{tick}",
                            str(D("23.34") + D(tick) / 100),
                        ),
                        BASE,
                        at,
                    )
                )
    assert book.tick_batch(events) == set()
    book.finalize_all(BASE + 30 * 60 + 41)
    for n in range(31):
        symbol = f"KRW-TINY{n}"
        assert len(book.load(symbol, 60)) == 30
        assert len(book.load(symbol, 180)) == 10
        assert len(book.load(symbol, 300)) == 6
        assert all(b["complete"] for b in book.load(symbol, 300))


def test_gap_does_not_become_complete_again_on_next_trade(store):
    book = Candles(store)
    book.tick(trade("KRW-SMALL", BASE + 1, "one"), BASE, BASE + 1)
    book.gap("KRW-SMALL", BASE, "lost queue")
    book.tick(trade("KRW-SMALL", BASE + 2, "two"), BASE, BASE + 2)
    book.finalize_all(BASE + 101)
    assert book.load("KRW-SMALL") == []
    assert not book.load("KRW-SMALL", complete=False)[0]["complete"]


def test_contiguous_loader_ignores_only_gaps_before_the_current_run(store):
    book = Candles(store)
    symbol = "KRW-TAIL"
    with store.connect() as conn:
        for start in [BASE, BASE + 120, BASE + 180, BASE + 240]:
            conn.execute(
                "INSERT INTO market_candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    symbol,
                    60,
                    start,
                    start + 60,
                    "100",
                    "101",
                    "99",
                    "100",
                    "1",
                    "100",
                    "TEST",
                    "TEST",
                    1,
                    1,
                    "TEST",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
    assert [b["start"] for b in book.load_contiguous(symbol)] == [
        BASE + 120,
        BASE + 180,
        BASE + 240,
    ]


def test_zero_baseline_is_not_an_infinite_or_fake_volume_percentage():
    scan = RollingScanner()
    scan.connected("KRW-ZERO", BASE - 600)
    for i in range(3):
        scan.tick(trade("KRW-ZERO", BASE + i, f"z-{i}", str(D("23.30") + D(i) / 100)))
    result = scan.measure("KRW-ZERO", BASE + 10)
    assert (
        result["volume_ratio_5m"] is None
        and result["first_activity"]
        and result["candidate"]
    )
    assert result["score"] != "Infinity"
    duplicate = trade("KRW-ZERO", BASE + 2, "z-2", "23.32")
    assert not scan.tick(duplicate) and scan.duplicates == 1
    assert scan.measure("KRW-ZERO", BASE + 10)["volume_5m"] == "30"


@pytest.mark.parametrize(
    "failure",
    [
        "thin",
        "spread",
        "slippage",
        "stale",
        "suspended",
        "warning",
        "missing",
        "delisted",
        "anomaly",
        "queue",
        "metadata-warning",
        "metadata-closed",
    ],
)
def test_small_coin_buy_eligibility_is_separate_from_monitoring(store, failure):
    m = Market(store)
    symbol = "KRW-SMALL"
    streams(m, symbol)
    m.seeds[symbol] = BASE
    assert m.safe(symbol)[0]
    if failure == "thin":
        m.latest[symbol]["orderbook"]["asks"] = [["23.34", "1"]]
    if failure == "spread":
        m.latest[symbol]["orderbook"]["asks"] = [["24", "100000"]]
    if failure == "slippage":
        m.latest[symbol]["orderbook"]["asks"] = [["23.34", "1"], ["23.39", "100000"]]
    if failure == "stale":
        m.latest[symbol]["trade"]["_received_mono"] -= 16
    if failure == "suspended":
        m.latest[symbol]["ticker"]["is_trading_suspended"] = True
    if failure == "warning":
        m.latest[symbol]["ticker"]["market_warning"] = "CAUTION"
    if failure == "missing":
        m.dirty.add(symbol)
    if failure == "delisted":
        m.unavailable.add(symbol)
    if failure == "anomaly":
        m.invalid_until[symbol] = time.monotonic() + 30
    if failure == "queue":
        m.trade_waiting[symbol] = time.monotonic() - 3
    if failure == "metadata-warning":
        m.market_metadata[symbol] = {"market_warning": "CAUTION"}
    if failure == "metadata-closed":
        m.market_metadata[symbol] = {"market_state": "CLOSED"}
        assert m.quote(symbol) is None
    assert not m.safe(symbol)[0]
    assert m.connections[symbol]  # Still monitored, not silently removed.


def test_queue_overflow_marks_gap_and_oldest_wait_is_not_refreshed_by_new_ticks(store):
    m = Market(store)
    m.batch_enabled = True
    m.trade_queue = asyncio.Queue(maxsize=1)
    now = time.time()
    m.coverage["KRW-Q"] = now - 60
    first = trade("KRW-Q", now, "first")
    m.receive_event(first, datetime.now(UTC), "fake", ["KRW-Q"])
    oldest = m.trade_waiting["KRW-Q"]
    m.receive_event(
        trade("KRW-Q", now + 0.001, "second"), datetime.now(UTC), "fake", ["KRW-Q"]
    )
    assert (
        m.metrics["queue_drops"] == 1
        and "KRW-Q" in m.dirty
        and "KRW-Q" in m.loss_minute
    )
    assert m.trade_waiting["KRW-Q"] == oldest


def test_local_model_time_split_and_validation_do_not_leak_into_training():
    samples = [
        {
            "symbol": "KRW-A",
            "at": BASE + i * 60,
            "label_end": BASE + (i + 3) * 60,
            "x": [math.log1p(i % 5), (i % 3) / 3, (i % 7) / 10, (i % 11) / 10],
            "y": int(i % 4 == 0),
        }
        for i in range(350)
    ]
    model, metrics = fit(samples)
    assert metrics["train_label_end"] < metrics["validation_start"]
    changed = [
        dict(s, y=1 - s["y"]) if s["at"] >= metrics["validation_start"] else s
        for s in samples
    ]
    model2, _ = fit(changed)
    assert model == model2
    assert 0 < predict(model, [1, 1, 1, 1]) < 1


def test_inference_is_candidate_only_and_never_claims_live_strategy_permission(store):
    m = Market(store)
    model, metrics = fit(
        [
            {
                "symbol": "KRW-A",
                "at": BASE + i * 60,
                "label_end": BASE + (i + 3) * 60,
                "x": [i % 2, 1, 2, 3],
                "y": i % 2,
            }
            for i in range(350)
        ]
    )
    m.model.model = model
    m.model.metrics = metrics
    bars = [
        dict(
            start=BASE + i * 60,
            open="23.34",
            high="23.35",
            low="23.33",
            close="23.35",
            volume="10",
            turnover="233.5",
            complete=True,
        )
        for i in range(30)
    ]
    with pytest.raises(ValueError):
        m.model.inference("KRW-A", bars, False)
    assert m.model.inferences == 0
    result = m.model.inference("KRW-A", bars, True)
    assert result["advisory_only"] and m.model.inferences == 1
    assert m.model.status()["affects_live_strategy"] is False


def test_early_observation_signals_are_not_auto_order_signals(store):
    m = Market(store)
    symbol = "KRW-LOW"
    streams(m, symbol)
    burst(m, symbol, int(time.time()) // 60 * 60)
    row = m.scan_basic()[0]
    m.save_early_signals([row])
    with store.connect() as conn:
        signal = conn.execute("SELECT * FROM signals").fetchone()
        assert (
            signal["type"] == "EARLY_SURGE"
            and signal["strategy_version"] == "scanner-observation-v1"
        )
        assert conn.execute("SELECT count(*) FROM manual_orders").fetchone()[0] == 0


def test_full_scanner_api_pages_all_markets_and_reports_watch_vs_order_ready(tmp_path):
    with TestClient(
        create_app(tmp_path / "api.sqlite3", market_enabled=False),
        base_url="http://127.0.0.1",
    ) as api:
        m = api.app.state.market
        m.universe = [f"KRW-ALL{i:03}" for i in range(203)]
        m.scan_basic()
        first = api.get("/api/market/scanner").json()
        assert (
            first["total"] == 203 and len(first["items"]) == 20 and first["pages"] == 11
        )
        assert (
            first["status"]["scope"] == "ALL_KRW"
            and first["status"]["order_ready_count"] == 0
        )
        assert first["status"]["watch_count"] == 203
        assert (
            api.get("/api/market/scanner?page=11").json()["items"][0]["symbol"]
            == "KRW-ALL200"
        )
        assert api.get("/api/market/scanner?page_size=101").status_code == 422


def test_cold_start_missing_baseline_is_not_reported_as_zero_volume_surge():
    scan = RollingScanner()
    scan.connected("KRW-COLD", BASE + 1)
    for i in range(3):
        scan.tick(
            trade("KRW-COLD", BASE + 2 + i, f"cold-{i}", str(D("23.30") + D(i) / 100))
        )
    row = scan.measure("KRW-COLD", BASE + 10)
    assert (
        not row["baseline_ready"] and not row["first_activity"] and not row["candidate"]
    )


def test_liquidity_uses_configured_order_budget_not_a_24h_money_floor(store):
    m = Market(store)
    streams(m, "KRW-LOW")
    m.seeds["KRW-LOW"] = BASE
    m.latest["KRW-LOW"]["orderbook"]["asks"] = [["23.34", "1000"]]
    m.latest["KRW-LOW"]["orderbook"]["bids"] = [["23.33", "1000"]]
    assert not m.safe("KRW-LOW")[0]  # Default maximum order 50,000 KRW.
    m.set_analysis_budget("10000")
    assert m.safe("KRW-LOW")[0]  # Actual planned order 10,000 KRW fits the same coin.
    m.set_analysis_budget(None)
    assert not m.safe("KRW-LOW")[0]


def test_model_trains_from_local_closed_candles_and_persists_version(store):
    from manual_assistant.scanner_model import ScannerModel

    book = Candles(store)
    for symbol in ("KRW-A", "KRW-B"):
        raw = []
        for i in range(220):
            close = D("23.34") + D(i % 10) / 10
            raw.append(
                dict(
                    candle_date_time_utc=iso(BASE + i * 60)[:19],
                    opening_price=str(close - D(".01")),
                    high_price=str(close + D(".01")),
                    low_price=str(close - D(".02")),
                    trade_price=str(close),
                    candle_acc_trade_volume=str(10 + i % 3),
                    candle_acc_trade_price=str(close * (10 + i % 3)),
                )
            )
        book.seed(symbol, raw, BASE + 230 * 60)
    model = ScannerModel(store)
    version, parameters, metrics = model.train_local()
    assert metrics["train_samples"] >= 100 and metrics["validation_samples"] >= 40
    assert metrics["train_label_end"] < metrics["validation_start"]
    reloaded = ScannerModel(store)
    assert reloaded.id == version and reloaded.model == parameters
    assert reloaded.status()["affects_live_strategy"] is False


def test_batched_full_market_ingest_is_bounded_and_does_not_need_orderbooks(store):
    async def check():
        m = Market(store)
        m.batch_enabled = True
        count = 5000
        now = time.time()
        for i in range(count):
            symbol = f"KRW-S{i % 513:03}"
            m.coverage[symbol] = now - 60
            m.receive_event(
                trade(symbol, now, f"load-{i}"), datetime.now(UTC), "test", [symbol]
            )
        assert m.trade_queue.qsize() == count and m.model.inferences == 0
        worker = asyncio.create_task(m.writer_loop())
        started = time.perf_counter()
        try:
            await asyncio.wait_for(m.trade_queue.join(), 20)
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        assert time.perf_counter() - started < 20
        assert m.trade_waiting == {} and m.metrics["queue_drops"] == 0
        with store.connect() as conn:
            assert (
                conn.execute("SELECT count(*) FROM market_events").fetchone()[0]
                == count
            )
        assert all("orderbook" not in value for value in m.latest.values())

    asyncio.run(check())
