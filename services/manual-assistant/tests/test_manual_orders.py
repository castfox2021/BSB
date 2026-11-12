import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from manual_assistant.app import Settings, create_app
from manual_assistant.engine import Engine
from manual_assistant.manual_orders import ManualOrders, OrderBlocked
from manual_assistant.private_auth import Credentials, serialize
from manual_assistant.private_client import PrivateClient
from manual_assistant.private_sync import PrivateSync
from manual_assistant.storage import Storage

D = Decimal
CREDS = Credentials(
    "fake-order-tests-only", "fake-test-secret-at-least-32-characters-000000"
)


class Market:
    latest = {"KRW-BTC": {}}

    def quote(self, symbol):
        return (
            {"bids": [["99.9", "100000"]], "asks": [["100", "100000"]]}
            if symbol in self.latest
            else None
        )


def chance():
    return {
        "bid_fee": "0.0004",
        "ask_fee": "0.0004",
        "market": {
            "id": "KRW-BTC",
            "state": "active",
            "bid": {"min_total": "5000"},
            "ask": {"min_total": "5000"},
            "max_total": "1000000000",
        },
        "bid_account": {"balance": "300000"},
        "ask_account": {"balance": "100000"},
    }


@pytest.fixture
def setup(tmp_path):
    store = Storage(tmp_path / "orders.sqlite3")
    store.initialize(Settings().model_dump())
    market = Market()
    private = PrivateSync(store, tmp_path / ".env", credentials=CREDS)
    private.rest_at = time.monotonic()
    private.ws_connected = True
    private.complete = True
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO private_state(account_id,reconciled) VALUES(?,1)",
            (private.account,),
        )
    engine = Engine(store, market)
    orders = ManualOrders(store, market, private, engine)
    return orders


class Exchange:
    def __init__(self):
        self.requests = []
        self.created = None
        self.mode = "ok"
        self.order_state = "done"
        self.order_quantity = "99.96"

    def handle(self, request):
        self.requests.append(request)
        if request.method == "POST":
            self.created = json.loads(request.content)
            payload = jwt.decode(
                request.headers["authorization"][7:],
                CREDS.secret_key,
                algorithms=["HS256"],
            )
            assert (
                payload["query_hash"]
                == hashlib.sha512(serialize(self.created).encode()).hexdigest()
            )
            assert request.url.path == "/v2/orders"
            if self.mode == "timeout":
                raise httpx.ReadTimeout("do-not-log-response-or-headers")
            if self.mode == "error":
                return httpx.Response(
                    400, json={"error": {"message": CREDS.secret_key}}
                )
            return httpx.Response(201, json={"order_id": "exchange-1"})
        if request.url.path == "/v1/orders/chance":
            return httpx.Response(200, json=chance())
        assert request.url.path == "/v1/order"
        if not self.created:
            return httpx.Response(404, json={"error": {"name": "order_not_found"}})
        at = datetime.now(UTC).isoformat()
        return httpx.Response(
            200,
            json={
                "uuid": "exchange-1",
                "client_order_id": self.created["client_order_id"],
                "market": "KRW-BTC",
                "side": "bid",
                "state": self.order_state,
                "created_at": at,
                "executed_volume": self.order_quantity,
                "paid_fee": "3.9984",
                "trades": [
                    {
                        "uuid": "trade-1",
                        "price": "100",
                        "volume": self.order_quantity,
                        "created_at": at,
                    }
                ],
            },
        )


def connect(orders, exchange):
    client = PrivateClient(CREDS, httpx.MockTransport(exchange.handle))

    async def no_wait_get(*args, **kwargs):
        client.next_at = 0
        return await original_get(*args, **kwargs)

    original_get = client.get
    client.get = no_wait_get
    orders.private.client = client
    orders.activate("실제 매수·매도를 직접 확인합니다")
    return client


