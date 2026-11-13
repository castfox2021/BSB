import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from manual_assistant.app import Settings
from manual_assistant.reversion import PRIMARY, ReversionLab
from manual_assistant.storage import Storage


class Book:
    def __init__(self):
        self.bars = [
            {
                "start": 1_800_000_000 + n * 60,
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100",
                "volume": "10",
                "complete": True,
            }
            for n in range(40)
        ]

    def load_contiguous(self, *_args, **_kwargs):
        return self.bars


class Market:
    def __init__(self):
        self.bid = "100"
        self.ask = "100"
        self.book = Book()
        self.rows = [{"symbol": "KRW-TEST", "price": "100", "ready": True,"completed_price_change_1m":"0","completed_price_change_3m":"0","completed_price_change_5m":"0"}]
        self.manual_detail = {}

    def quote(self, _symbol):
        return {
            "bids": [[self.bid, "1000000"]],
            "asks": [[self.ask, "1000000"]],
        }

    def safe(self, _symbol):
        return True, "SAFE"


def setup(tmp_path):
    store = Storage(tmp_path / "reversion.sqlite3")
    store.initialize(Settings().model_dump())
    market = Market()
    return store, market, ReversionLab(store, market)


def enter(lab, market):
    config = lab.config()
    lab._enter(
        "KRW-TEST",
        D("99"),
        market.book.bars,
        market.quote("KRW-TEST"),
        config,
        ["-0.02", "-0.03", "-0.04"],
    )
    return config


def test_schema_is_paper_only_and_entry_persists_all_comparison_policies(tmp_path):
    store, market, lab = setup(tmp_path)
    enter(lab, market)
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
        rows = conn.execute("SELECT * FROM reversion_positions").fetchall()
        assert len(rows) == 5
        assert all(D(row["current_stop"]) >= D(row["entry_price"]) * D("0.985") for row in rows)
        config = json.loads(
            conn.execute("SELECT value FROM strategy_state WHERE key='reversion_config'").fetchone()[0]
        )
        assert config["enabled"] and not config["live_execution"]
    restarted = ReversionLab(Storage(store.path), market)
    assert len(restarted.summary()["positions"]) == 1


def test_trailing_stop_only_moves_up_and_gap_exit_uses_available_bid(tmp_path):
    store, market, lab = setup(tmp_path)
    config = enter(lab, market)
    config["trailing_activation_rate"] = "0.5"  # Open position keeps its saved 2% policy.
    market.bid = "102.2"
    lab._protect(config)
    with store.connect() as conn:
        row = conn.execute(
            "SELECT * FROM reversion_positions WHERE policy=?", (PRIMARY,)
        ).fetchone()
        raised = D(row["current_stop"])
        assert row["trail_active"] and raised >= D("102.2") * D("0.99")
    market.bid = "101"
    lab._protect(config)
    with store.connect() as conn:
        row = conn.execute(
            "SELECT * FROM reversion_positions WHERE policy=?", (PRIMARY,)
        ).fetchone()
        assert row["status"] == "CLOSED" and row["exit_reason"] == "TRAILING_STOP"
        assert D(row["current_stop"]) == raised
        assert D(row["exit_price"]) < raised  # gap/slippage is recorded, never invented at stop.


