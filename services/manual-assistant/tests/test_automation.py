import asyncio
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from test_manual_orders import CREDS, chance
from test_manual_orders import setup as setup

from manual_assistant.app import create_app
from manual_assistant.automation import Automation
from manual_assistant.manual_orders import ManualOrders, OrderBlocked
from manual_assistant.private_client import PrivateClient

D = Decimal


class AutoExchange:
    def __init__(self):
        self.sent = []
        self.orders = {}
        self.mode = "ok"

    def handle(self, request):
        if request.method == "POST":
            p = json.loads(request.content)
            self.sent.append(p)
            oid = f"auto-order-{len(self.sent)}"
            p["uuid"] = oid
            self.orders[oid] = p
            if self.mode == "timeout":
                raise httpx.ReadTimeout("fake timeout")
            if self.mode == "permission":
                return httpx.Response(
                    401,
                    json={
                        "error": {"name": "out_of_scope", "message": CREDS.secret_key}
                    },
                )
            return httpx.Response(201, json={"order_id": oid})
        if request.url.path == "/v1/orders/chance":
            return httpx.Response(200, json=chance())
        assert request.url.path == "/v1/order"
        key = request.url.params.get("uuid")
        p = (
            self.orders.get(key)
            if key
            else next(
                (
                    o
                    for o in self.orders.values()
                    if o["client_order_id"] == request.url.params.get("client_order_id")
                ),
                None,
            )
        )
        if not p:
            return httpx.Response(404, json={"error": {"name": "order_not_found"}})
        qty = D(p["price"]) / 100 if p["side"] == "bid" else D(p["volume"])
        price = D("100") if p["side"] == "bid" else D("99.9")
        fee = qty * price * D(".0004")
        at = datetime.now(UTC).isoformat()
        return httpx.Response(
            200,
            json={
                "uuid": p["uuid"],
                "client_order_id": p["client_order_id"],
                "market": p["market"],
                "side": p["side"],
                "state": "done",
                "created_at": at,
                "executed_volume": str(qty),
                "paid_fee": str(fee),
                "trades": [
                    {
                        "uuid": "fill-" + p["uuid"],
                        "price": str(price),
                        "volume": str(qty),
                        "created_at": at,
                    }
                ],
            },
        )


@pytest.fixture
def auto(setup):
    exchange = AutoExchange()
    client = PrivateClient(CREDS, httpx.MockTransport(exchange.handle))
    original = client.get

    async def get(*args, **kwargs):
        client.next_at = 0
        return await original(*args, **kwargs)

    client.get = get
    setup.private.client = client
    setup.market.safe = lambda s: (True, "정상")
    setup.private.save_accounts(
        [
            {"currency": "KRW", "balance": "300000", "locked": "0"},
            {"currency": "BTC", "balance": "100000", "locked": "0"},
        ]
    )
    with setup.store.connect() as conn:
        conn.execute("UPDATE private_state SET reconciled=1")
    automation = Automation(setup)
    setup.engine.lab.active = lambda: "approved-v1"
    setup.engine.risk = lambda: {"buy_signals_enabled": True, "reasons": []}
    return automation, exchange, client


def start(auto):
    automation, exchange, client = auto
    automation.activate(
        "신호에 따라 실제 자동매매를 시작합니다", "approved-v1", "10000", True
    )
    return automation, exchange, client


def signal(a, key, kind="BUY_CANDIDATE", version="approved-v1"):
    a.engine.signal("KRW-BTC", kind, version, "100", ["fixture"], key)


@pytest.mark.parametrize(
    "case", ["approval", "permission", "version", "budget", "risk", "reconciled"]
)
def test_start_prerequisites_never_send_order(auto, case):
    async def check():
        a, e, client = auto
        approval, version, budget, permission = (
            "신호에 따라 실제 자동매매를 시작합니다",
            "approved-v1",
            "10000",
            True,
        )
        if case == "approval":
            approval = ""
        if case == "permission":
            permission = False
        if case == "version":
            version = "unvalidated"
        if case == "budget":
            budget = "50001"
        if case == "risk":
            a.engine.risk = lambda: {
                "buy_signals_enabled": False,
                "reasons": ["손실 한도"],
            }
        if case == "reconciled":
            with a.store.connect() as conn:
                conn.execute("UPDATE private_state SET reconciled=0")
        with pytest.raises(OrderBlocked):
            a.activate(approval, version, budget, permission)
        assert not a.active and e.sent == []
        await client.close()

    asyncio.run(check())


