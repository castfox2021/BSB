import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from manual_assistant.app import Settings, create_app
from manual_assistant.engine import Engine, sweep
from manual_assistant.ledger import decimal_math
from manual_assistant.quiet_board import QuietBoard
from manual_assistant.storage import Storage

HEADERS = {"origin": "http://localhost:13000", "x-bsb-local": "1"}


class Market:
    def __init__(self):
        self.latest = {}
        self.connections = {}
        self.bid = "100"
        def load(*_args, **_kwargs):
            return [{"start": int(time.time()) // 60 * 60 - 60, "close": "100"}]

        self.book = SimpleNamespace(load=load, load_contiguous=load)

    def safe(self, symbol):
        return True, "ok"

    def quote(self, symbol):
        return {"bids": [[self.bid, "100000"]], "asks": [["100", "100000"]]}

    def mark(self, symbol="KRW-XRP", price="100", age=0, lag=0):
        at = datetime.now(UTC) - timedelta(seconds=age)
        self.connections[symbol] = True
        self.latest[symbol] = {
            "trade": {
                "price": price,
                "received_at": at.isoformat(),
                "exchange_at": (at - timedelta(seconds=lag)).isoformat(),
                "stream_type": "REALTIME",
            }
        }


@pytest.fixture
def setup(tmp_path):
    store = Storage(tmp_path / "board.sqlite3")
    store.initialize(Settings().model_dump())
    market = Market()
    market.mark()
    board = QuietBoard(store, market)
    prefs = board.preferences()
    with store.connect() as conn:
        version = dict(
            conn.execute(
                "SELECT * FROM strategy_versions WHERE id=?", (prefs["follow_version"],)
            ).fetchone()
        )
    version["params"] = json.loads(version["params"])
    return store, market, board, version


def insert(
    store, version, side="BUY", offset=0, price="100", pnl="0", simulated=True, qty="10"
):
    at = (datetime.now(UTC) - timedelta(seconds=offset)).isoformat()
    payload = {
        "symbol": "KRW-XRP",
        "side": side,
        "at": at,
        "signal_at": at,
        "fill_price": price,
        "quantity": qty,
        "cost_krw": "1000.4",
        "proceeds": "1050",
        "fee_krw": "0.4",
        "slippage_krw": "1",
        "net_pnl": pnl,
        "version_id": version,
        "simulated": simulated,
        "channel": "PAPER",
        "reason_summary": "fixture reason",
    }
    identifier = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO paper_trades VALUES(?,?,?,?)",
            (identifier, version, json.dumps(payload), at),
        )
    return identifier


def test_board_reads_only_confirmed_paper_records_not_signals_or_requests(setup):
    store, market, board, v = setup
    insert(store, v["id"], simulated=False)
    identifier = insert(store, v["id"], offset=1)
    s = board.snapshot()
    assert s["buys"]["total"] == 1 and s["buys"]["items"][0]["id"] == identifier
    assert s["buys"]["items"][0]["actual_exchange_fill"] is False
    assert s["trading_mode"] == "PAPER" and s["live_execution_allowed"] is False
    assert s["notifications"] == {"sound": False, "popup": False, "browser": False}


def test_duplicate_poll_and_restart_keep_same_event_ids_and_paging(setup):
    store, market, board, v = setup
    ids = [insert(store, v["id"], offset=i) for i in range(12)]
    a = board.snapshot(page_size=5)
    b = board.snapshot(buy_page=2, page_size=5)
    assert [t["id"] for t in a["buys"]["items"]] == ids[:5]
    assert not {t["id"] for t in a["buys"]["items"]} & {
        t["id"] for t in b["buys"]["items"]
    }
    again = QuietBoard(Storage(store.path), market).snapshot(page_size=5)
    assert [t["id"] for t in again["buys"]["items"]] == ids[:5]
    assert a["buys"]["total"] == 12 and a["buys"]["pages"] == 3


@pytest.mark.parametrize(
    "age,price,status",
    [
        (20, "100", "RECENT"),
        (20, "102", "CHASE_CAUTION"),
        (150, "100", "CHASE_CAUTION"),
        (700, "100", "OLD"),
    ],
)
def test_configurable_age_and_price_status_does_not_approve_order(
    setup, age, price, status
):
    store, market, board, v = setup
    market.mark(price=price)
    insert(store, v["id"], offset=age)
    t = board.snapshot()["buys"]["items"][0]
    assert t["signal_status"] == status and not t["actual_exchange_fill"]


@pytest.mark.parametrize("age,lag", [(30, 0), (0, 30)])
def test_stale_current_price_is_not_used_for_returns_or_valuation(setup, age, lag):
    store, market, board, v = setup
    insert(store, v["id"])
    market.mark(age=age, lag=lag)
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO paper_accounts VALUES(?,?,?,?,?)",
            (
                v["id"],
                "149000",
                json.dumps(
                    {
                        "holdings": {
                            "KRW-XRP": {
                                "quantity": "10",
                                "cost": "1000.4",
                                "entry_price": "100",
                                "opened_at": time.time(),
                            }
                        },
                        "seen": {},
                    }
                ),
                "0",
                0,
            ),
        )
    s = board.snapshot()
    assert s["buys"]["items"][0]["current_price"] is None
    assert s["buys"]["items"][0]["price_change_rate"] is None
    assert s["holdings"][0]["net_estimate_krw"] is None
    assert s["summary"]["holding_count"] == 1


