import asyncio
import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from manual_assistant.app import Settings, create_app
from manual_assistant.private_auth import (
    BithumbAuth,
    CredentialError,
    Credentials,
    load_credentials,
    serialize,
)
from manual_assistant.private_client import PrivateClient, PrivateError
from manual_assistant.private_sync import PrivateSync
from manual_assistant.storage import Storage

# Entirely fictitious credentials. No real network requests in these tests.
CREDS = Credentials("test-access-only", "unit-test-secret-key-not-real-000000000000")
HEADERS = {"origin": "http://127.0.0.1:13000", "x-bsb-local": "1"}


def test_jwt_nonce_timestamp_hash_and_exact_query_wire():
    async def check():
        seen = []

        def handler(request):
            assert request.method == "GET"
            payload = jwt.decode(
                request.headers["authorization"][7:],
                CREDS.secret_key,
                algorithms=["HS256"],
            )
            seen.append(payload)
            query = request.url.query.decode()
            assert payload["query_hash"] == hashlib.sha512(query.encode()).hexdigest()
            assert (
                query
                == "states[]=done&states[]=cancel&amount=1.2300&label=A+%26+%ED%95%9C%EA%B8%80"
            )
            return httpx.Response(200, json=[])

        client = PrivateClient(CREDS, httpx.MockTransport(handler))
        params = {
            "states": ["done", "cancel"],
            "amount": Decimal("1.2300"),
            "label": "A & 한글",
        }
        await client.get("/v1/orders", params)
        client.next_at = 0
        await client.get("/v1/orders", params)
        assert seen[0]["nonce"] != seen[1]["nonce"]
        assert UUID(seen[0]["nonce"]).version == 4
        assert abs(seen[0]["timestamp"] - time.time() * 1000) < 5000
        assert seen[0]["query_hash_alg"] == "SHA512"
        await client.close()

    asyncio.run(check())
    auth = BithumbAuth(CREDS)
    payload = jwt.decode(auth.token(), CREDS.secret_key, algorithms=["HS256"])
    assert "query_hash" not in payload and "query_hash_alg" not in payload
    # Pure parameter encoding/signing can represent body params without any body transport.
    assert serialize({"order_ids": ["abc", "def"]}) == "order_ids[]=abc&order_ids[]=def"
    assert serialize({"b": 2, "a": 1}) == "b=2&a=1"
    assert "test-access" not in repr(auth) + repr(CREDS)
    for invalid in (1.2, True, None, Decimal("NaN")):
        with pytest.raises(ValueError):
            serialize({"price": invalid})