def test_approved_signals_execute_once_then_sell_and_report_realized_results(auto):
    async def check():
        a, e, client = start(auto)
        assert e.sent == []  # Activation alone does not trade.
        signal(a, "first-buy")
        await a.tick()
        assert len(e.sent) == 1 and e.sent[0]["side"] == "bid"
        assert a.orders.ledger()["positions"] == []  # Ack isn't a fill.
        await a.orders.recover()
        a.orders.private.pending.clear()
        assert a.orders.ledger()["positions"][0]["quantity"] == "99.96"
        await a.tick()
        assert len(e.sent) == 1
        signal(a, "no-average-down")
        await a.tick()
        assert len(e.sent) == 1  # No averaging down.
        # Sell risk signal remains eligible even after changing the approved entry version.
        a.engine.lab.active = lambda: "other-version"
        signal(a, "risk-sell", "RISK_EXIT", "manual-risk")
        await a.tick()
        assert len(e.sent) == 2 and e.sent[1]["side"] == "ask"
        await a.orders.recover()
        assert a.orders.ledger()["positions"] == []
        assert D(a.orders.ledger()["realized_net_krw"]) < 0
        assert len(a.orders.ledger()["executions"]) == 2
        from manual_assistant.trade_review import TradeReview

        with a.store.connect() as conn:
            records = [
                json.loads(r[0])
                for r in conn.execute(
                    "SELECT payload FROM decision_events WHERE entity_type='ORDER_REQUEST'"
                )
            ]
            assert (
                conn.execute(
                    "SELECT count(*) FROM decision_events WHERE entity_type='FILL'"
                ).fetchone()[0]
                == 2
            )
        assert len(records) == 2 and all(r["mode"] == "AUTO_APPROVAL" for r in records)
        campaign = TradeReview(
            a.store, a.orders.market, a.orders.private.account
        ).positions()[0]
        assert campaign["modes"] == ["AUTO_APPROVAL"]
        assert D(campaign["realized_net_krw"]) == D(
            a.orders.ledger()["realized_net_krw"]
        )
        await client.close()

    asyncio.run(check())


def test_watch_unapproved_expired_and_pre_start_signals_never_trade(auto):
    async def check():
        a, e, client = auto
        signal(a, "pre-start")
        start(auto)
        signal(a, "watch", "WATCH_ENTRY", "observation-v1")
        signal(a, "unapproved", "BUY_CANDIDATE", "experimental")
        signal(a, "expired")
        with a.store.connect() as conn:
            conn.execute(
                "UPDATE signals SET expires_at='2020-01-01T00:00:00+00:00' WHERE type='BUY_CANDIDATE' AND strategy_version='approved-v1'"
            )
        await a.tick()
        assert e.sent == []
        await client.close()

    asyncio.run(check())


def test_pause_stale_connection_loss_and_version_change(auto):
    async def check():
        a, e, client = start(auto)
        signal(a, "buy")
        a.orders.private.ws_connected = False
        await a.tick()
        assert e.sent == [] and a.active
        a.orders.private.ws_connected = True
        a.engine.risk = lambda: {
            "buy_signals_enabled": False,
            "reasons": ["日 손실 한도"],
        }
        await a.tick()
        assert e.sent == []
        # Same signal was claimed/skipped and is never replayed if the gate changes.
        a.engine.risk = lambda: {"buy_signals_enabled": True, "reasons": []}
        await a.tick()
        assert e.sent == []
        signal(a, "version")
        a.engine.lab.active = lambda: "new-version"
        await a.tick()
        assert e.sent == []
        await client.close()

    asyncio.run(check())


def test_manual_paths_cannot_use_active_automatic_grant(auto):
    async def check():
        a, e, client = start(auto)
        with pytest.raises(OrderBlocked):
            await a.orders.preview("KRW-BTC", "BUY", "10000")
        a.stop()
        signal(a, "stopped")
        await a.tick()
        assert e.sent == [] and not a.orders.active
        await client.close()

    asyncio.run(check())


@pytest.mark.parametrize("mode", ["timeout", "permission"])
def test_transport_uncertainty_or_rejection_stops_auto_without_retry(auto, mode):
    async def check():
        a, e, client = start(auto)
        e.mode = mode
        signal(a, "uncertain")
        await a.tick()
        assert len(e.sent) == 1 and not a.active
        signal(a, "another")
        await a.tick()
        assert len(e.sent) == 1
        await a.orders.recover()
        assert len(e.sent) == 1
        await client.close()

    asyncio.run(check())


def test_own_fills_reconcile_without_repeated_user_approval_external_fills_do_not(auto):
    async def check():
        a, e, client = start(auto)
        signal(a, "owned")
        await a.tick()
        await a.orders.recover()
        a.orders.private.pending.clear()
        assert not a.orders.private.status()["reconciled"]
        assert a.account_guard() == []
        with a.store.connect() as conn:
            at = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT INTO private_fills VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    a.orders.private.account,
                    "external",
                    "outside",
                    "KRW-BTC",
                    "BUY",
                    "1",
                    "100",
                    "0",
                    "EXACT",
                    at,
                    at,
                    "WS",
                ),
            )
        assert a.account_guard()
        await client.close()

    asyncio.run(check())


