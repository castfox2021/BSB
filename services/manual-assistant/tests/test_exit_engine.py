from decimal import Decimal as D
from fastapi.testclient import TestClient

from manual_assistant.app import Settings, create_app
from manual_assistant.exit_engine import evaluate_exit_step
from manual_assistant.reversion import ReversionLab
from manual_assistant.storage import Storage


class Book:
    def __init__(self):
        self.bars = [
            {
                "start": 1_800_000_000 + n * 60,
                "open": "10000",
                "high": "10100",
                "low": "9900",
                "close": "10000",
                "volume": "10",
                "complete": True,
            }
            for n in range(40)
        ]

    def load_contiguous(self, *_args, **_kwargs):
        return self.bars


class Market:
    def __init__(self):
        self.bid = "10000"
        self.ask = "10000"
        self.book = Book()
        self.rows = [{"symbol": "KRW-TEST", "price": "10000", "ready": True}]
        self.manual_detail = {}

    def quote(self, _symbol):
        return {
            "bids": [[self.bid, "1000000"]],
            "asks": [[self.ask, "1000000"]],
        }

    def safe(self, _symbol):
        return True, "SAFE"


def setup_lab(tmp_path):
    store = Storage(tmp_path / "task17.sqlite3")
    store.initialize(Settings().model_dump())
    market = Market()
    lab = ReversionLab(store, market)
    config = lab.config()
    lab._enter(
        "KRW-TEST",
        D("9900"),
        market.book.bars,
        market.quote("KRW-TEST"),
        config,
        ["-0.02", "-0.03", "-0.04"],
    )
    return store, market, lab, config


def test_tp1_tp2_runner_and_trailing_close(tmp_path):
    store, market, lab, config = setup_lab(tmp_path)
    with store.connect() as conn:
        pos = conn.execute(
            "SELECT * FROM reversion_positions WHERE policy='partial_tp_trailing'"
        ).fetchone()
        init_qty = D(pos["quantity"])
        entry = D(pos["entry_price"])

    # 1. +1.2% -> TP1 sells 25%
    market.bid = str((entry * D("1.013")).quantize(D("0.01")))
    lab._protect(config)
    with store.connect() as conn:
        pos = conn.execute(
            "SELECT * FROM reversion_positions WHERE policy='partial_tp_trailing'"
        ).fetchone()
        ep = conn.execute(
            "SELECT * FROM position_exit_policy WHERE position_id=?", (pos["id"],)
        ).fetchone()
        tp1 = conn.execute(
            "SELECT * FROM position_exit_leg WHERE position_id=? AND leg='TP1'",
            (pos["id"],),
        ).fetchone()
        assert ep["state"] == "TP1_DONE"
        assert tp1["status"] == "FILLED"
        assert abs(D(tp1["filled_quantity"]) - init_qty * D("0.25")) < D("0.00001")
        assert abs(D(pos["quantity"]) - init_qty * D("0.75")) < D("0.00001")

    # 2. +2.5% -> TP2 sells additional 25%, leaves 50% in RUNNER_ACTIVE
    market.bid = str((entry * D("1.026")).quantize(D("0.01")))
    lab._protect(config)
    with store.connect() as conn:
        pos = conn.execute(
            "SELECT * FROM reversion_positions WHERE policy='partial_tp_trailing'"
        ).fetchone()
        ep = conn.execute(
            "SELECT * FROM position_exit_policy WHERE position_id=?", (pos["id"],)
        ).fetchone()
        assert ep["state"] == "RUNNER_ACTIVE"
        assert abs(D(pos["quantity"]) - init_qty * D("0.50")) < D("0.00001")

    # 3. +5% -> Runner stays open, peak updates
    market.bid = str((entry * D("1.05")).quantize(D("0.01")))
    lab._protect(config)
    with store.connect() as conn:
        pos = conn.execute(
            "SELECT * FROM reversion_positions WHERE policy='partial_tp_trailing'"
        ).fetchone()
        ep = conn.execute(
            "SELECT * FROM position_exit_policy WHERE position_id=?", (pos["id"],)
        ).fetchone()
        assert pos["status"] == "OPEN"
        assert ep["state"] == "RUNNER_ACTIVE"
        assert D(ep["peak_price"]) >= entry * D("1.05")

    # 4. Drop > 1.0% from peak -> Runner closes with TRAILING_STOP
    market.bid = str((D(ep["peak_price"]) * D("0.985")).quantize(D("0.01")))
    lab._protect(config)
    with store.connect() as conn:
        pos = conn.execute(
            "SELECT * FROM reversion_positions WHERE policy='partial_tp_trailing'"
        ).fetchone()
        ep = conn.execute(
            "SELECT * FROM position_exit_policy WHERE position_id=?", (pos["id"],)
        ).fetchone()
        assert pos["status"] == "CLOSED"
        assert pos["exit_reason"] == "TRAILING_STOP"
        assert ep["state"] == "CLOSED"