def test_success_requires_two_steps_exact_signature_dedup_and_only_fills_update_ledger(
    setup,
):
    async def check():
        exchange = Exchange()
        client = connect(setup, exchange)
        p = await setup.preview("KRW-BTC", "BUY", "10000")
        assert not any(r.method == "POST" for r in exchange.requests)
        assert setup.ledger()["positions"] == []
        with pytest.raises(OrderBlocked):
            await setup.submit(p["id"], "wrong")
        accepted = await setup.submit(p["id"], p["confirmation"])
        assert accepted["status"] == "ACKNOWLEDGED"
        assert setup.ledger()["positions"] == []
        repeated = await setup.submit(p["id"], p["confirmation"])
        assert repeated["duplicate"]
        assert sum(r.method == "POST" for r in exchange.requests) == 1
        await setup.recover()
        assert setup.state()["orders"][0]["status"] == "DONE"
        assert setup.ledger()["positions"][0]["quantity"] == "99.96"
        assert D(setup.ledger()["cash_krw"]) == D("140000.0016")
        await setup.recover()
        assert len(setup.ledger()["executions"]) == 1
        assert CREDS.secret_key not in json.dumps(setup.state())
        from manual_assistant.trade_review import TradeReview

        with setup.store.connect() as conn:
            events = [dict(r) for r in conn.execute("SELECT * FROM decision_events")]
        requests = [
            json.loads(r["payload"])
            for r in events
            if r["entity_type"] == "ORDER_REQUEST"
        ]
        assert len(requests) == 1 and requests[0]["mode"] == "MANUAL_APPROVAL"
        assert len([r for r in events if r["entity_type"] == "FILL"]) == 1
        assert CREDS.secret_key not in json.dumps(events)
        assert TradeReview(
            setup.store, setup.market, setup.private.account
        ).positions()[0]["modes"] == ["MANUAL_APPROVAL"]
        await client.close()

    asyncio.run(check())


@pytest.mark.parametrize(
    "case",
    [
        "cap",
        "minimum",
        "cash",
        "stale",
        "loss",
        "valuation",
        "connection",
        "stopped",
        "external",
    ],
)
def test_preflight_blocks_dangerous_orders_without_post(setup, case):
    async def check():
        exchange = Exchange()
        client = connect(setup, exchange)
        value = "10000"
        if case == "cap":
            value = "50001"
        if case == "minimum":
            value = "4000"
        if case == "cash":
            with patch.object(
                setup.engine,
                "ledger",
                return_value=setup.ledger() | {"cash_krw": "55000"},
            ):
                with pytest.raises(OrderBlocked):
                    await setup.preview("KRW-BTC", "BUY", value)
            await client.close()
            return
        if case == "stale":
            setup.market.quote = lambda symbol: None
        if case == "loss":
            setup.engine.ledger = lambda: {
                "positions": [],
                "valuation_complete": True,
                "daily_realized_krw": "-3000",
                "unrealized_before_exit_fee_krw": "0",
                "consecutive_losses": 0,
                "cash_krw": "147000",
            }
        if case == "valuation":
            setup.engine.ledger = lambda: {"positions": [], "valuation_complete": False}
        if case == "connection":
            setup.private.ws_connected = False
        if case == "stopped":
            setup.stop()
        if case == "external":
            with setup.store.connect() as conn:
                conn.execute(
                    "INSERT INTO private_fills VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        setup.private.account,
                        "external-fill",
                        "external-order",
                        "KRW-BTC",
                        "BUY",
                        "1",
                        "100",
                        "0",
                        "EXACT",
                        datetime.now(UTC).isoformat(),
                        datetime.now(UTC).isoformat(),
                        "WS",
                    ),
                )
        with pytest.raises(OrderBlocked):
            await setup.preview("KRW-BTC", "BUY", value)
        assert not any(r.method == "POST" for r in exchange.requests)
        await client.close()

    asyncio.run(check())


def test_timeout_persists_unknown_and_restart_recovers_without_posting_again(setup):
    async def check():
        exchange = Exchange()
        exchange.mode = "timeout"
        client = connect(setup, exchange)
        p = await setup.preview("KRW-BTC", "BUY", "10000")
        result = await setup.submit(p["id"], p["confirmation"])
        assert result["status"] == "UNKNOWN"
        restarted = ManualOrders(setup.store, setup.market, setup.private, setup.engine)
        assert not restarted.active
        await restarted.recover()
        assert restarted.state()["orders"][0]["status"] == "DONE"
        assert len(restarted.ledger()["executions"]) == 1
        assert sum(r.method == "POST" for r in exchange.requests) == 1
        await client.close()

    asyncio.run(check())


def test_unresolved_error_never_retries_and_blocks_new_intent(setup):
    async def check():
        exchange = Exchange()
        exchange.mode = "error"
        client = connect(setup, exchange)
        p = await setup.preview("KRW-BTC", "BUY", "10000")
        assert (await setup.submit(p["id"], p["confirmation"]))["status"] == "UNKNOWN"
        exchange.created = None  # Exchange cannot find the uncertain order.
        await setup.recover()
        assert setup.state()["orders"][0]["status"] == "UNKNOWN"
        with pytest.raises(OrderBlocked):
            await setup.preview("KRW-BTC", "BUY", "10000")
        assert sum(r.method == "POST" for r in exchange.requests) == 1
        await client.close()

    asyncio.run(check())


