import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from manual_assistant.app import Settings, create_app
from manual_assistant.ledger import rebuild
from manual_assistant.review_events import (
    append_event,
    archive_candles,
    capture_fill,
    capture_signal,
)
from manual_assistant.storage import Storage
from manual_assistant.trade_review import TradeReview

BASE = int(datetime(2026, 1, 1, 14, 59, tzinfo=UTC).timestamp())
HEADERS = {"origin": "http://localhost:13000", "x-bsb-local": "1"}


def iso(seconds):
    return datetime.fromtimestamp(seconds, UTC).isoformat()


@pytest.fixture
def store(tmp_path):
    db = Storage(tmp_path / "review.sqlite3")
    db.initialize(Settings().model_dump())
    return db


class Market:
    latest = {}
    model = None

    def quote(self, symbol):
        return {"bids": [["110", "100"]], "asks": [["110.1", "100"]]}


def fill(store, id, side, qty, price, fee, offset, source="STRATEGY", partial=False):
    at = iso(BASE + offset)
    with store.connect() as conn:
        if source == "STRATEGY":
            conn.execute(
                "INSERT INTO manual_fills VALUES(?,?,?,?,?,?,?,?,?)",
                (id, "KRW-TEST", side, qty, price, fee, at, at, ""),
            )
        else:
            conn.execute(
                "INSERT INTO private_fills VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "account",
                    id,
                    "order-" + id,
                    "KRW-TEST",
                    side,
                    qty,
                    price,
                    fee,
                    "EXACT" if fee else "UNKNOWN",
                    at,
                    at,
                    "WS",
                ),
            )
        with patch("manual_assistant.review_events.utcnow", return_value=at):
            capture_fill(
                conn, id, "KRW-TEST", at, side, qty, price, fee, partial=partial
            )


def bar(start, price="100", complete=True):
    return dict(
        symbol="KRW-TEST",
        timeframe=60,
        start=start,
        end=start + 60,
        open=price,
        high=price,
        low=price,
        close=price,
        volume="10",
        turnover=str(D(price) * 10),
        closed=1,
        complete=complete,
        origin="REST",
        updated_at=iso(start + 60),
    )


def test_partial_buys_and_sells_match_existing_weighted_ledger(store):
    fill(store, "b1", "BUY", "2", "100", "1", 0)
    fill(store, "b2", "BUY", "1", "130", "2", 10)
    fill(store, "s1", "SELL", "1", "120", "0.5", 20, partial=True)
    fill(store, "s2", "SELL", "2", "105", "1", 30)
    review = TradeReview(store, Market())
    p = review.positions()[0]
    with store.connect() as conn:
        expected = rebuild(
            [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM manual_fills ORDER BY filled_at,recorded_at,id"
                )
            ]
        )
    assert D(p["realized_net_krw"]) == D(expected["realized_net_krw"]) == D("-4.5")
    assert p["result"] == "LOSS" and p["quantity"] == "0"
    assert D(p["fees_krw"]) == D("4.5")
    assert sum(D(a["quantity"]) for a in p["allocations"]) == 3
    assert sum(D(a["cost_including_buy_fee_krw"]) for a in p["allocations"]) == 333
    assert p["fills"][2]["exit_reason"] == "MANUAL_EXIT"


def test_exit_reason_is_independent_of_profit(store):
    fill(store, "b", "BUY", "1", "100", "0", 0)
    fill(store, "s", "SELL", "1", "101", "0", 20)
    signal = {
        "id": "stop",
        "symbol": "KRW-TEST",
        "type": "RISK_EXIT",
        "generated_at": iso(BASE + 10),
        "strategy_version": "v1",
    }
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO signals VALUES(?,?,?,?,?,?,?)",
            (
                "stop",
                "KRW-TEST",
                "RISK_EXIT",
                "v1",
                json.dumps(signal),
                iso(BASE + 10),
                iso(BASE + 40),
            ),
        )
        conn.execute("INSERT INTO fill_links VALUES(?,?,?)", ("s", "stop", "MANUAL"))
        append_event(
            conn,
            "SIGNAL",
            "stop",
            "KRW-TEST",
            iso(BASE + 10),
            {"signal": signal, "exit_reason": "STOP_LOSS"},
        )
    p = TradeReview(store, Market()).positions()[0]
    assert p["fills"][-1]["exit_reason"] == "STOP_LOSS"
    assert p["fills"][-1]["result"] == "PROFIT"


