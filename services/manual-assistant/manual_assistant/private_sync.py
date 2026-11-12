"""Read-only private feed. Imported fills never silently mutate strategy capital."""

import asyncio
import json
import logging
import random
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, localcontext
from uuid import uuid4

from websockets.asyncio.client import connect

from .ledger import decimal_math, rebuild
from .private_auth import CredentialError, load_credentials
from .private_client import ERRORS, PRIVATE_WS, PrivateClient, PrivateError


def now():
    return datetime.now(UTC).isoformat()


def number(value):
    result = Decimal(str(value))
    if (
        not result.is_finite()
        or result < 0
        or len(str(value)) > 100
        or (result and not -30 <= result.adjusted() <= 30)
    ):
        raise ValueError("invalid numeric response")
    return format(result, "f")


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("invalid identifier")
    return value


def timestamp(value):
    if isinstance(value, (int, Decimal)):
        parsed = datetime.fromtimestamp(float(value) / 1000, UTC)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if (
        parsed.tzinfo is None
        or parsed.year < 2020
        or parsed.timestamp() > time.time() + 60
    ):
        raise ValueError("invalid timestamp")
    return parsed.astimezone(UTC).isoformat()


class PrivateSync:
    def __init__(self, store, env_path, credentials=None, client=None):
        self.store = store
        self.credentials = credentials
        self.enabled = credentials is not None
        self.code = "CONFIGURED" if self.enabled else "NOT_CONFIGURED"
        if credentials is None:
            try:
                self.credentials, self.enabled, self.code = load_credentials(env_path)
            except (CredentialError, OSError) as error:
                self.code = (
                    str(error)
                    if isinstance(error, CredentialError)
                    else "ENV_READ_ERROR"
                )
                self.enabled = True  # Misconfiguration must fail closed.
        self.account = self.credentials.account_id if self.credentials else None
        self.client = client
        self.rest_at = None
        self.ws_connected = False
        self.event_at = None
        self.pending = set()
        self.wake = asyncio.Event()
        self.orders = None
        self.complete = False
        self.error = None
        self.sync_lock = asyncio.Lock()

    def status(self):
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT reconciled FROM private_state WHERE account_id=?",
                (self.account,),
            ).fetchone()
            count = conn.execute(
                "SELECT count(*) FROM private_fills WHERE account_id=?", (self.account,)
            ).fetchone()[0]
        fresh = self.rest_at is not None and time.monotonic() - self.rest_at < 120
        return dict(
            enabled=self.enabled,
            configured=self.credentials is not None,
            connection_state=self.code,
            rest_fresh=fresh,
            ws_connected=self.ws_connected,
            last_event_at=self.event_at,
            error_code=self.error,
            message=ERRORS.get(self.error, self.code),
            reconciled=bool(row and row[0]),
            imported_fills=count,
            history_complete=self.complete,
            pending_orders=len(self.pending),
            read_only=True,
            live_order_enabled=False,
            strategy_capital_krw="150000",
        )

    def guard(self):
        with self.store.connect() as conn:
            stopped = (
                conn.execute("SELECT state FROM trading_control WHERE id=1").fetchone()[
                    0
                ]
                == "EMERGENCY_STOP"
            )
        reasons = ["시스템 긴급 정지 상태"] if stopped else []
        if not self.enabled:
            return reasons
        s = self.status()
        if (
            not s["rest_fresh"]
            or not s["ws_connected"]
            or s["error_code"]
            or self.pending
        ):
            reasons.append("Private API 연결·잔고·체결 동기화 확인 필요")
        if not s["history_complete"]:
            reasons.append("체결 조회 범위 미완료: 이전 내역 확인 필요")
        if not s["reconciled"]:
            reasons.append("자동 체결 매매노트와 전략 수동 원장 대조 필요")
        with self.store.connect() as conn:
            manual = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM manual_fills ORDER BY filled_at,recorded_at,id"
                )
            ]
        ledger = rebuild(manual, self.store.settings()["initial_capital_krw"], {})
        balances = {
            r["currency"]: Decimal(r["balance"]) + Decimal(r["locked"])
            for r in self.rows("private_accounts")
        }
        available = next(
            (
                Decimal(r["balance"])
                for r in self.rows("private_accounts")
                if r["currency"] == "KRW"
            ),
            Decimal(0),
        )
        if Decimal(ledger["cash_krw"]) > available or any(
            Decimal(p["quantity"]) > balances.get(p["symbol"].split("-")[1], Decimal(0))
            for p in ledger["positions"]
        ):
            reasons.append("전략 원장의 현금·보유 수량이 실제 계좌를 초과함")
        return reasons

    def rows(self, table, limit=200, day=None):
        if table not in ("private_accounts", "private_orders", "private_fills"):
            raise ValueError("invalid table")
        order = {
            "private_accounts": "currency",
            "private_orders": "created_at DESC",
            "private_fills": "filled_at DESC",
        }[table]
        with self.store.connect() as conn:
            # Day filtering occurs before LIMIT, in KST.
            extra = (
                " AND date(datetime(filled_at,'+9 hours'))=?"
                if day and table == "private_fills"
                else ""
            )
            args = [self.account] + ([day] if extra else []) + [limit]
            rows = [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM {table} WHERE account_id=?{extra} ORDER BY {order} LIMIT ?",
                    args,
                )
            ]
            if table == "private_fills":
                for row in rows:
                    note = conn.execute(
                        "SELECT notes FROM private_notes WHERE account_id=? AND trade_id=?",
                        (self.account, row["trade_id"]),
                    ).fetchone()
                    row["notes"] = note[0] if note else ""
        for row in rows:
            row.pop("account_id", None)
        return rows

    def save_fill(self, trade, order, source, fee=None, fee_status="UNKNOWN"):
        symbol = str(order.get("market", order.get("code", "")))
        if not re.fullmatch(r"KRW-[A-Z0-9]+", symbol):
            return  # Non-KRW fills aren't allocated to the KRW strategy.
        side = {"bid": "BUY", "ask": "SELL", "buy": "BUY", "sell": "SELL"}.get(
            order.get("side")
        )
        if side is None:
            raise ValueError("invalid side")
        tid = identifier(trade.get("uuid", trade.get("trade_id")))
        oid = identifier(order.get("uuid", order.get("order_id")))
        quantity = number(trade.get("volume", trade.get("trade_quantity")))
        price = number(trade.get("price", trade.get("trade_price")))
        if Decimal(quantity) <= 0 or Decimal(price) <= 0:
            raise ValueError("nonpositive fill")
        at = timestamp(trade.get("created_at", trade.get("trade_timestamp")))
        fee_text = number(fee) if fee is not None else None
        with self.store.connect() as conn:
            old = conn.execute(
                "SELECT * FROM private_fills WHERE account_id=? AND trade_id=?",
                (self.account, tid),
            ).fetchone()
            if old:
                if (
                    old["symbol"] != symbol
                    or old["side"] != side
                    or old["order_id"] != oid
                    or Decimal(old["quantity"]) != Decimal(quantity)
                    or Decimal(old["price"]) != Decimal(price)
                ):
                    raise ValueError("conflicting fill")
                if old["fee_status"] == "EXACT":
                    if (
                        fee_status == "EXACT"
                        and fee_text is not None
                        and Decimal(old["fee_krw"]) != Decimal(fee_text)
                    ):
                        raise ValueError("conflicting exact fee")
                    return
                if fee_text is None:
                    return
                if old["fee_krw"] == fee_text and old["fee_status"] == fee_status:
                    return
                conn.execute(
                    "UPDATE private_fills SET fee_krw=?,fee_status=? WHERE account_id=? AND trade_id=?",
                    (fee_text, fee_status, self.account, tid),
                )
            else:
                conn.execute(
                    "INSERT INTO private_fills VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        self.account,
                        tid,
                        oid,
                        symbol,
                        side,
                        quantity,
                        price,
                        fee_text,
                        fee_status,
                        at,
                        now(),
                        source,
                    ),
                )
            from .review_events import append_event, capture_fill

            capture_fill(
                conn,
                "exchange-" + tid,
                symbol,
                at,
                side,
                quantity,
                price,
                fee_text,
                oid,
                partial=len(order.get("trades", [])) > 1
                or (
                    order.get("volume") is not None
                    and Decimal(number(order["volume"])) > Decimal(quantity)
                ),
            )
            if old:
                append_event(
                    conn,
                    "FILL_FEE",
                    f"{tid}:{fee_status}:{fee_text}",
                    symbol,
                    now(),
                    {
                        "fill_id": "exchange-" + tid,
                        "fee_krw": fee_text,
                        "fee_status": fee_status,
                    },
                )
            conn.execute(
                "INSERT INTO private_state(account_id,reconciled) VALUES(?,0) ON CONFLICT(account_id) DO UPDATE SET reconciled=0",
                (self.account,),
            )
        if self.orders and hasattr(self.orders, "wake"):
            self.orders.wake.set()

    @decimal_math
    def ingest_order(self, order):
        oid = identifier(order["uuid"])
        market = str(order["market"])
        if not re.fullmatch(r"[A-Z0-9]+-[A-Z0-9]+", market):
            raise ValueError("invalid market")
        if order["side"] not in ("bid", "ask") or order["state"] not in (
            "wait",
            "watch",
            "done",
            "cancel",
        ):
            raise ValueError("invalid order state")
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO private_orders VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,order_id) DO UPDATE SET state=excluded.state,executed_quantity=excluded.executed_quantity,paid_fee=excluded.paid_fee,updated_at=excluded.updated_at",
                (
                    self.account,
                    oid,
                    market,
                    order["side"],
                    order["state"],
                    number(order.get("executed_volume", "0")),
                    number(order.get("paid_fee", "0")),
                    timestamp(order["created_at"]),
                    now(),
                ),
            )
        trades = order.get("trades", [])
        if not isinstance(trades, list):
            raise ValueError("invalid trades")
        total = sum(
            Decimal(number(t["price"])) * Decimal(number(t["volume"])) for t in trades
        )
        # Only allocate order-level fees if all executed quantity is represented.
        full = sum(Decimal(number(t["volume"])) for t in trades) == Decimal(
            number(order.get("executed_volume", "0"))
        )
        allocated = Decimal(0)
        for i, trade in enumerate(trades):
            fee = trade.get("paid_fee")
            status = "EXACT" if fee is not None else "UNKNOWN"
            if fee is None and full and total and order.get("paid_fee") is not None:
                with localcontext() as ctx:
                    ctx.prec = 80
                    fee = (
                        Decimal(number(order["paid_fee"])) - allocated
                        if i == len(trades) - 1
                        else Decimal(number(order["paid_fee"]))
                        * Decimal(number(trade["price"]))
                        * Decimal(number(trade["volume"]))
                        / total
                    )
                    fee = fee.quantize(
                        Decimal("0.000000000000000000000001"), rounding=ROUND_DOWN
                    )
                    allocated += fee
                status = "ORDER_TOTAL_ALLOCATION"
            self.save_fill(trade, order, "REST", fee, status)

    def ingest_ws(self, event):
        if event.get("type") != "myOrder":
            return
        self.event_at = now()
        oid = identifier(event["order_id"])
        # Cumulative done/cancel quantities are not individual fills.
        if event.get("state") == "trade":
            self.save_fill(
                event,
                event,
                "PRIVATE_WS_V2",
                event.get("paid_fee"),
                "EXACT" if event.get("paid_fee") is not None else "UNKNOWN",
            )
        self.pending.add(oid)
        self.wake.set()

    def save_accounts(self, accounts):
        if not isinstance(accounts, list):
            raise ValueError("invalid accounts")
        parsed = []
        for row in accounts:
            currency = str(row["currency"])
            unit = str(row.get("unit_currency", "KRW"))
            if not re.fullmatch(r"[A-Z0-9]{1,20}", currency) or not re.fullmatch(
                r"[A-Z0-9]{1,20}", unit
            ):
                raise ValueError("invalid currency")
            parsed.append(
                (
                    self.account,
                    currency,
                    number(row["balance"]),
                    number(row["locked"]),
                    number(row.get("avg_buy_price", "0")),
                    unit,
                    now(),
                )
            )
        with self.store.connect() as conn:
            previous = {
                (r["currency"], Decimal(r["balance"]), Decimal(r["locked"]))
                for r in conn.execute(
                    "SELECT * FROM private_accounts WHERE account_id=?", (self.account,)
                )
            }
            current = {(r[1], Decimal(r[2]), Decimal(r[3])) for r in parsed}
            if previous != current:
                conn.execute(
                    "INSERT INTO private_state(account_id,reconciled) VALUES(?,0) ON CONFLICT(account_id) DO UPDATE SET reconciled=0",
                    (self.account,),
                )
            conn.execute(
                "DELETE FROM private_accounts WHERE account_id=?", (self.account,)
            )
            conn.executemany(
                "INSERT INTO private_accounts VALUES(?,?,?,?,?,?,?)", parsed
            )

    async def refresh(self):
        if self.client is None:
            raise PrivateError("network_error")
        async with self.sync_lock:
            self.complete = False
            accounts = await self.client.get("/v1/accounts")
            if not isinstance(accounts, list):
                raise PrivateError("invalid_response")
            self.save_accounts(accounts)
            ids = set(self.pending)
            all_complete = True
            for state in ("done", "cancel", "wait"):
                for page in range(1, 4):
                    orders = await self.client.get(
                        "/v1/orders",
                        {
                            "state": state,
                            "page": page,
                            "limit": 100,
                            "order_by": "desc",
                        },
                    )
                    if not isinstance(orders, list):
                        raise PrivateError("invalid_response")
                    for o in orders:
                        oid = identifier(o["uuid"])
                        with self.store.connect() as conn:
                            previous = conn.execute(
                                "SELECT state,executed_quantity,paid_fee FROM private_orders WHERE account_id=? AND order_id=?",
                                (self.account, oid),
                            ).fetchone()
                        if (
                            previous is None
                            or previous["state"] != o["state"]
                            or Decimal(previous["executed_quantity"])
                            != Decimal(number(o.get("executed_volume", "0")))
                            or Decimal(previous["paid_fee"])
                            != Decimal(number(o.get("paid_fee", "0")))
                        ):
                            ids.add(oid)
                    if len(orders) < 100:
                        break
                    if page == 3:
                        all_complete = False
            # Previously known unfinished orders must be reconciled even after long outages.
            with self.store.connect() as conn:
                ids.update(
                    r[0]
                    for r in conn.execute(
                        "SELECT order_id FROM private_orders WHERE account_id=? AND state IN ('wait','watch')",
                        (self.account,),
                    )
                )
            self.pending.update(ids)
            for oid in sorted(ids)[:40]:
                self.pending.discard(oid)
                try:
                    order = await self.client.get("/v1/order", {"uuid": oid})
                    self.ingest_order(order)
                except BaseException:
                    self.pending.add(oid)
                    raise
            # Refresh balances after slow history import, not before it.
            if ids:
                accounts = await self.client.get("/v1/accounts")
                self.save_accounts(accounts)
            self.rest_at = time.monotonic()
            self.complete = all_complete and not self.pending
            self.error = None
            self.code = "CONNECTED_READ_ONLY"
            with self.store.connect() as conn:
                conn.execute(
                    "INSERT INTO private_state(account_id,last_rest) VALUES(?,?) ON CONFLICT(account_id) DO UPDATE SET last_rest=excluded.last_rest",
                    (self.account, now()),
                )

    async def rest_loop(self):
        while True:
            self.wake.clear()
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.error = (
                    error.code
                    if isinstance(error, PrivateError)
                    else "invalid_response"
                )
                self.rest_at = None
                self.complete = False
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=60)
                await asyncio.sleep(1)
            except TimeoutError:
                pass

    async def ws_loop(self):
        delay = 1
        logger = logging.getLogger("bsb.private.websocket")
        logger.setLevel(logging.CRITICAL)
        while True:
            try:
                async with connect(
                    PRIVATE_WS,
                    additional_headers={
                        "Authorization": "Bearer " + self.client.auth.token()
                    },
                    logger=logger,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2**20,
                    open_timeout=10,
                ) as ws:
                    await ws.send(
                        json.dumps(
                            [
                                {"ticket": str(uuid4())},
                                {"type": "myOrder", "codes": []},
                                {"format": "DEFAULT"},
                            ]
                        )
                    )
                    self.ws_connected = True
                    self.wake.set()  # REST reconciliation on every reconnect.
                    self.rest_at = None
                    delay = 1
                    async for message in ws:
                        event = json.loads(message, parse_float=Decimal)
                        if not isinstance(event, dict) or "error" in event:
                            raise PrivateError("api_error")
                        self.ingest_ws(event)
                    # A clean remote close also backs off before reconnecting.
                    self.ws_connected = False
                    self.rest_at = None
                    await asyncio.sleep(1 + random.random())
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.ws_connected = False
                self.rest_at = None
                self.error = "network_error"
                if getattr(getattr(error, "response", None), "status_code", None) in (
                    418,
                    429,
                ):
                    delay = 600
                await asyncio.sleep(delay + random.random())
                delay = min(60, delay * 2)
            finally:
                self.ws_connected = False

    @asynccontextmanager
    async def start(self):
        tasks = []
        if self.credentials and self.enabled:
            self.client = self.client or PrivateClient(self.credentials)
            tasks = [
                asyncio.create_task(self.rest_loop()),
                asyncio.create_task(self.ws_loop()),
            ]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.client:
                await self.client.close()