def test_only_local_env_rotation_permissions_and_no_os_environ(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setenv("BITHUMB_ACCESS_KEY", "process-must-not-be-read")
    assert load_credentials(path)[0] is None
    path.write_text(
        "BITHUMB_PRIVATE_ENABLED=true\nBITHUMB_ACCESS_KEY=test\nBITHUMB_SECRET_KEY="
        + CREDS.secret_key
        + "\n"
    )
    path.chmod(0o600)
    with pytest.raises(CredentialError, match="ROTATION"):
        load_credentials(path)
    with path.open("a") as file:
        file.write("BITHUMB_KEYS_ROTATED=true\n")
    assert load_credentials(path)[0].access_key == "test"
    path.chmod(0o644)
    with pytest.raises(CredentialError, match="PERMISSIONS"):
        load_credentials(path)
    assert ".env" in Path(".gitignore").read_text().splitlines()


@pytest.mark.parametrize(
    "code",
    [
        "invalid_query_payload",
        "jwt_verification",
        "expired_jwt",
        "NotAllowIP",
        "out_of_scope",
    ],
)
def test_api_errors_redacted_and_no_write_transport(code, caplog):
    async def check():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(
                401,
                json={
                    "error": {"name": code, "message": CREDS.secret_key},
                    "secret": CREDS.secret_key,
                },
            )

        client = PrivateClient(CREDS, httpx.MockTransport(handler))
        with pytest.raises(PrivateError) as caught:
            await client.get("/v1/accounts")
        assert caught.value.code == code
        assert CREDS.secret_key not in str(caught.value)
        for path in (
            "/v1/orders/new",
            "/v1/withdraws/coin",
            "https://evil.test",
            "/v1/orders/../accounts",
        ):
            with pytest.raises(PermissionError):
                await client.get(path)
        assert len(calls) == 1
        assert not any(
            hasattr(client, name)
            for name in ("post", "delete", "create_order", "cancel_order", "withdraw")
        )
        await client.close()

    asyncio.run(check())
    assert CREDS.secret_key not in caplog.text


@pytest.fixture
def feed(tmp_path):
    store = Storage(tmp_path / "private.sqlite3")
    store.initialize(Settings().model_dump())
    return PrivateSync(store, tmp_path / ".env", credentials=CREDS)


def order():
    return {
        "uuid": "order-1",
        "market": "KRW-BTC",
        "side": "bid",
        "state": "done",
        "executed_volume": "0.3",
        "paid_fee": "12",
        "created_at": "2026-01-01T00:00:00+09:00",
        "trades": [
            {
                "uuid": "fill-1",
                "price": "100000",
                "volume": "0.1",
                "created_at": "2026-01-01T00:00:01+09:00",
            },
            {
                "uuid": "fill-2",
                "price": "100000",
                "volume": "0.2",
                "created_at": "2026-01-01T00:00:02+09:00",
            },
        ],
    }


def event():
    return {
        "type": "myOrder",
        "order_id": "order-1",
        "code": "KRW-BTC",
        "side": "buy",
        "state": "trade",
        "trade_id": "fill-1",
        "trade_price": 100000,
        "trade_quantity": Decimal("0.1"),
        "paid_fee": Decimal("3.9"),
        "trade_timestamp": 1767193201000,
    }


def test_rest_ws_fill_dedup_fee_upgrade_day_journal_and_no_double_count(feed):
    feed.ingest_order(order())
    feed.ingest_order(order())
    fills = feed.rows("private_fills", day="2026-01-01")
    assert len(fills) == 2
    assert sum(Decimal(f["fee_krw"]) for f in fills) == 12
    assert all(f["fee_status"] == "ORDER_TOTAL_ALLOCATION" for f in fills)
    feed.ingest_ws(event())
    feed.ingest_order(order())  # REST must not overwrite exact WS fee.
    fills = feed.rows("private_fills")
    exact = next(f for f in fills if f["trade_id"] == "fill-1")
    assert exact["fee_status"] == "EXACT" and Decimal(exact["fee_krw"]) == Decimal(
        "3.9"
    )
    assert all("account_id" not in f for f in fills)
    with feed.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM manual_fills").fetchone()[0] == 0
        assert conn.execute("SELECT reconciled FROM private_state").fetchone()[0] == 0
    done = event() | {"state": "done", "trade_id": "fake-cumulative"}
    feed.ingest_ws(done)
    assert len(feed.rows("private_fills")) == 2
    with pytest.raises(ValueError, match="conflicting"):
        feed.ingest_ws(event() | {"trade_quantity": "0.2"})
    # Key rotation isolates journal namespaces rather than silently combining accounts.
    other = PrivateSync(
        feed.store,
        Path("/absent"),
        credentials=Credentials("other-account", CREDS.secret_key),
    )
    assert other.rows("private_fills") == []


def test_reconnect_rest_catches_missing_fills_and_failure_stops_buys(feed):
    class Fake:
        async def get(self, path, params=None):
            if path == "/v1/accounts":
                return [
                    {
                        "currency": "KRW",
                        "balance": "200000",
                        "locked": "0",
                        "avg_buy_price": "0",
                        "unit_currency": "KRW",
                    }
                ]
            if path == "/v1/orders":
                return [order()] if params["state"] == "done" else []
            assert path == "/v1/order"
            return order()

    feed.client = Fake()
    feed.ws_connected = True
    asyncio.run(feed.refresh())
    assert len(feed.rows("private_fills")) == 2
    assert feed.status()["history_complete"] and feed.status()["rest_fresh"]
    with feed.store.connect() as conn:
        conn.execute("UPDATE private_state SET reconciled=1")
    assert feed.guard() == []
    asyncio.run(feed.refresh())
    assert len(feed.rows("private_fills")) == 2 and feed.guard() == []
    feed.ws_connected = False
    assert feed.guard()
    feed.ws_connected = True
    feed.rest_at = time.monotonic() - 121
    assert feed.guard()
    feed.save_accounts([{"currency": "KRW", "balance": "199000", "locked": "0"}])
    assert not feed.status()["reconciled"]


def test_live_start_always_blocked_restart_and_secret_free_status(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LIVE_ORDER_ENABLED", "true")
    path = tmp_path / "app.sqlite3"
    app = create_app(path, market_enabled=False)
    with TestClient(app, base_url="http://127.0.0.1") as api:
        state = api.get("/api/private/status").json()
        assert state["connection_state"] == "NOT_CONFIGURED"
        assert state["live_order_enabled"] is False
        assert (
            api.get("/api/private/accounts").json()["strategy_capital_krw"] == "150000"
        )
        response = api.post(
            "/api/trading/control", headers=HEADERS, json={"action": "start"}
        )
        assert response.status_code == 409
        state = api.post(
            "/api/trading/control", headers=HEADERS, json={"mode": "AUTO_APPROVAL"}
        ).json()
        assert state["mode"] == "AUTO_APPROVAL" and state["state"] == "STOPPED"
        state = api.post(
            "/api/trading/control", headers=HEADERS, json={"action": "emergency-stop"}
        ).json()
        assert state["state"] == "EMERGENCY_STOP" and state["cancel_attempted"] is False
        assert (
            api.post(
                "/api/private/reconcile",
                headers=HEADERS,
                json={"approval": "계좌·전략원장 대조 완료"},
            ).status_code
            == 409
        )
        assert api.post("/api/private/sync", json={}).status_code == 403
    with TestClient(
        create_app(path, market_enabled=False), base_url="http://127.0.0.1"
    ) as api:
        state = api.get("/api/trading/control").json()
        assert state["mode"] == "MANUAL_APPROVAL" and state["state"] == "STOPPED"
        assert state["live_order_enabled"] is False
    assert CREDS.secret_key.encode() not in path.read_bytes()


def test_private_ws_subscription_header_and_reconnect_journal_dedup(feed):
    import contextlib

    from websockets.asyncio.client import connect as actual_connect
    from websockets.asyncio.server import serve

    from manual_assistant.private_client import PRIVATE_WS

    async def check():
        ready = asyncio.Event()
        headers = []
        subscriptions = []

        async def handler(ws):
            headers.append(ws.request.headers["Authorization"])
            subscriptions.append(json.loads(await ws.recv()))
            message = event()
            message["trade_quantity"] = "0.1"
            message["paid_fee"] = "3.9"
            await ws.send(json.dumps(message))
            if len(headers) == 1:
                await ws.close()
            else:
                ready.set()
                await ws.wait_closed()

        class Fake:
            auth = BithumbAuth(CREDS)

        feed.client = Fake()
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            def connector(url, **kwargs):
                assert url == PRIVATE_WS
                return actual_connect(f"ws://127.0.0.1:{port}", **kwargs)

            with (
                patch("manual_assistant.private_sync.connect", connector),
                patch("manual_assistant.private_sync.random.random", return_value=0),
            ):
                task = asyncio.create_task(feed.ws_loop())
                try:
                    await asyncio.wait_for(ready.wait(), 5)
                    await asyncio.sleep(0.05)
                    assert feed.ws_connected
                    assert (
                        feed.rest_at is None
                    )  # REST recovery required after reconnect.
                    assert len(feed.rows("private_fills")) == 1
                    assert subscriptions[0][1] == {"type": "myOrder", "codes": []}
                    assert headers[0] != headers[1]
                    for header in headers:
                        payload = jwt.decode(
                            header[7:], CREDS.secret_key, algorithms=["HS256"]
                        )
                        assert "query_hash" not in payload
                finally:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        assert not feed.ws_connected

    asyncio.run(check())


def test_rate_limit_sets_cooldown_and_no_server_payload_leaks():
    async def check():
        client = PrivateClient(
            CREDS,
            httpx.MockTransport(
                lambda request: httpx.Response(
                    429, headers={"Retry-After": "120"}, text=CREDS.secret_key
                )
            ),
        )
        start = time.monotonic()
        with pytest.raises(PrivateError) as error:
            await client.get("/v1/accounts")
        assert error.value.code == "rate_limited"
        assert client.next_at >= start + 119
        assert CREDS.secret_key not in str(error.value)
        await client.close()

    asyncio.run(check())


def test_missing_rotation_ack_blocks_signal_guard(tmp_path):
    path = tmp_path / ".env"
    path.write_text("BITHUMB_PRIVATE_ENABLED=true\n")
    path.chmod(0o600)
    with TestClient(
        create_app(tmp_path / "app.sqlite3", market_enabled=False),
        base_url="http://127.0.0.1",
    ) as api:
        status = api.get("/api/private/status").json()
        assert status["enabled"] and not status["configured"]
        risk = api.get("/api/risk/status").json()
        assert not risk["buy_signals_enabled"]
        assert any("Private API" in r for r in risk["reasons"])


def test_chance_normalizes_whitelisted_fields_without_echoing_extra_data(tmp_path):
    class Fake:
        async def get(self, path, parameters):
            assert path == "/v1/orders/chance" and parameters == {"market": "KRW-BTC"}
            return {
                "bid_fee": "0.0004",
                "ask_fee": "0.0004",
                "secret": CREDS.secret_key,
                "market": {
                    "bid": {"min_total": "5000"},
                    "ask": {"min_total": "5000"},
                    "max_total": "1000000000",
                },
            }

        async def close(self):
            pass

    app = create_app(tmp_path / "chance.sqlite3", market_enabled=False)
    with TestClient(app, base_url="http://127.0.0.1") as api:
        app.state.private.client = Fake()
        response = api.get("/api/private/chance?market=KRW-BTC")
        assert response.status_code == 200
        assert response.json()["bid"]["min_total"] == "5000"
        assert CREDS.secret_key not in response.text and "secret" not in response.json()
        assert api.get("/api/private/chance?market=invalid").status_code == 422