def test_external_inventory_without_cost_or_fee_does_not_fabricate_profit(store):
    fill(store, "external-s", "SELL", "1", "100", None, 0, source="EXTERNAL")
    review = TradeReview(store, Market(), "account")
    p = review.positions()[0]
    assert p["realized_net_krw"] is None and not p["cost_complete"]
    assert p["scope"] == "EXTERNAL" and not p["fee_complete"]
    assert review.journal("2026-01-01")["total"] == 0
    j = review.journal("2026-01-01", scope="EXTERNAL")
    assert j["total"] == 1 and not j["summary"]["pnl_complete"]
    assert review.performance("2026-01-01", "2026-01-02")["items"] == []


def test_kst_day_boundary_and_notes_survive_restart_and_csv_escapes(store):
    fill(store, "b", "BUY", "1", "100", "1", 0)
    fill(store, "s", "SELL", "1", "110", "1", 120)
    review = TradeReview(store, Market())
    pid = review.positions()[0]["id"]
    review.save_note(pid, "=formula", ["=tag", "손절"])
    again = TradeReview(Storage(store.path), Market())
    assert again.journal("2026-01-01")["summary"]["fill_count"] == 1
    assert again.journal("2026-01-02")["summary"]["realized_net_krw"] == "8"
    assert again.positions()[0]["tags"] == ["=tag", "손절"]
    output = again.csv("2026-01-02")
    assert "'=formula" in output and "'=tag" in output
    assert "2026-01-02T00:01:00+09:00" in output


