import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from manual_assistant.app import Settings, create_app
from manual_assistant.backtest import run, validate_bars
from manual_assistant.candles import Candles
from manual_assistant.lab import Lab
from manual_assistant.ledger import rebuild
from manual_assistant.market import Market
from manual_assistant.storage import Storage

D = Decimal
BASE = int(datetime(2026, 11, 10, tzinfo=UTC).timestamp())
HEADERS = {"origin": "http://127.0.0.1:13000", "x-bsb-local": "1"}
PARAMS = {
    "lookback": 5,
    "volume_ratio": "1",
    "stop_loss": "0.008",
    "take_profit": "0.012",
    "max_bars": 15,
}


def iso(at):
    return datetime.fromtimestamp(at, UTC).isoformat()


def bars(count=160):
    return [
        {
            "start": BASE + i * 60,
            "open": "100",
            "high": "102",
            "low": "98",
            "close": "101",
            "volume": "100",
            "closed": True,
            "complete": True,
        }
        for i in range(count)
    ]


def fill(identifier, side, qty, price, fee="0"):
    return {
        "id": str(identifier),
        "symbol": "KRW-BTC",
        "side": side,
        "quantity": qty,
        "price": price,
        "fee_krw": fee,
        "filled_at": iso(BASE + identifier),
        "recorded_at": iso(BASE + identifier),
        "notes": "",
    }


@pytest.fixture
def store(tmp_path):
    instance = Storage(tmp_path / "test.sqlite3")
    instance.initialize(Settings().model_dump())
    return instance


def test_partial_sell_exact_fees_and_no_capital_increase():
    result = rebuild(
        [fill(1, "BUY", "2", "100", "2"), fill(2, "SELL", "0.5", "110", "1")],
        marks={"KRW-BTC": "105"},
    )
    assert result["cash_krw"] == "149852.0"
    assert result["realized_net_krw"] == "3.50"
    assert result["positions"][0]["quantity"] == "1.5"
    assert D(result["positions"][0]["cost_basis_krw"]) == D("151.5")
    assert D(result["positions"][0]["average_entry_price"]) == D("100")
    assert result["closed_campaign_pnls"] == []
    result = rebuild([fill(1, "BUY", "1", "100"), fill(2, "SELL", "1", "90")])
    assert D(result["cash_krw"]) == D("149990")
    assert result["consecutive_losses"] == 1
    assert Settings().max_entry_krw == "50000"


def test_oversell_and_float_money_are_rejected():
    with pytest.raises(ValueError):
        rebuild([fill(1, "SELL", "1", "100")])
    with pytest.raises(ValueError):
        rebuild([fill(1, "BUY", 0.1, "100")])


def test_actual_fills_idempotency_and_atomic_oversell(tmp_path):
    with TestClient(
        create_app(tmp_path / "api.sqlite3", market_enabled=False),
        base_url="http://127.0.0.1",
    ) as api:
        data = {
            k: v
            for k, v in fill(1, "BUY", "2", "100", "2").items()
            if k != "recorded_at"
        }
        data["confirmed"] = True
        assert (
            api.post("/api/manual-fills", json=data, headers=HEADERS).status_code == 200
        )
        assert (
            api.post("/api/manual-fills", json=data, headers=HEADERS).json()[
                "duplicate"
            ]
            is True
        )
        data.update(id="2", side="SELL", quantity="3", filled_at=iso(BASE + 2))
        assert (
            api.post("/api/manual-fills", json=data, headers=HEADERS).status_code == 422
        )
        assert api.get("/api/positions").json()["items"][0]["quantity"] == "2"
        assert len(api.get("/api/trades").json()["items"]) == 1
        assert not api.get("/api/risk/status").json()["buy_signals_enabled"]
    with TestClient(
        create_app(tmp_path / "api.sqlite3", market_enabled=False),
        base_url="http://127.0.0.1",
    ) as api:
        assert api.get("/api/positions").json()["items"][0]["quantity"] == "2"