def test_restart_stopped_and_signal_claim_survives(auto):
    async def check():
        a, e, client = start(auto)
        signal(a, "claim-once")
        await a.tick()
        await a.orders.recover()
        a.orders.private.pending.clear()
        restarted = Automation(
            ManualOrders(a.store, a.orders.market, a.orders.private, a.engine)
        )
        assert not restarted.active and not restarted.orders.active
        await restarted.tick()
        assert len(e.sent) == 1
        with a.store.connect() as conn:
            assert (
                conn.execute("SELECT count(*) FROM auto_signal_claims").fetchone()[0]
                == 1
            )
        await client.close()

    asyncio.run(check())


def test_automatic_stop_during_rate_wait_prevents_post(auto):
    async def check():
        a, e, client = start(auto)
        original = client.get

        async def get(*args, **kwargs):
            result = await original(*args, **kwargs)
            client.next_at = time.monotonic() + 0.3
            return result

        client.get = get
        signal(a, "delayed")
        task = asyncio.create_task(a.tick())
        await asyncio.sleep(0.1)
        a.stop()
        await task
        assert e.sent == [] and not a.active
        await client.close()

    asyncio.run(check())


def test_api_cannot_start_without_local_intent_or_credentials(tmp_path):
    with TestClient(
        create_app(tmp_path / "api.sqlite3", market_enabled=False),
        base_url="http://127.0.0.1",
    ) as api:
        assert api.post("/api/automation/activate", json={}).status_code == 403
        assert not api.get("/api/automation/status").json()["active"]
        headers = {"origin": "http://127.0.0.1:13000", "x-bsb-local": "1"}
        response = api.post(
            "/api/automation/activate",
            headers=headers,
            json={
                "approval": "신호에 따라 실제 자동매매를 시작합니다",
                "version": "fake",
                "budget_krw": "10000",
                "permissions_confirmed": True,
            },
        )
        assert response.status_code == 422
        assert not api.get("/api/orders/status").json()["active"]


def test_stop_cancels_waiting_attempt_without_killing_worker_or_retrying(auto):
    import contextlib

    from manual_assistant.manual_orders import OrderTransport

    async def check():
        a, e, client = start(auto)
        entered = asyncio.Event()

        async def hang(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        signal(a, "cancel-in-flight")
        with patch.object(OrderTransport, "submit", hang):
            worker = asyncio.create_task(a.run())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                a.stop()
                await asyncio.sleep(0.05)
                assert not worker.done() and not a.active and e.sent == []
                with a.store.connect() as conn:
                    assert (
                        conn.execute("SELECT status FROM manual_orders").fetchone()[0]
                        == "SUBMITTING"
                    )
                with pytest.raises(OrderBlocked):
                    a.activate(
                        "신호에 따라 실제 자동매매를 시작합니다",
                        "approved-v1",
                        "10000",
                        True,
                    )
            finally:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker
        await client.close()

    asyncio.run(check())


def test_real_risk_engine_accepts_own_reconciled_fills_and_keeps_exit_on_report_expiry(
    auto,
):
    from manual_assistant.engine import Engine

    async def check():
        a, e, client = auto
        # Only promotion evidence is a fixture; run the real production risk engine.
        a.engine.risk = Engine.risk.__get__(a.engine, Engine)
        a.engine.private_guard = a.account_guard
        with a.store.connect() as conn:
            at = datetime.now(UTC).isoformat()
            conn.execute("UPDATE settings SET value='true' WHERE key='costs_confirmed'")
            report = {
                "validation": {
                    "fee_rate_assumption": "0.0004",
                    "slippage_assumption": "0.001",
                }
            }
            conn.execute(
                "INSERT INTO lab_runs VALUES(?,?,?,?,?)",
                ("risk-fixture", "approved-v1", "fixture", json.dumps(report), at),
            )
            conn.execute(
                "INSERT INTO promotion_history VALUES(NULL,?,?,?,?)",
                ("approved-v1", None, "risk-fixture", at),
            )
        start(auto)
        signal(a, "real-risk-buy")
        await a.tick()
        assert len(e.sent) == 1
        await a.orders.recover()
        a.orders.private.pending.clear()
        assert not a.orders.private.status()["reconciled"]
        assert a.engine.risk()["buy_signals_enabled"]
        with a.store.connect() as conn:
            conn.execute("UPDATE lab_runs SET created_at='2020-01-01T00:00:00+00:00'")
        assert not a.engine.risk()["buy_signals_enabled"]
        signal(a, "exit-with-expired-entry-report", "RISK_EXIT", "manual-risk")
        await a.tick()
        assert len(e.sent) == 2 and e.sent[-1]["side"] == "ask"
        await client.close()

    asyncio.run(check())