def test_decision_payload_is_immutable_even_after_live_settings_change(store):
    payload = {
        "id": "x",
        "symbol": "KRW-TEST",
        "type": "BUY_CANDIDATE",
        "strategy_version": "v1",
        "generated_at": iso(BASE),
        "basis": [],
    }
    market = SimpleNamespace(
        latest={
            "KRW-TEST": {
                "orderbook": {"bids": [["23.34", "10"]], "received_at": iso(BASE)}
            }
        },
        detail_cache={"KRW-TEST": {"ai": {"model_id": "m1", "probability": 0.6}}},
    )
    with store.connect() as conn:
        capture_signal(
            conn,
            market,
            payload,
            store.settings(),
            {"passed": True},
            {"id": "v1", "params": {"threshold": "1.5"}},
        )
    market.latest["KRW-TEST"]["orderbook"]["bids"][0][0] = "999"
    with store.connect() as conn:
        conn.execute("UPDATE settings SET value='\"0.9\"' WHERE key='stop_loss_rate'")
        row = conn.execute("SELECT * FROM decision_events").fetchone()
        original = json.loads(row["payload"])
        assert original["thresholds"]["stop_loss_rate"] == "0.008"
        assert original["market_snapshot"]["orderbook"]["bids"][0][0] == "23.34"
        assert original["ai"]["model_id"] == "m1"
        for sql in (
            "UPDATE decision_events SET payload='{}'",
            "DELETE FROM decision_events",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                conn.execute(sql)


def test_replay_hides_future_candle_repair_signal_and_fill(store):
    with store.connect() as conn:
        archive_candles(conn, [bar(BASE)], iso(BASE + 60), "WS_CLOSED")
        repaired = bar(BASE, "999")
        archive_candles(conn, [repaired], iso(BASE + 300), "REST_RECEIVED")
        with patch(
            "manual_assistant.review_events.utcnow", return_value=iso(BASE + 150)
        ):
            append_event(
                conn,
                "SIGNAL",
                "future",
                "KRW-TEST",
                iso(BASE + 100),
                {"signal": {"reference_price": "500"}},
            )
    fill(store, "b", "BUY", "1", "101", "0", 120)
    review = TradeReview(store, Market())
    early = review.chart("KRW-TEST", iso(BASE - 60), iso(BASE + 600), iso(BASE + 90))
    assert early["candles"][0]["close"] == "100"
    assert not early["markers"] and not early["evidence"]
    middle = review.chart("KRW-TEST", iso(BASE - 60), iso(BASE + 600), iso(BASE + 140))
    assert len(middle["markers"]) == 1 and middle["markers"][0]["kind"] == "FILL"
    late = review.chart("KRW-TEST", iso(BASE - 60), iso(BASE + 600), iso(BASE + 400))
    assert late["candles"][0]["close"] == "999" and len(late["markers"]) == 2
    assert late["candles"][0]["sources"] == ["REST_RECEIVED"]


def test_known_at_not_exchange_time_controls_late_fill_marker(store):
    with store.connect() as conn:
        with patch(
            "manual_assistant.review_events.utcnow", return_value=iso(BASE + 180)
        ):
            capture_fill(
                conn,
                "late",
                "KRW-TEST",
                iso(BASE + 10),
                "SELL",
                "0.5",
                "23.34",
                "0.01",
                partial=True,
            )
    review = TradeReview(store, Market())
    assert not review.chart("KRW-TEST", iso(BASE), iso(BASE + 300), iso(BASE + 60))[
        "markers"
    ]
    marker = review.chart("KRW-TEST", iso(BASE), iso(BASE + 300), iso(BASE + 200))[
        "markers"
    ][0]
    assert (
        marker["at"] == iso(BASE + 10)
        and marker["price"] == "23.34"
        and marker["partial"]
    )


@pytest.mark.parametrize("tf", [60, 180, 300])
def test_replay_timeframes_are_aggregated_only_from_known_minutes(store, tf):
    start = BASE // 300 * 300
    with store.connect() as conn:
        archive_candles(
            conn,
            [bar(start + i * 60, str(100 + i)) for i in range(5)],
            iso(start + 300),
        )
    review = TradeReview(store, Market())
    hidden = review.chart(
        "KRW-TEST", iso(start), iso(start + 600), iso(start + 299), tf
    )
    assert hidden["candles"] == []
    shown = review.chart("KRW-TEST", iso(start), iso(start + 600), iso(start + 301), tf)
    assert len(shown["candles"]) == (5 if tf == 60 else 2 if tf == 180 else 1)
    assert max(D(b["high"]) for b in shown["candles"]) == 104
    assert D(shown["candles"][-1]["close"]) == 104


def test_snapshot_of_open_candle_does_not_use_future_high_or_close(store):
    b = bar(BASE, "100", False)
    b["closed"] = 0
    later = {**b, "high": "999", "close": "999"}
    with store.connect() as conn:
        archive_candles(conn, [b], iso(BASE + 10), "WS_DETAIL_SNAPSHOT")
        archive_candles(conn, [later], iso(BASE + 40), "WS_DETAIL_SNAPSHOT")
    early = TradeReview(store, Market()).chart(
        "KRW-TEST", iso(BASE), iso(BASE + 300), iso(BASE + 20)
    )
    assert (
        early["candles"][0]["close"] == "100" and early["candles"][0]["high"] == "100"
    )
    assert not early["candles"][0]["closed"]


def test_duplicate_events_do_not_duplicate_fill_or_replay_marker(store):
    with store.connect() as conn:
        for _ in range(3):
            with patch(
                "manual_assistant.review_events.utcnow", return_value=iso(BASE + 20)
            ):
                capture_fill(
                    conn, "same", "KRW-TEST", iso(BASE), "BUY", "1", "23.34", "0"
                )
    result = TradeReview(store, Market()).chart(
        "KRW-TEST", iso(BASE - 60), iso(BASE + 300), iso(BASE + 60)
    )
    assert len(result["markers"]) == 1


def test_legacy_candle_is_not_backdated_as_original_knowledge(store):
    b = bar(BASE)
    with store.connect() as conn:
        archive_candles(conn, [b], source="LEGACY_IMPORTED_NOW")
    result = TradeReview(store, Market()).chart(
        "KRW-TEST", iso(BASE), iso(BASE + 600), iso(BASE + 400)
    )
    assert result["candles"] == [] and result["missing_minutes"] > 0


def test_new_cycle_after_full_exit_has_separate_stable_id(store):
    fill(store, "b1", "BUY", "1", "100", "0", 0)
    fill(store, "s1", "SELL", "1", "110", "0", 10)
    fill(store, "b2", "BUY", "1", "100", "0", 20)
    p = TradeReview(store, Market()).positions()
    assert len(p) == 2 and p[0]["id"] != p[1]["id"]
    assert p[0]["status"] == "CLOSED" and p[1]["status"] == "OPEN"
    assert p[1]["net_estimate_krw"] is not None


def test_api_journal_replay_notes_export_and_local_review_are_readonly_to_exchange(
    tmp_path,
):
    path = tmp_path / "api.sqlite3"
    app = create_app(path, origins=["http://localhost:13000"], market_enabled=False)
    with TestClient(app, base_url="http://localhost") as client:
        store = app.state.storage
        fill(store, "b", "BUY", "1", "23.34", "0.01", 0)
        fill(store, "s", "SELL", "1", "24", "0.02", 120)
        j = client.get("/api/trading/journal?date=2026-01-02").json()
        assert j["summary"]["realized_net_krw"] == "0.63"
        pid = j["items"][0]["id"]
        assert client.get("/api/trading/positions").json()["items"] == []
        assert client.get(f"/api/trading/positions/{pid}/replay").status_code == 200
        assert client.get(f"/api/trading/positions/{pid}/reasoning").json()[
            "missing_original_evidence"
        ]
        assert (
            client.patch(
                f"/api/trading/positions/{pid}/journal",
                json={"note": "memo", "tags": ["분할"]},
            ).status_code
            == 403
        )
        assert (
            client.patch(
                f"/api/trading/positions/{pid}/journal",
                headers=HEADERS,
                json={"note": "memo", "tags": ["분할"]},
            ).status_code
            == 200
        )
        export = client.get("/api/trading/journal.csv?date=2026-01-02")
        assert (
            export.status_code == 200
            and "text/csv" in export.headers["content-type"]
            and "memo" in export.text
        )
        review = client.post(
            f"/api/trading/positions/{pid}/review", headers=HEADERS, json={}
        ).json()
        assert review["post_hoc"] and not review["affects_live_strategy"]
        assert not review["ai_model_used"]
        assert (
            len(client.get(f"/api/trading/positions/{pid}/reviews").json()["items"])
            == 1
        )
        assert not client.get("/api/automation/status").json()["active"]
        assert (
            client.get("/api/trading/chart?symbol=KRW-TEST&timeframe=15").status_code
            == 422
        )
        assert client.get("/api/trading/journal?date=invalid").status_code == 422
        assert client.get("/api/trading/journal?timezone=UTC").status_code == 422
    with TestClient(
        create_app(path, origins=["http://localhost:13000"], market_enabled=False),
        base_url="http://localhost",
    ) as client:
        assert (
            client.get("/api/trading/journal?date=2026-01-02").json()["items"][0][
                "note"
            ]
            == "memo"
        )


def test_empty_or_cancelled_order_without_fills_has_no_profit(store):
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO manual_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "intent",
                "account",
                "KRW-TEST",
                "BUY",
                "10000",
                "1",
                "{}",
                "{}",
                "DONE",
                "exchange-id",
                None,
                iso(BASE + 60),
                iso(BASE),
                iso(BASE + 10),
                None,
                None,
            ),
        )
    review = TradeReview(store, Market(), "account")
    assert review.positions() == []
    assert review.journal("2026-01-01")["summary"]["realized_net_krw"] == "0"