def tick(identifier, second, price="100", quantity="1"):
    return {
        "event_id": str(identifier),
        "event_type": "market.trade",
        "symbol": "KRW-BTC",
        "exchange_at": iso(BASE + second),
        "received_at": iso(BASE + second + 0.1),
        "price": price,
        "quantity": quantity,
        "stream_type": "REALTIME",
    }


def test_trade_candles_dedup_and_three_timeframes(store):
    book = Candles(store)
    for i in range(5):
        book.tick(tick(i, i * 60 + 1, str(100 + i)), BASE - 1, BASE + 400)
    book.tick(tick(0, 1, "100"), BASE - 1, BASE + 400)
    book.finalize("KRW-BTC", BASE + 400)
    one = book.load("KRW-BTC")
    three = book.load("KRW-BTC", 180)
    five = book.load("KRW-BTC", 300)
    assert len(one) == 5 and one[0]["volume"] == "1"
    assert len(three) == 1 and three[0]["volume"] == "3"
    assert len(five) == 1 and five[0]["volume"] == "5"
    assert five[0]["open"] == "100" and five[0]["close"] == "104"


def test_partial_or_missing_minutes_do_not_become_complete(store):
    book = Candles(store)
    book.tick(tick(1, 10), BASE + 5, BASE + 300)
    book.tick(tick(2, 130), BASE + 5, BASE + 300)
    book.finalize("KRW-BTC", BASE + 300)
    assert len(book.load("KRW-BTC")) == 1  # first minute began before coverage
    assert book.load("KRW-BTC", 180) == []


def test_rest_absolute_recovery_does_not_double_count_ws(store):
    book = Candles(store)
    book.tick(tick(1, 1), BASE - 1, BASE + 300)
    raw = [
        {
            "candle_date_time_utc": datetime.fromtimestamp(BASE, UTC).strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),
            "opening_price": 100,
            "high_price": 102,
            "low_price": 99,
            "trade_price": 101,
            "candle_acc_trade_volume": D("2E-8"),
            "candle_acc_trade_price": D("0.000002"),
        }
    ]
    book.seed("KRW-BTC", raw, BASE + 300)
    assert book.load("KRW-BTC")[0]["volume"] == "0.00000002"
    book.tick(tick(2, 2), BASE - 1, BASE + 300)
    assert book.load("KRW-BTC")[0]["volume"] == "0.00000002"


def ready_market(store):
    market = Market(store)
    symbol = "KRW-BTC"
    now = time.monotonic()
    base = {
        "stream_type": "REALTIME",
        "_received_mono": now,
        "_exchange_lag_ms": 100,
        "exchange_at": iso(time.time()),
    }
    market.connections[symbol] = True
    market.seeds[symbol] = BASE
    market.latest[symbol] = {
        "trade": dict(base),
        "ticker": {
            **base,
            "market_state": "ACTIVE",
            "is_trading_suspended": False,
            "market_warning": "NONE",
            "price": "100",
            "turnover_24h": "4000000000",
        },
        "orderbook": {**base, "bids": [["100", "1000"]], "asks": [["100.1", "1000"]]},
    }
    return market


@pytest.mark.parametrize(
    "failure",
    ["disconnect", "stale", "skew", "spread", "depth", "recovery", "snapshot"],
)
def test_feed_fail_closed(store, failure):
    market = ready_market(store)
    symbol = "KRW-BTC"
    assert market.safe(symbol)[0]
    if failure == "disconnect":
        market.connections[symbol] = False
    elif failure == "stale":
        market.latest[symbol]["trade"]["_received_mono"] -= 16
    elif failure == "skew":
        market.latest[symbol]["orderbook"]["_exchange_lag_ms"] = -2000
    elif failure == "spread":
        market.latest[symbol]["orderbook"]["asks"][0][0] = "110"
    elif failure == "depth":
        market.latest[symbol]["orderbook"]["asks"][0][1] = "1"
    elif failure == "recovery":
        market.dirty.add(symbol)
    else:
        market.latest[symbol]["trade"]["stream_type"] = "SNAPSHOT"
    assert not market.safe(symbol)[0]