def test_expiry_price_change_stop_and_duplicate_concurrent_confirm(setup):
    async def check():
        exchange = Exchange()
        client = connect(setup, exchange)
        p = await setup.preview("KRW-BTC", "BUY", "10000")
        setup.market.quote = lambda symbol: {
            "bids": [["101", "100000"]],
            "asks": [["101.1", "100000"]],
        }
        with pytest.raises(OrderBlocked):
            await setup.submit(p["id"], p["confirmation"])
        setup.market.quote = Market().quote
        with setup.store.connect() as conn:
            conn.execute(
                "UPDATE manual_orders SET expires_at=? WHERE id=?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), p["id"]),
            )
        with pytest.raises(OrderBlocked):
            await setup.submit(p["id"], p["confirmation"])
        p = await setup.preview("KRW-BTC", "BUY", "10000")
        result = await asyncio.gather(
            setup.submit(p["id"], p["confirmation"]),
            setup.submit(p["id"], p["confirmation"]),
        )
        assert sum(r.method == "POST" for r in exchange.requests) == 1
        assert any(r.get("duplicate") for r in result)
        setup.stop()
        assert not setup.state()["active"]
        await client.close()

    asyncio.run(check())


def test_sell_only_registered_available_quantity_and_loss_does_not_prevent_exit(setup):
    async def check():
        exchange = Exchange()
        client = connect(setup, exchange)
        with pytest.raises(OrderBlocked):
            await setup.preview("KRW-BTC", "SELL", "100")
        with setup.store.connect() as conn:
            at = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT INTO manual_fills VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "existing",
                    "KRW-BTC",
                    "BUY",
                    "100",
                    "140",
                    "5",
                    at,
                    at,
                    "user confirmed",
                ),
            )
        with pytest.raises(OrderBlocked):
            await setup.preview("KRW-BTC", "SELL", "101")
        # Current estimated loss is >3000; selling remains possible.
        preview = await setup.preview("KRW-BTC", "SELL", "100")
        assert preview["side"] == "SELL"
        assert not any(r.method == "POST" for r in exchange.requests)
        await client.close()

    asyncio.run(check())


def test_local_api_mutations_need_origin_and_never_activate_without_credentials(
    tmp_path,
):
    with TestClient(
        create_app(tmp_path / "api.sqlite3", market_enabled=False),
        base_url="http://127.0.0.1",
    ) as api:
        for route in ("activate", "preview", "submit", "recover", "stop"):
            assert api.post("/api/orders/" + route, json={}).status_code == 403
        response = api.post(
            "/api/orders/activate",
            headers={"origin": "http://127.0.0.1:13000", "x-bsb-local": "1"},
            json={"approval": "실제 매수·매도를 직접 확인합니다"},
        )
        assert response.status_code == 422
        assert not api.get("/api/orders/status").json()["active"]
        assert not api.get("/api/orders/status").json()["auto_order_enabled"]


def test_stop_during_rate_limit_wait_prevents_post(setup):
    async def check():
        exchange = Exchange()
        client = connect(setup, exchange)
        p = await setup.preview("KRW-BTC", "BUY", "10000")
        original = client.get

        async def delayed_throttle(*args, **kwargs):
            result = await original(*args, **kwargs)
            client.next_at = time.monotonic() + 0.2
            return result

        client.get = delayed_throttle
        task = asyncio.create_task(setup.submit(p["id"], p["confirmation"]))
        await asyncio.sleep(0.05)
        setup.stop()
        result = await task
        assert result["status"] == "REJECTED"
        assert not any(r.method == "POST" for r in exchange.requests)
        await client.close()

    asyncio.run(check())


def test_closed_fill_exact_fee_update_reaches_strategy_ledger(setup):
    async def check():
        exchange = Exchange()
        client = connect(setup, exchange)
        p = await setup.preview("KRW-BTC", "BUY", "10000")
        await setup.submit(p["id"], p["confirmation"])
        await setup.recover()
        with setup.store.connect() as conn:
            conn.execute(
                "UPDATE private_fills SET fee_krw='4',fee_status='EXACT' WHERE trade_id='trade-1'"
            )
        await setup.recover()
        assert setup.ledger()["executions"][0]["fee_krw"] == "4"
        assert D(setup.ledger()["cash_krw"]) == D("140000")
        await client.close()

    asyncio.run(check())


def test_known_permission_rejection_stops_session_and_does_not_retry(setup):
    async def check():
        exchange = Exchange()

        def reject(request):
            if request.method == "POST":
                exchange.requests.append(request)
                return httpx.Response(
                    401,
                    json={
                        "error": {"name": "out_of_scope", "message": CREDS.secret_key}
                    },
                )
            return exchange.handle(request)

        client = PrivateClient(CREDS, httpx.MockTransport(reject))
        original = client.get

        async def get(*args, **kwargs):
            client.next_at = 0
            return await original(*args, **kwargs)

        client.get = get
        setup.private.client = client
        setup.activate("실제 매수·매도를 직접 확인합니다")
        p = await setup.preview("KRW-BTC", "BUY", "10000")
        result = await setup.submit(p["id"], p["confirmation"])
        assert result["status"] == "REJECTED" and not setup.active
        assert CREDS.secret_key not in json.dumps(result)
        assert sum(r.method == "POST" for r in exchange.requests) == 1
        await client.close()

    asyncio.run(check())