def test_replay_reference_lines_use_only_captured_thresholds_and_known_fills(store):
    fill(store, "b", "BUY", "1", "100", "0", 30)
    with store.connect() as conn:
        with patch(
            "manual_assistant.review_events.utcnow", return_value=iso(BASE + 10)
        ):
            append_event(
                conn,
                "SIGNAL",
                "old",
                "KRW-TEST",
                iso(BASE + 10),
                {"thresholds": {"stop_loss_rate": "0.01", "take_profit_rate": "0.02"}},
            )
        with patch(
            "manual_assistant.review_events.utcnow", return_value=iso(BASE + 180)
        ):
            append_event(
                conn,
                "SIGNAL",
                "future",
                "KRW-TEST",
                iso(BASE + 180),
                {"thresholds": {"stop_loss_rate": "0.9", "take_profit_rate": "0.9"}},
            )
    r = TradeReview(store, Market())
    early = r.chart("KRW-TEST", iso(BASE), iso(BASE + 300), iso(BASE + 20))
    assert early["reference_lines"] is None
    middle = r.chart("KRW-TEST", iso(BASE), iso(BASE + 300), iso(BASE + 60))
    assert (
        middle["reference_lines"]["stop"] == "99.00"
        and middle["reference_lines"]["target"] == "102.00"
    )