def test_ten_minute_failure_does_not_close_a_rising_position(tmp_path):
    store, market, lab = setup(tmp_path)
    config = enter(lab, market)
    market.bid = "101"
    market.book.bars[-3]["close"] = "100.2"
    market.book.bars[-2]["close"] = "100.5"
    market.book.bars[-1]["close"] = "101"
    old = (datetime.now(UTC) - timedelta(minutes=11)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE reversion_positions SET opened_at=?", (old,))
    lab._protect(config)
    with store.connect() as conn:
        assert conn.execute(
            "SELECT count(*) FROM reversion_positions WHERE status='OPEN'"
        ).fetchone()[0] == 5


def test_rebound_failure_and_fixed_take_comparison_are_recorded(tmp_path):
    store, market, lab = setup(tmp_path)
    config = enter(lab, market)
    old = (datetime.now(UTC) - timedelta(minutes=11)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE reversion_positions SET opened_at=?", (old,))
    market.bid = "100.1"
    lab._protect(config)
    with store.connect() as conn:
        assert conn.execute(
            "SELECT count(*) FROM reversion_positions WHERE exit_reason='REBOUND_FAILURE'"
        ).fetchone()[0] == 5

    store2, market2, lab2 = setup(tmp_path / "second")
    config2 = enter(lab2, market2)
    market2.bid = "102.7"
    lab2._protect(config2)
    with store2.connect() as conn:
        row = conn.execute(
            "SELECT * FROM reversion_positions WHERE policy='structure_fixed_take'"
        ).fetchone()
        assert row["status"] == "CLOSED"
        assert row["exit_reason"] == "FIXED_TAKE_COMPARISON"


def test_completed_drop_requires_multi_minute_confirmation(tmp_path):
    _, _, lab = setup(tmp_path)
    config = lab.config()
    config.update(drop_1m="0.010", drop_3m="0.015", drop_5m="0.020")
    assert not lab.crash_signal([D("-0.011"), D("0.002"), D("0.006")], config)
    assert not lab.crash_signal([D("-0.011"), D("-0.001"), D("-0.011")], config)
    assert lab.crash_signal([D("-0.011"), D("-0.013"), D("-0.014")], config)
    assert lab.crash_signal([D("-0.002"), D("-0.016"), D("-0.018")], config)


def test_rebound_needs_two_higher_completed_bars_and_buffered_breakout(tmp_path):
    _, _, lab = setup(tmp_path)
    config = lab.config()
    weak = [
        {"open": "99", "low": "98.5", "close": "100"},
        {"open": "100", "low": "98.4", "close": "100.3"},
    ]
    assert lab.rebound_trigger(weak, D("100"), D("98"), config) is None
    confirmed = [
        {"open": "99", "low": "98.5", "close": "100"},
        {"open": "100", "low": "99", "close": "100.3"},
    ]
    assert lab.rebound_trigger(confirmed, D("100"), D("98"), config) == D("100.3")


def test_drop_base_rebound_requires_consolidation_before_entry(tmp_path):
    store, market, lab = setup(tmp_path)
    market.rows[0].update(
        completed_price_change_1m="-0.021",
        completed_price_change_3m="-0.031",
        completed_price_change_5m="-0.041",
    )
    market.rows[0]["price"] = "90"
    market.bid = "90"
    market.ask = "90"

    # Step 1: Detect crash
    lab.tick()
    assert "KRW-TEST" in lab.watch

    # Step 2: Immediate V-bounce is rejected because no base has formed.
    market.rows[0]["price"] = "92"
    market.bid = "92"
    market.ask = "92"
    lab.last_scan = 0
    lab.tick()
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM reversion_positions").fetchone()[0] == 0

    # Step 3: five base bars followed by two higher completed confirmation bars.
    bars = list(market.book.bars)
    values = [
        ("95", "95", "89.5", "90"),
        ("90", "90.4", "89.7", "90.1"),
        ("90.1", "90.5", "89.8", "90.2"),
        ("90.2", "90.6", "89.9", "90.3"),
        ("90.3", "90.7", "90", "90.4"),
        ("90.4", "90.8", "90.1", "90.5"),
        ("90.5", "91", "90.2", "90.8"),
        ("90.8", "91.5", "90.4", "91.3"),
    ]
    for offset, (opening, high, low, close) in zip(range(-8, 0), values):
        bars[offset] = {
            "start": 1000 + (offset + 8) * 60,
            "open": opening,
            "high": high,
            "low": low,
            "close": close,
            "volume": "20",
            "complete": True,
        }
    market.book.bars = bars
    market.rows[0]["price"] = "91.3"
    market.bid = "91.3"
    market.ask = "91.3"
    lab.last_scan = 0
    lab.tick()

    with store.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reversion_positions WHERE status='OPEN'"
        ).fetchall()
        assert len(rows) == 5
        # Verify structure low is at or below the base low
        assert all(D(r["structure_low"]) <= D("90") for r in rows)
