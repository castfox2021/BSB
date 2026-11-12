import json

import pytest
from fastapi.testclient import TestClient

from manual_assistant.app import create_app


@pytest.fixture
def api(tmp_path):
    app = create_app(tmp_path / "history.sqlite3", market_enabled=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        app.state.private.account = "account-a"
        yield client


def seed_signals(api, count=217):
    at = "2026-01-01T00:00:00+00:00"
    with api.app.state.storage.connect() as conn:
        for i in range(count):
            sid = f"signal-{i:05}"
            payload = {
                "id": sid,
                "symbol": "KRW-BTC",
                "type": "WATCH_ENTRY",
                "strategy_version": "observation-v1",
                "generated_at": at,
                "expires_at": at,
                "reference_price": "100",
                "reasons": ["fixture"],
                "data_at": at,
            }
            conn.execute(
                "INSERT INTO signals VALUES(?,?,?,?,?,?,?)",
                (
                    sid,
                    "KRW-BTC",
                    "WATCH_ENTRY",
                    "observation-v1",
                    json.dumps(payload),
                    at,
                    at,
                ),
            )


def test_all_signal_history_beyond_old_limit_and_deterministic_page_ties(api):
    seed_signals(api)
    first = api.get("/api/history/signals?page=1&page_size=10").json()
    assert first["total"] == 217 and first["pages"] == 22 and len(first["items"]) == 10
    assert first["items"][0]["id"] == "signal-00216"
    second = api.get(
        "/api/history/signals",
        params={"page": 2, "page_size": 10, "before": first["snapshot"]},
    ).json()
    assert second["items"][0]["id"] == "signal-00206"
    assert not set(r["id"] for r in first["items"]) & set(
        r["id"] for r in second["items"]
    )
    last = api.get(
        "/api/history/signals",
        params={"page": 999, "page_size": 10, "before": first["snapshot"]},
    ).json()
    assert last["page"] == 22 and len(last["items"]) == 7


def test_snapshot_excludes_new_even_backdated_rows_without_page_shifts(api):
    seed_signals(api, 25)
    first = api.get("/api/history/signals").json()
    expected = api.get(
        "/api/history/signals", params={"page": 2, "before": first["snapshot"]}
    ).json()
    with api.app.state.storage.connect() as conn:
        at = "2026-01-01T00:00:00+00:00"
        payload = first["items"][0] | {"id": "zz-backdated-late"}
        conn.execute(
            "INSERT INTO signals VALUES(?,?,?,?,?,?,?)",
            (
                "zz-backdated-late",
                "KRW-BTC",
                "WATCH_ENTRY",
                "observation-v1",
                json.dumps(payload),
                at,
                at,
            ),
        )
    second = api.get(
        "/api/history/signals", params={"page": 2, "before": first["snapshot"]}
    ).json()
    assert second["items"] == expected["items"] and second["total"] == 25
    latest = api.get("/api/history/signals").json()
    assert latest["total"] == 26 and latest["items"][0]["id"] == "zz-backdated-late"


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page": 1000001},
        {"page_size": 0},
        {"page_size": 51},
        {"before": "bad"},
        {"before": "2099-01-01T00:00:00+00:00"},
        {"before": "2026-01-01T00:00:00+00:00|evil"},
    ],
)
def test_invalid_history_inputs_are_rejected(api, params):
    assert api.get("/api/history/signals", params=params).status_code == 422


def test_order_paging_scopes_accounts_and_does_not_expose_parameters(api):
    with api.app.state.storage.connect() as conn:
        for i in range(24):
            conn.execute(
                "INSERT INTO manual_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"order-{i:03}",
                    "account-a" if i < 23 else "account-b",
                    "KRW-BTC",
                    "BUY",
                    "10000",
                    "10000",
                    "{}",
                    "{}",
                    "DONE",
                    f"ex-{i}",
                    None,
                    "2026-01-01T00:00:30+00:00",
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                    None,
                    None,
                ),
            )
    result = api.get("/api/history/orders").json()
    assert result["total"] == 23 and result["pages"] == 3
    assert all(
        "parameters" not in r and "account_id" not in r and "session" not in r
        for r in result["items"]
    )
    third = api.get(
        "/api/history/orders", params={"page": 3, "before": result["snapshot"]}
    ).json()
    assert len(third["items"]) == 3


def test_day_filter_precedes_pagination_and_notes_are_kept(api):
    with api.app.state.storage.connect() as conn:
        for i in range(25):
            # 16:00 UTC is the next day in KST.
            at = "2026-01-01T16:00:00+00:00" if i < 22 else "2026-01-01T00:00:00+00:00"
            conn.execute(
                "INSERT INTO private_fills VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "account-a",
                    f"fill-{i:03}",
                    "order-a",
                    "KRW-BTC",
                    "BUY",
                    "1",
                    "100",
                    "0",
                    "EXACT",
                    at,
                    at,
                    "REST",
                ),
            )
        conn.execute(
            "INSERT INTO private_notes VALUES(?,?,?)",
            ("account-a", "fill-021", "review note"),
        )
    result = api.get("/api/history/journal?day=2026-01-02").json()
    assert result["total"] == 22 and result["pages"] == 3
    assert result["items"][0]["notes"] == "review note"
    last = api.get(
        "/api/history/journal",
        params={"day": "2026-01-02", "page": 3, "before": result["snapshot"]},
    ).json()
    assert len(last["items"]) == 2 and all("account_id" not in f for f in last["items"])
    assert api.get("/api/history/journal?day=bad").status_code == 422


def test_trade_paging_preserves_full_ledger_cost_basis(api):
    with api.app.state.storage.connect() as conn:
        conn.execute(
            "INSERT INTO manual_fills VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "buy",
                "KRW-BTC",
                "BUY",
                "1",
                "10000",
                "4",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "",
            ),
        )
        conn.execute(
            "INSERT INTO manual_fills VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "sell",
                "KRW-BTC",
                "SELL",
                "1",
                "11000",
                "4",
                "2026-01-02T00:00:00+00:00",
                "2026-01-02T00:00:00+00:00",
                "",
            ),
        )
    result = api.get("/api/history/trades?page_size=1").json()
    assert result["total"] == 2 and result["items"][0]["id"] == "sell"
    assert result["items"][0]["realized_net_krw"] == "992"
    second = api.get(
        "/api/history/trades",
        params={"page": 2, "page_size": 1, "before": result["snapshot"]},
    ).json()
    assert second["items"][0]["id"] == "buy"


def test_empty_and_unknown_history(api):
    result = api.get("/api/history/automation").json()
    assert result["total"] == 0 and result["pages"] == 1 and result["items"] == []
    assert api.get("/api/history/not-a-table").status_code == 422