def test_backtest_executes_next_open_and_stop_first_with_costs():
    with patch("manual_assistant.backtest.entry", return_value=(True, [])):
        report = run("breakout", PARAMS, bars(40))
    assert report["trades"][0]["entry_at"] == BASE + 60
    assert report["trades"][0]["reason"] == "stop"
    assert D(report["fees_krw"]) > 0 and D(report["slippage_krw"]) > 0
    assert D(report["final_capital"]) < D("150000")
    assert D(report["mdd"]) > 0


def test_gaps_and_unfinished_backtest_data_are_rejected():
    for data in [
        bars(40)[:10] + bars(40)[11:],
        [{**b, "complete": False} for b in bars(40)],
    ]:
        with pytest.raises(ValueError):
            validate_bars(data)


def test_future_prices_cannot_change_prior_trades():
    original = bars(80)
    altered = bars(80)
    for bar in altered[60:]:
        bar.update(open="900", high="999", low="800", close="950")
    with patch("manual_assistant.backtest.entry", return_value=(True, [])):
        before = run("momentum", PARAMS, original)
        after = run("momentum", PARAMS, altered)
    cutoff = BASE + 60 * 60
    assert [t for t in before["trades"] if t["exit_at"] < cutoff] == [
        t for t in after["trades"] if t["exit_at"] < cutoff
    ]


def test_optimizer_chooses_train_only_and_never_auto_promotes(store):
    lab = Lab(store)
    version = lab.add_version("breakout", PARAMS)
    dataset = lab.dataset("KRW-BTC", bars(160))
    calls = []

    def fake(kind, params, data, fee, slippage, warmup=None):
        calls.append((data[0]["start"], params["volume_ratio"]))
        return {
            "net_profit": params["volume_ratio"],
            "trades_count": 5,
            "mdd": "0.01",
            "fee_rate_assumption": fee,
            "slippage_assumption": slippage,
            "trades": [],
            "equity_curve": [],
        }

    with patch("manual_assistant.lab.run", side_effect=fake):
        report = lab.evaluate(
            version, dataset, "0.0004", "0.001", {"volume_ratio": ["1", "2"]}
        )
    assert calls[:2] == [(BASE, "1"), (BASE, "2")]
    assert all(value == "2" for _, value in calls[2:])
    assert report["train_end"] == report["validation_start"]
    assert lab.active() is None
    with pytest.raises(ValueError):
        lab.promote(report["version_id"], report["id"], "yes")
    assert lab.active() is None
    lab.promote(report["version_id"], report["id"], "실전 신호 적용 승인")
    assert Lab(store).active() == report["version_id"]
    # A previous immutable version can be restored only with its own matching validated report.
    with pytest.raises(ValueError):
        lab.promote(version, report["id"], "실전 신호 적용 승인")


def test_failed_validation_cannot_be_promoted(store):
    lab = Lab(store)
    version = lab.add_version("breakout", PARAMS)
    dataset = lab.dataset("KRW-BTC", bars(160))
    report = lab.evaluate(version, dataset, "0.0004", "0.001")
    assert report["promotion_eligible"] is False
    with pytest.raises(ValueError):
        lab.promote(version, report["id"], "실전 신호 적용 승인")
    assert lab.active() is None


def test_approved_strategy_restore_requires_matching_report(store):
    lab = Lab(store)
    first = lab.add_version("breakout", PARAMS)
    second = lab.add_version("momentum", PARAMS)
    now = datetime.now(UTC).isoformat()
    with store.connect() as conn:
        for identifier, version in [("r1", first), ("r2", second)]:
            report = {
                "promotion_eligible": True,
                "validation": {
                    "fee_rate_assumption": "0.0004",
                    "slippage_assumption": "0.001",
                },
            }
            conn.execute(
                "INSERT INTO lab_runs VALUES (?,?,?,?,?)",
                (identifier, version, "fixture", json.dumps(report), now),
            )
    lab.promote(first, "r1", "실전 신호 적용 승인")
    lab.promote(second, "r2", "실전 신호 적용 승인")
    lab.promote(first, "r1", "실전 신호 적용 승인")
    assert lab.active() == first
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM promotion_history").fetchone()[0] == 3