def test_preferences_pin_version_and_never_promote_or_change_strategy(setup):
    store, market, board, v = setup
    before = store.settings()
    with store.connect() as conn:
        others = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM strategy_versions WHERE id!=?", (v["id"],)
            )
        ]
    changed = board.save_preferences(
        {
            "follow_version": others[0],
            "recent_seconds": 30,
            "stale_seconds": 300,
            "chase_rate": "0.02",
        }
    )
    assert changed["follow_version"] == others[0]
    assert QuietBoard(Storage(store.path), market).preferences() == changed
    assert store.settings() == before
    with store.connect() as conn:
        assert (
            conn.execute(
                "SELECT value FROM strategy_state WHERE key='active_version'"
            ).fetchone()
            is None
        )
    for patch_value in [
        {"stale_seconds": 20},
        {"recent_seconds": True},
        {"chase_rate": "NaN"},
        {"follow_version": "missing"},
        {"live_execution_allowed": True},
    ]:
        with pytest.raises(ValueError):
            board.save_preferences(patch_value)


@decimal_math
def test_comparison_has_all_history_not_last_thousand_or_combined_capital(setup):
    store, market, board, v = setup
    for i in range(1003):
        insert(store, v["id"], side="SELL", offset=i, pnl="1")
    g = next(r for r in board.comparison()["items"] if r["version_id"] == v["id"])
    assert g["paper_sell_fills"] == 1003 and g["paper_net_krw"] == "1003"
    assert g["live_sell_fills"] == 0
    assert D(g["paper_realized_return_rate"]) == D("1003") / D("150000")
    assert board.snapshot(sell_page=201)["summary"]["today_realized_krw"] == "1003"


def test_kst_daily_pnl_not_affected_by_visible_sell_page(setup):
    store, market, board, v = setup
    insert(store, v["id"], side="SELL", pnl="850")
    insert(store, v["id"], side="SELL", offset=86400 * 2, pnl="-500")
    assert (
        board.snapshot(page_size=1, sell_page=2)["summary"]["today_realized_krw"]
        == "850"
    )


def test_old_fill_price_derived_only_from_persisted_funds_quantity_and_fee(setup):
    store, market, board, v = setup
    identifier = insert(store, v["id"])
    with store.connect() as conn:
        row = conn.execute(
            "SELECT payload FROM paper_trades WHERE id=?", (identifier,)
        ).fetchone()
        t = json.loads(row[0])
        t.pop("fill_price")
        t.pop("signal_at")
        conn.execute(
            "UPDATE paper_trades SET payload=? WHERE id=?", (json.dumps(t), identifier)
        )
    t = board.detail(identifier)
    assert D(t["fill_price"]) == 100 and t["derived_fill_price"]
    assert t["elapsed_basis"] == "FILL_FALLBACK"
    with store.connect() as conn:
        t = json.loads(
            conn.execute(
                "SELECT payload FROM paper_trades WHERE id=?", (identifier,)
            ).fetchone()[0]
        )
        t.pop("quantity")
        conn.execute(
            "UPDATE paper_trades SET payload=? WHERE id=?", (json.dumps(t), identifier)
        )
    assert board.detail(identifier)["fill_price"] is None


@decimal_math
def test_paper_engine_still_uses_identical_sweep_and_records_display_evidence(setup):
    store, market, board, v = setup
    engine = Engine(store, market)
    with patch("manual_assistant.engine.entry", return_value=(True, ["test entry"])):
        engine.paper(v, [{"symbol": "KRW-XRP", "ready": True}])
    qty, cost = sweep([["100", "100000"]], budget=D("50000"))
    s = board.snapshot()
    t = s["buys"]["items"][0]
    assert D(t["quantity"]) == qty and D(t["cost_krw"]) == cost
    assert D(t["fill_price"]) == cost / (qty * D("1.0004"))
    assert t["reason_summary"] == "test entry"
    assert board.detail(t["id"])["strategy_snapshot"]["id"] == v["id"]
    assert s["summary"]["holding_count"] == 1
    market.bid = "90"
    engine.paper(v, [], False)
    sold = board.snapshot()["sells"]["items"][0]
    _, proceeds = sweep([["90", "100000"]], quantity=qty, buy=False)
    assert D(sold["net_pnl"]) == proceeds - cost
    assert (
        sold["reason"] == "stop"
        and D(sold["fee_krw"]) > 0
        and D(sold["slippage_krw"]) > 0
    )
    assert board.snapshot()["summary"]["holding_count"] == 0