def test_tp1_then_drop_triggers_task16_stop(tmp_path):
    store, market, lab, config = setup_lab(tmp_path)
    with store.connect() as conn:
        entry = D(
            conn.execute(
                "SELECT entry_price FROM reversion_positions WHERE policy='partial_tp_trailing'"
            ).fetchone()[0]
        )
    market.bid = str((entry * D("1.013")).quantize(D("0.01")))
    lab._protect(config)
    market.bid = str((entry * D("0.98")).quantize(D("0.01")))
    lab._protect(config)
    with store.connect() as conn:
        pos = conn.execute(
            "SELECT * FROM reversion_positions WHERE policy='partial_tp_trailing'"
        ).fetchone()
        assert pos["status"] == "CLOSED"
        assert pos["exit_reason"] == "STRUCTURE_STOP"


def test_single_tick_jump_crosses_tp1_and_tp2_without_duplicates(tmp_path):
    store, market, lab, config = setup_lab(tmp_path)
    with store.connect() as conn:
        entry = D(
            conn.execute(
                "SELECT entry_price FROM reversion_positions WHERE policy='partial_tp_trailing'"
            ).fetchone()[0]
        )
    market.bid = str((entry * D("1.03")).quantize(D("0.01")))
    lab._protect(config)
    # Run second tick at same price -> must NOT duplicate TP1/TP2
    lab._protect(config)
    with store.connect() as conn:
        pos = conn.execute(
            "SELECT * FROM reversion_positions WHERE policy='partial_tp_trailing'"
        ).fetchone()
        legs = conn.execute(
            "SELECT leg,status FROM position_exit_leg WHERE position_id=?", (pos["id"],)
        ).fetchall()
        assert len(legs) == 2
        assert {l["leg"] for l in legs} == {"TP1", "TP2"}
        assert D(pos["quantity"]) > 0


def test_pure_engine_dust_stale_monotonic_stop_and_pending_cancel():
    # Stale / invalid price -> hold
    d_stale = evaluate_exit_step(
        position_id="p1",
        state="OPEN",
        entry_avg_price=D("1000"),
        initial_quantity=D("100"),
        remaining_quantity=D("100"),
        peak_price=D("1000"),
        protective_stop_price=D("985"),
        task16_stop_price=D("985"),
        latest_price=D("1050"),
        tp1_pct=D("0.012"),
        tp1_fraction=D("0.25"),
        tp2_pct=D("0.025"),
        tp2_fraction=D("0.25"),
        trailing_drawdown_pct=D("0.01"),
        is_stale=True,
    )
    assert d_stale.state_after == "OPEN"
    assert not d_stale.intents

    # Pending TP1 + stop hit -> cancel pending first, never oversell
    d_cancel = evaluate_exit_step(
        position_id="p1",
        state="TP1_PENDING",
        entry_avg_price=D("1000"),
        initial_quantity=D("100"),
        remaining_quantity=D("100"),
        peak_price=D("1015"),
        protective_stop_price=D("985"),
        task16_stop_price=D("985"),
        latest_price=D("980"),
        tp1_pct=D("0.012"),
        tp1_fraction=D("0.25"),
        tp2_pct=D("0.025"),
        tp2_fraction=D("0.25"),
        trailing_drawdown_pct=D("0.01"),
        legs={"TP1": {"status": "PENDING"}},
    )
    assert d_cancel.state_after == "RECONCILING"
    assert d_cancel.intents[0].leg == "CANCEL_PENDING"

    # Dust deferral when TP1 notional < 5000 KRW
    d_dust = evaluate_exit_step(
        position_id="p1",
        state="OPEN",
        entry_avg_price=D("1000"),
        initial_quantity=D("10"),
        remaining_quantity=D("10"),
        peak_price=D("1000"),
        protective_stop_price=D("985"),
        task16_stop_price=D("985"),
        latest_price=D("1015"),
        tp1_pct=D("0.012"),
        tp1_fraction=D("0.25"),
        tp2_pct=D("0.025"),
        tp2_fraction=D("0.25"),
        trailing_drawdown_pct=D("0.01"),
        min_order_krw=D("5000"),
    )
    assert d_dust.events[0][0] == "DUST_DEFERRED"
    assert not d_dust.intents


def test_task17_api_endpoints_and_paper_isolation(tmp_path):
    headers = {"origin": "http://127.0.0.1:13000", "x-bsb-local": "1"}
    app = create_app(tmp_path / "api17.sqlite3")
    with TestClient(app, base_url="http://127.0.0.1:13000") as client:
        res = client.get("/api/trading/exit-policy", headers=headers)
        assert res.status_code == 200
        assert res.json()["tp1_pct"] == "0.012"
        assert res.json()["mode"] == "PAPER"

        put_res = client.put(
            "/api/trading/exit-policy",
            headers=headers,
            json={"tp1_pct": "0.015", "tp2_pct": "0.030", "mode": "LIVE"},
        )
        assert put_res.status_code == 200
        # LIVE gate cannot be bypassed via exit-policy PUT
        assert put_res.json()["mode"] == "PAPER"
        assert put_res.json()["tp1_pct"] == "0.015"

        comp = client.get("/api/trading/exit-comparison", headers=headers)
        assert comp.status_code == 200
        assert any(p["policy"] == "partial_tp_trailing" for p in comp.json()["policies"])

        evs = client.get("/api/trading/exit-events", headers=headers)
        assert evs.status_code == 200
        assert "items" in evs.json()