def test_websocket_snapshot_origin_and_no_auto_live_strategy(tmp_path):
    from starlette.websockets import WebSocketDisconnect

    with TestClient(
        create_app(tmp_path / "ws.sqlite3", market_enabled=False),
        base_url="http://127.0.0.1",
    ) as api:
        with api.websocket_connect(
            "ws://127.0.0.1/ws/stream", headers={"origin": HEADERS["origin"]}
        ) as socket:
            data = socket.receive_json()
            assert data["type"] == "snapshot"
            assert data["risk"]["buy_signals_enabled"] is False
            assert data["market"]["source"] == "OFFLINE"
        with pytest.raises(WebSocketDisconnect):
            with api.websocket_connect(
                "ws://127.0.0.1/ws/stream", headers={"origin": "https://other.example"}
            ):
                pass
        overview = api.get("/api/lab/overview").json()
        assert overview["automatic_promotion"] is False
        assert overview["active_version"] is None
        assert overview["paper_initial_capital"] == "150000"
        assert len(overview["versions"]) == 3


def test_local_real_socket_reconnect_and_trade_id_dedup(store):
    import asyncio
    import contextlib

    from websockets.asyncio.client import connect as actual_connect
    from websockets.asyncio.server import serve

    async def check():
        ready = asyncio.Event()
        sessions = []

        milliseconds = int(time.time() * 1000)

        async def handler(ws):
            subscription = json.loads(await ws.recv())
            sessions.append(subscription)
            raw = {
                "type": "trade",
                "code": "KRW-BTC",
                "trade_price": 100,
                "trade_volume": 1,
                "trade_timestamp": milliseconds,
                "sequential_id": 1,
                "stream_type": "REALTIME",
                "ask_bid": "BID",
            }
            await ws.send(json.dumps(raw))
            if len(sessions) == 1:
                await ws.close()
            else:
                ready.set()
                await ws.wait_closed()

        market = Market(store)
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            def connector(url, **kwargs):
                assert url == "wss://ws-api.bithumb.com/websocket/v1"
                return actual_connect(f"ws://127.0.0.1:{port}", **kwargs)

            with patch("manual_assistant.market.connect", side_effect=connector):
                task = asyncio.create_task(market.shard(["KRW-BTC"]))
                try:
                    await asyncio.wait_for(ready.wait(), 8)
                    assert market.metrics["reconnects"] == 1
                    assert sessions[0][0]["ticket"] != sessions[1][0]["ticket"]
                    assert {v["type"] for v in sessions[1] if "type" in v} == {
                        "trade",
                        "ticker",
                    }
                    assert not market.safe("KRW-BTC")[0]
                finally:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        assert market.connections["KRW-BTC"] is False
        with store.connect() as conn:
            assert conn.execute("SELECT count(*) FROM market_events").fetchone()[0] == 1

    asyncio.run(check())


def test_candidates_use_each_symbols_own_orderbook(store):
    market = Market(store)
    market.seeds.update({"KRW-BTC": time.time(), "KRW-ETH": time.time()})
    market.latest = {
        "KRW-BTC": {
            "ticker": {
                "turnover_24h": "5000000000",
                "price": "100",
                "exchange_at": iso(BASE),
            },
            "orderbook": {"bids": [["100", "1000"]], "asks": [["100.1", "1000"]]},
        },
        "KRW-ETH": {
            "ticker": {
                "turnover_24h": "4000000000",
                "price": "200",
                "exchange_at": iso(BASE),
            },
            "orderbook": {"bids": [["200", "1000"]], "asks": [["200.4", "1000"]]},
        },
    }
    with (
        patch.object(market, "safe", return_value=(True, "정상")),
        patch.object(market.book, "load", return_value=bars(30)),
    ):
        items = {r["symbol"]: r for r in market.scan_basic()}
    assert D(items["KRW-BTC"]["spread"]) == D("0.001")
    assert D(items["KRW-ETH"]["spread"]) == D("0.002")
    del market.latest["KRW-BTC"]["orderbook"]
    with (
        patch.object(market, "safe", return_value=(False, "호가 미수신")),
        patch.object(market.book, "load", return_value=bars(30)),
    ):
        items = {r["symbol"]: r for r in market.scan_basic()}
    assert items["KRW-BTC"]["spread"] is None