def test_api_follow_mode_blocks_live_activation_and_keeps_readonly_journal(tmp_path):
    app = create_app(
        tmp_path / "app.sqlite3",
        origins=["http://localhost:13000"],
        market_enabled=False,
    )
    with TestClient(app, base_url="http://localhost") as client:
        assert app.state.orders.live_execution_disabled
        data = client.get("/api/board").json()
        assert (
            not data["live_execution_allowed"]
            and data["operation_mode"] == "QUIET_FOLLOW"
        )
        assert (
            client.post(
                "/api/orders/activate",
                headers=HEADERS,
                json={"approval": "실제 매수·매도를 직접 확인합니다"},
            ).status_code
            == 422
        )
        assert not client.get("/api/orders/status").json()["active"]
        app.state.engine.lab.active = lambda: "test-approved"
        r = client.post(
            "/api/automation/activate",
            headers=HEADERS,
            json={
                "approval": "신호에 따라 실제 자동매매를 시작합니다",
                "version": "test-approved",
                "budget_krw": "10000",
                "permissions_confirmed": True,
            },
        )
        assert r.status_code == 422 and "수동 추종" in r.text
        assert not client.get("/api/automation/status").json()["active"]
        assert (
            client.patch(
                "/api/board/preferences", json={"recent_seconds": 30}
            ).status_code
            == 403
        )
        assert (
            client.patch(
                "/api/board/preferences", headers=HEADERS, json={"recent_seconds": 30}
            ).status_code
            == 200
        )
        assert client.get("/api/trading/journal").status_code == 200
        assert client.get("/api/board/trades/not-a-real-id").status_code == 422


def test_actual_follow_fill_is_linked_to_paper_version_without_any_order(tmp_path):
    app = create_app(
        tmp_path / "follow.sqlite3",
        origins=["http://localhost:13000"],
        market_enabled=False,
    )
    with TestClient(app, base_url="http://localhost") as client:
        board = QuietBoard(app.state.storage, app.state.market)
        version = board.preferences()["follow_version"]
        paper_id = insert(app.state.storage, version, offset=60)
        at = datetime.now(UTC).isoformat()
        data = {
            "id": "actual-follow",
            "symbol": "KRW-XRP",
            "side": "BUY",
            "quantity": "5",
            "price": "101",
            "fee_krw": "0.2",
            "filled_at": at,
            "confirmed": True,
            "paper_trade_id": paper_id,
        }
        assert (
            client.post("/api/manual-fills", headers=HEADERS, json=data).status_code
            == 200
        )
        assert client.post("/api/manual-fills", headers=HEADERS, json=data).json()[
            "duplicate"
        ]
        from manual_assistant.trade_review import TradeReview

        p = TradeReview(app.state.storage, app.state.market).positions()[0]
        assert p["fills"][0]["strategy_version"] == version
        assert p["fills"][0]["paper_trade_id"] == paper_id
        assert not client.get("/api/orders/status").json()["active"]
        assert (
            client.post(
                "/api/manual-fills",
                headers=HEADERS,
                json={**data, "paper_trade_id": None},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/manual-fills",
                headers=HEADERS,
                json={**data, "id": "wrong-side", "side": "SELL"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/manual-fills",
                headers=HEADERS,
                json={**data, "id": "bad-type", "paper_trade_id": {}},
            ).status_code
            == 422
        )


@pytest.mark.parametrize(
    "case,expected",
    [
        ("starting", "STARTING"),
        ("error", "ENGINE_ERROR"),
        ("stalled", "ENGINE_STALLED"),
        ("offline", "DATA_WAIT"),
        ("no_candidates", "MARKET_WAIT"),
        ("watching", "WATCHING"),
    ],
)
def test_runtime_distinguishes_empty_history_from_stopped_analysis(
    setup, case, expected
):
    store, market, board, version = setup
    now = datetime.now(UTC).isoformat()
    engine = SimpleNamespace(
        last_tick_at=now,
        last_error=None,
        paper_diagnostics={
            version["id"]: {
                "entries_scheduled": True,
                "checked_at": now,
                "evaluated": 0,
                "reasons": {"매수 조건 미충족": 1},
            }
        },
    )
    market.rows = [{"ready": True}]
    if case == "starting":
        engine.last_tick_at = None
    elif case == "error":
        engine.last_error = "ValueError"
    elif case == "stalled":
        engine.last_tick_at = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()
    elif case == "offline":
        market.connections = {}
    elif case == "no_candidates":
        market.rows = []
    runtime = QuietBoard(store, market, engine=engine).snapshot()["runtime"]
    assert runtime["state"] == expected
    assert runtime["last_analysis_at"] == engine.last_tick_at
    assert runtime["running"] == (case in ("no_candidates", "watching"))