def test_sell_submission_and_confirmed_fills_close_position(setup):
    async def check():
        exchange = Exchange()

        def handler(request):
            response = exchange.handle(request)
            if request.url.path == "/v1/order" and exchange.created:
                data = response.json()
                data.update(side="ask", executed_volume="100", paid_fee="3.996")
                data["trades"] = [
                    {
                        "uuid": "sell-trade",
                        "price": "99.9",
                        "volume": "100",
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                ]
                return httpx.Response(200, json=data)
            return response

        client = PrivateClient(CREDS, httpx.MockTransport(handler))
        original = client.get

        async def get(*args, **kwargs):
            client.next_at = 0
            return await original(*args, **kwargs)

        client.get = get
        setup.private.client = client
        setup.activate("실제 매수·매도를 직접 확인합니다")
        with setup.store.connect() as conn:
            at = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT INTO manual_fills VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "starting-buy",
                    "KRW-BTC",
                    "BUY",
                    "100",
                    "100",
                    "4",
                    at,
                    at,
                    "confirmed",
                ),
            )
        preview = await setup.preview("KRW-BTC", "SELL", "100")
        result = await setup.submit(preview["id"], preview["confirmation"])
        assert result["status"] == "ACKNOWLEDGED"
        assert exchange.created["side"] == "ask" and exchange.created["volume"] == "100"
        assert (
            exchange.created["order_type"] == "market"
            and "price" not in exchange.created
        )
        await setup.recover()
        assert setup.ledger()["positions"] == []
        assert D(setup.ledger()["cash_krw"]) == D("149982.004")
        await client.close()

    asyncio.run(check())


def test_observation_signal_does_not_require_promoted_strategy_or_submit_orders(setup):
    from manual_assistant.market import Market as RealMarket

    market = RealMarket(setup.store)
    market.latest = {
        "KRW-BTC": {"orderbook": {"exchange_at": datetime.now(UTC).isoformat()}}
    }
    engine = Engine(setup.store, market)
    minute = int(time.time()) // 60 * 60
    bars = [
        {
            "start": minute - (30 - i) * 60,
            "open": "100",
            "close": "101",
            "volume": "100",
            "complete": True,
        }
        for i in range(30)
    ]
    bars[-1]["close"] = "102"
    candidates = [
        {"symbol": "KRW-BTC", "ready": True, "volume_ratio": "1.2", "price": "102"}
    ]
    with (
        patch.object(market, "candidates", return_value=candidates),
        patch.object(market, "safe", return_value=(True, "정상")),
        patch.object(
            market,
            "quote",
            return_value={"bids": [["101", "10000"]], "asks": [["102", "10000"]]},
        ),
        patch.object(market.book, "load", return_value=bars),
        patch.object(market.book, "finalize"),
        patch.object(engine, "paper"),
    ):
        engine.tick()
        assert not engine.risk()["buy_signals_enabled"]
    with setup.store.connect() as conn:
        signals = [dict(r) for r in conn.execute("SELECT * FROM signals")]
        assert len(signals) == 1 and signals[0]["type"] == "WATCH_ENTRY"
        assert conn.execute("SELECT count(*) FROM manual_orders").fetchone()[0] == 0


def test_rest_repeating_fee_allocation_can_be_imported_without_precision_loss_block(
    setup,
):
    async def check():
        exchange = Exchange()

        def handler(request):
            response = exchange.handle(request)
            if request.url.path == "/v1/order" and exchange.created:
                data = response.json()
                data.update(executed_volume="3", paid_fee="1")
                data["trades"] = [
                    {
                        "uuid": f"fraction-{i}",
                        "price": "100",
                        "volume": "1",
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                    for i in range(3)
                ]
                return httpx.Response(200, json=data)
            return response

        client = PrivateClient(CREDS, httpx.MockTransport(handler))
        original = client.get

        async def get(*args, **kwargs):
            client.next_at = 0
            return await original(*args, **kwargs)

        client.get = get
        setup.private.client = client
        setup.activate("실제 매수·매도를 직접 확인합니다")
        p = await setup.preview("KRW-BTC", "BUY", "10000")
        await setup.submit(p["id"], p["confirmation"])
        await setup.recover()
        assert setup.state()["orders"][0]["status"] == "DONE"
        assert sum(D(f["fee_krw"]) for f in setup.ledger()["executions"]) == 1
        await client.close()

    asyncio.run(check())