def test_local_model_posthoc_analysis_is_separate_from_original_evidence(store):
    fill(store, "b", "BUY", "1", "100", "0", 0)
    with store.connect() as conn:
        conn.execute("INSERT INTO fill_links VALUES(?,?,?)", ("b", "entry", "MANUAL"))
        append_event(
            conn,
            "SIGNAL",
            "entry",
            "KRW-TEST",
            iso(BASE - 1),
            {
                "signal": {
                    "basis": [bar(BASE - 600 + i * 60, str(90 + i)) for i in range(10)]
                },
                "ai": {"model_id": "original", "probability": 0.4},
            },
        )
    model = SimpleNamespace(
        id="current",
        model={
            "means": [0, 0, 0, 0],
            "scales": [1, 1, 1, 1],
            "weights": [0, 0, 0, 0],
            "bias": 0,
        },
    )
    market = Market()
    market.model = model
    r = TradeReview(store, market)
    pid = r.positions()[0]["id"]
    before = r.reasoning(pid)
    review = r.review(pid)
    assert (
        review["ai_model_used"]
        and review["post_hoc"]
        and not review["affects_live_strategy"]
    )
    assert [m["model_id"] for m in review["model_analysis"]] == ["original", "current"]
    assert review["model_analysis"][1]["probability"] == 0.5
    assert r.reasoning(pid) == before


def test_fee_correction_is_revealed_only_when_received(store):
    with store.connect() as conn:
        with patch(
            "manual_assistant.review_events.utcnow", return_value=iso(BASE + 10)
        ):
            capture_fill(
                conn, "exchange-b", "KRW-TEST", iso(BASE), "BUY", "1", "23.34", None
            )
        with patch(
            "manual_assistant.review_events.utcnow", return_value=iso(BASE + 120)
        ):
            append_event(
                conn,
                "FILL_FEE",
                "correction",
                "KRW-TEST",
                iso(BASE + 120),
                {"fill_id": "exchange-b", "fee_krw": "0.01"},
            )
    r = TradeReview(store, Market())
    assert (
        r.chart("KRW-TEST", iso(BASE), iso(BASE + 300), iso(BASE + 60))["markers"][0][
            "fee_krw"
        ]
        is None
    )
    assert (
        r.chart("KRW-TEST", iso(BASE), iso(BASE + 300), iso(BASE + 180))["markers"][0][
            "fee_krw"
        ]
        == "0.01"
    )


def test_risk_exit_performance_is_attributed_to_entry_strategy(store):
    fill(store, "b", "BUY", "1", "100", "0", 0)
    fill(store, "s", "SELL", "1", "110", "0", 120)
    with store.connect() as conn:
        for fid, sid, version in [
            ("b", "entry", "approved-v1"),
            ("s", "exit", "manual-risk"),
        ]:
            payload = {"strategy_version": version}
            conn.execute(
                "INSERT INTO signals VALUES(?,?,?,?,?,?,?)",
                (
                    sid,
                    "KRW-TEST",
                    "BUY_CANDIDATE" if fid == "b" else "RISK_EXIT",
                    version,
                    json.dumps(payload),
                    iso(BASE),
                    iso(BASE + 30),
                ),
            )
            conn.execute("INSERT INTO fill_links VALUES(?,?,?)", (fid, sid, "MANUAL"))
    r = TradeReview(store, Market())
    p = r.positions()[0]
    assert p["fills"][-1]["decision_strategy_version"] == "manual-risk"
    assert p["fills"][-1]["strategy_version"] == "approved-v1"
    assert (
        r.performance("2026-01-01", "2026-01-02")["items"][0]["strategy"]
        == "approved-v1"
    )
