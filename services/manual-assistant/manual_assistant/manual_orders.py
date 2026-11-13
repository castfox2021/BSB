"""Order execution shared by manual confirmation and explicit automation grants."""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from uuid import uuid4

from .ledger import amount, decimal_math, decimal_text, rebuild
from .private_auth import serialize
from .private_client import BASE, ERRORS, PrivateError
from .private_sync import identifier, now

D = Decimal
logger = logging.getLogger("bsb.manual.orders")
OPEN = ("SUBMITTING", "ACKNOWLEDGED", "UNKNOWN")


class OrderBlocked(ValueError):
    pass


class OrderRejected(OrderBlocked):
    pass


class OrderNotSent(OrderBlocked):
    pass


class OrderTransport:
    """One fixed POST, no automatic retries. Never cancel or withdraw."""

    def __init__(self, client):
        self.client = client

    async def submit(self, parameters, authorize):
        if set(parameters) not in (
            {"market", "side", "order_type", "price", "client_order_id"},
            {"market", "side", "order_type", "volume", "client_order_id"},
        ):
            raise OrderBlocked("주문 필드 오류")
        async with self.client.lock:
            await asyncio.sleep(max(0, self.client.next_at - time.monotonic()))
            self.client.next_at = time.monotonic() + 1
        authorize()  # Recheck stop, expiry, balances and quote after rate-limit waiting.
        # Sign the ordered parameter encoding; transmit the same string fields as JSON.
        token = self.client.auth.token(serialize(parameters))
        response = await self.client.http.post(
            BASE + "/v2/orders",
            json=parameters,
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        )
        if response.status_code in (400, 401, 403):
            try:
                code = response.json().get("error", {}).get("name")
            except Exception:
                code = None
            if code in (
                "invalid_parameter",
                "invalid_price",
                "under_price_limit_bid",
                "under_price_limit_ask",
                "invalid_price_bid",
                "invalid_price_ask",
                "bank_account_required",
                "two_factor_auth_required",
                "blocked_member_id",
            ):
                raise OrderRejected(
                    "거래소가 주문을 거절했습니다. 계정·금액 조건을 확인하세요."
                )
            if code in (
                "invalid_query_payload",
                "jwt_verification",
                "expired_jwt",
                "NotAllowIP",
                "out_of_scope",
            ):
                raise OrderRejected(ERRORS[code])
        if response.status_code != 201:
            # Conservatively uncertain: even HTTP errors are never retried automatically.
            logger.warning(
                "OrderTransport non-201 response: status=%s body=%s",
                response.status_code,
                response.text[:300],
            )
            raise OrderBlocked(
                "거래소 응답 확인 필요: 재전송하지 않고 주문 내역을 조회합니다."
            )
        payload = json.loads(response.text, parse_float=D)
        return identifier(payload.get("order_id", payload.get("uuid")))


class ManualOrders:
    def __init__(self, store, market, private, engine):
        self.store, self.market, self.private, self.engine = (
            store,
            market,
            private,
            engine,
        )
        self.active = False
        self.live_execution_disabled = False
        self.activated_at = None
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.automation = None

    def state(self):
        with self.store.connect() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id,market,side,budget,quantity,status,order_id,created_at,updated_at FROM manual_orders WHERE account_id=? ORDER BY created_at DESC LIMIT 30",
                    (self.private.account,),
                )
            ]
        with self.store.connect() as conn:
            has_open = (
                conn.execute(
                    "SELECT 1 FROM manual_orders WHERE status IN ('SUBMITTING','ACKNOWLEDGED','UNKNOWN') LIMIT 1"
                ).fetchone()
                is not None
            )
            policy = self.store.settings()
            return dict(
                active=self.active,
                has_open_orders=has_open,
                manual_only=not (self.automation and self.automation.active),
                auto_order_enabled=bool(self.automation and self.automation.active),
                configured=self.private.credentials is not None,
                initial_capital_krw=policy.get("initial_capital_krw", "150000"),
                max_entry_krw=policy.get("max_entry_krw", "50000"),
                daily_loss_limit_krw=policy.get("daily_loss_alert_krw", "3000"),
                orders=rows,
            )

    def stop(self):
        if self.automation and self.automation.active:
            self.automation.stop("주문 실행 정지", False)
        self.active = False
        self.activated_at = None
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE manual_orders SET status='EXPIRED',updated_at=? WHERE status='PREVIEW'",
                (now(),),
            )
            self.store.audit(conn, "MANUAL_ORDERS_STOPPED", {})

    def ready(self):
        if self.live_execution_disabled:
            raise OrderBlocked(
                "수동 추종 모드: 프로그램의 실제 주문 전송이 비활성화되어 있습니다."
            )
        s = self.private.status()
        if (
            not s["configured"]
            or not s["enabled"]
            or not s["rest_fresh"]
            or not s["ws_connected"]
            or s["error_code"]
            or not s["history_complete"]
            or s["pending_orders"]
        ):
            raise OrderBlocked("계좌·Private WS·체결 내역의 최신 동기화가 필요합니다.")
        with self.store.connect() as conn:
            state = conn.execute(
                "SELECT state FROM trading_control WHERE id=1"
            ).fetchone()[0]
            if state == "EMERGENCY_STOP":
                raise OrderBlocked("긴급 정지 상태입니다. 먼저 정지 상태를 해제하세요.")
            if self.activated_at:
                external = conn.execute(
                    """SELECT 1 FROM private_fills f WHERE f.account_id=? AND f.received_at>=?
                    AND NOT EXISTS(SELECT 1 FROM manual_orders o WHERE o.account_id=f.account_id AND o.order_id=f.order_id) LIMIT 1""",
                    (self.private.account, self.activated_at),
                ).fetchone()
                if external:
                    raise OrderBlocked(
                        "외부 체결 발생: 주문을 정지하고 수동 원장을 대조한 후 다시 활성화하세요."
                    )

    def activate(self, approval):
        if self.automation and self.automation.active:
            raise OrderBlocked("자동매매를 먼저 정지하세요.")
        self.ready()
        if (
            approval != "실제 매수·매도를 직접 확인합니다"
            or not self.private.status()["reconciled"]
        ):
            raise OrderBlocked(
                "계좌·전략 원장 대조와 수동 주문 활성화 확인이 필요합니다."
            )
        self.active = True
        self.activated_at = now()
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE trading_control SET mode='MANUAL_APPROVAL',state='RUNNING',updated_at=? WHERE id=1",
                (now(),),
            )
            self.store.audit(conn, "MANUAL_ORDERS_ACTIVATED", {"manual_only": True})
        return self.state()

    def ledger(self):
        # Existing manual entries plus this app's confirmed exchange fills. Never add an order acknowledgment.
        return self.engine.ledger()

    @decimal_math
    def validate(self, market, side, value, chance):
        self.ready()
        if not self.active:
            raise OrderBlocked("수동 실주문이 정지되어 있습니다.")
        if (
            side not in ("BUY", "SELL")
            or market not in self.market.latest
            or not market.startswith("KRW-")
        ):
            raise OrderBlocked("종목 또는 주문 방향 오류")
        if side == "BUY" and hasattr(self.market, "trade_guard"):
            ok, reason = self.market.trade_guard(market)
            if not ok:
                raise OrderBlocked(reason)
        quote = self.market.quote(market)
        if not quote:
            raise OrderBlocked("신선하고 정상인 현재가·호가가 없습니다.")
        bid, ask = D(quote["bids"][0][0]), D(quote["asks"][0][0])
        if ask <= bid or (ask - bid) / bid > D("0.003"):
            raise OrderBlocked("스프레드가 넓거나 호가가 비정상입니다.")
        rules = chance.get("market", {})
        if rules.get("state") != "active" or rules.get("id") != market:
            raise OrderBlocked("거래 가능 종목 상태를 확인할 수 없습니다.")
        ledger = self.ledger()
        positions = {p["symbol"]: p for p in ledger["positions"]}
        quantity, budget, total, fee = D(0), D(0), D(0), D(0)
        if side == "BUY":
            budget = amount(value)
            policy = self.store.settings()
            if (
                budget > min(D("50000"), D(policy["max_entry_krw"]))
                or budget != budget.to_integral_value()
            ):
                raise OrderBlocked(
                    "수수료 포함 신규 매수는 정수 원 단위, 최대 50,000원입니다."
                )
            fee = amount(str(chance.get("bid_fee", "")), True)
            if fee > D("0.01"):
                raise OrderBlocked("계정 수수료율을 확인하세요.")
            total = (budget / (1 + fee)).to_integral_value(rounding=ROUND_DOWN)
            quantity, spent = D(0), D(0)
            for price, size in quote["asks"]:
                p, q = D(price), D(size)
                if p > ask * D("1.003"):
                    break
                take = min(q, (total - spent) / p)
                quantity += take
                spent += take * p
            if total - spent > D("1e-18"):
                raise OrderBlocked("허용 가격 범위 내 매도 호가 잔량이 부족합니다.")
            if quantity <= 0 or spent / quantity / ask - 1 > D(
                policy["execution_slippage_rate"]
            ) + D("1e-24"):
                raise OrderBlocked("예상 슬리피지 초과")
            if not ledger["valuation_complete"]:
                raise OrderBlocked("보유분 평가 시세가 오래되었습니다.")
            exit_rate = max(
                D(policy["execution_fee_rate"]),
                amount(str(chance.get("ask_fee", "")), True),
            )
            if exit_rate > D("0.01"):
                raise OrderBlocked("Account exit fee requires review")
            exit_fee = sum(
                D(p["quantity"]) * D(p["mark_price"]) * exit_rate
                for p in positions.values()
            )
            loss = (
                D(ledger["daily_realized_krw"])
                + D(ledger["unrealized_before_exit_fee_krw"])
                - exit_fee
            )
            if loss <= -min(D("3000"), D(policy["daily_loss_alert_krw"])):
                raise OrderBlocked("일일 손실 한도 도달: 신규 매수 중지")
            if ledger["consecutive_losses"] >= policy["consecutive_losses_limit"]:
                raise OrderBlocked("연속 손실 한도 도달")
            if len(positions) >= policy["max_positions"] and market not in positions:
                raise OrderBlocked("보유 종목 한도 도달")
            cap = D(policy.get("initial_capital_krw", "150000"))
            if D(ledger["cash_krw"]) > cap:
                # Profits do not automatically grow order sizing or total strategy capital.
                available = cap
            else:
                available = D(ledger["cash_krw"])
            if sum(D(p["cost_basis_krw"]) for p in positions.values()) + budget > cap:
                raise OrderBlocked(f"총 전략 운용 자본 {int(cap):,}원 초과")
            if available - budget < D(policy["minimum_cash_reserve_krw"]):
                raise OrderBlocked("전략 현금 여유 부족")
            actual = amount(str(chance.get("bid_account", {}).get("balance", "")), True)
            if actual < budget or D(ledger["cash_krw"]) > actual:
                raise OrderBlocked("계좌 사용 가능 KRW 또는 원장 대조 필요")
            minimum = amount(str(rules.get("bid", {}).get("min_total", "")))
            if total < minimum:
                raise OrderBlocked("최소 매수 금액 미달")
            params = {
                "market": market,
                "side": "bid",
                "order_type": "price",
                "price": decimal_text(total),
            }
        else:
            quantity = amount(value)
            held = D(positions.get(market, {}).get("quantity", "0"))
            actual = amount(str(chance.get("ask_account", {}).get("balance", "")), True)
            if quantity > min(held, actual):
                raise OrderBlocked("전략 또는 실제 계좌의 매도 가능 수량 초과")
            total = quantity * bid
            fee = amount(str(chance.get("ask_fee", "")), True)
            if fee > D("0.01"):
                raise OrderBlocked("계정 수수료율을 확인하세요.")
            depth = sum(D(q) for p, q in quote["bids"] if D(p) >= bid * D("0.997"))
            if depth < quantity:
                raise OrderBlocked("허용 가격 범위 내 매수 호가 잔량이 부족합니다.")
            if total < amount(str(rules.get("ask", {}).get("min_total", ""))):
                raise OrderBlocked("최소 매도 금액 미달")
            params = {
                "market": market,
                "side": "ask",
                "order_type": "market",
                "volume": decimal_text(quantity),
            }
        if rules.get("max_total") is not None and total > amount(
            str(rules["max_total"])
        ):
            raise OrderBlocked("거래소 최대 주문 금액 초과")
        return params, dict(
            market=market,
            side=side,
            budget=decimal_text(budget),
            quantity=decimal_text(quantity),
            reference_price=decimal_text(ask if side == "BUY" else bid),
            fee_rate=decimal_text(fee),
            estimated_fee_krw=decimal_text(total * fee),
            estimated_notional_krw=decimal_text(total),
        )

    def execution_guard(self, automation, signal_id, side):
        if self.automation and self.automation.active:
            if automation is not self.automation:
                raise OrderBlocked(
                    "자동매매 중 직접 주문은 중지됩니다. 먼저 자동매매를 정지하세요."
                )
            self.automation.authorize(signal_id, side)
        elif automation is not None:
            raise OrderBlocked("자동매매 실행 권한 만료")

    async def preview(self, market, side, value, signal_id=None, *, _automation=None):
        async with self.lock:
            self.execution_guard(_automation, signal_id, side)
            self.ready()
            await self.recover()
            self.require_no_open()
            if hasattr(self.market, "request_detail"):
                self.market.request_detail(market)
                until = time.monotonic() + 3
                while self.market.quote(market) is None and time.monotonic() < until:
                    await asyncio.sleep(0.1)
            chance = await self.private.client.get(
                "/v1/orders/chance", {"market": market}
            )
            params, details = self.validate(market, side, value, chance)
            self.execution_guard(_automation, signal_id, side)
            # Optional signal is attribution, never authorization. Expired signals cannot be used.
            if signal_id:
                with self.store.connect() as conn:
                    signal = conn.execute(
                        "SELECT * FROM signals WHERE id=?", (signal_id,)
                    ).fetchone()
                if (
                    not signal
                    or signal["symbol"] != market
                    or datetime.fromisoformat(signal["expires_at"]) <= datetime.now(UTC)
                ):
                    raise OrderBlocked("신호가 만료되었거나 종목이 다릅니다.")
                compatible = (
                    ("BUY_CANDIDATE", "WATCH_ENTRY")
                    if side == "BUY"
                    else ("SELL_CANDIDATE", "RISK_EXIT")
                )
                if signal["type"] not in compatible:
                    raise OrderBlocked("신호 방향이 다릅니다.")
            oid = str(uuid4())
            params["client_order_id"] = oid
            expires = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
            if signal_id:
                expires = min(expires, signal["expires_at"])
            with self.store.connect() as conn:
                conn.execute(
                    "INSERT INTO manual_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        oid,
                        self.private.account,
                        market,
                        side,
                        details["budget"],
                        value,
                        json.dumps(params),
                        json.dumps(details),
                        "PREVIEW",
                        None,
                        signal_id,
                        expires,
                        now(),
                        now(),
                        None,
                        self.activated_at,
                    ),
                )
                from .review_events import append_event

                append_event(
                    conn,
                    "ORDER_PREVIEW",
                    oid,
                    market,
                    now(),
                    {
                        "signal_id": signal_id,
                        "side": side,
                        "details": details,
                        "mode": "AUTO_APPROVAL"
                        if _automation is not None
                        else "MANUAL_APPROVAL",
                        "exit_reason": "MANUAL_EXIT"
                        if side == "SELL" and not signal_id
                        else None,
                    },
                )
                if _automation is not None:
                    conn.execute(
                        "UPDATE auto_signal_claims SET order_intent_id=? WHERE account_id=? AND signal_id=? AND status='CLAIMED'",
                        (oid, self.private.account, signal_id),
                    )
            return {
                **details,
                "id": oid,
                "expires_at": expires,
                "confirmation": f"{market} 실제 {'매수' if side == 'BUY' else '매도'}",
                "order_type": "시장가",
                "price_guaranteed": False,
            }

    def require_no_open(self):
        with self.store.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM manual_orders WHERE status IN ('SUBMITTING','ACKNOWLEDGED','UNKNOWN') LIMIT 1"
            ).fetchone():
                raise OrderBlocked(
                    "처리 중이거나 결과가 불명확한 주문이 있습니다. 중복 주문을 막고 조회로 확인합니다."
                )

    async def submit(self, oid, confirmation, *, _automation=None):
        async with self.lock:
            with self.store.connect() as conn:
                row = conn.execute(
                    "SELECT * FROM manual_orders WHERE id=? AND account_id=?",
                    (oid, self.private.account),
                ).fetchone()
            if not row:
                raise OrderBlocked("주문 확인 내역 없음")
            self.execution_guard(_automation, row["signal_id"], row["side"])
            if row["status"] != "PREVIEW":
                return {
                    "id": oid,
                    "status": row["status"],
                    "order_id": row["order_id"],
                    "duplicate": True,
                }
            if row["session"] != self.activated_at or datetime.fromisoformat(
                row["expires_at"]
            ) <= datetime.now(UTC):
                raise OrderBlocked("주문 확인 시간이 만료되었습니다. 새로 조회하세요.")
            if (
                confirmation
                != f"{row['market']} 실제 {'매수' if row['side'] == 'BUY' else '매도'}"
            ):
                raise OrderBlocked("실제 주문 확인이 필요합니다.")
            self.ready()
            self.require_no_open()
            chance = await self.private.client.get(
                "/v1/orders/chance", {"market": row["market"]}
            )
            params, detail = self.validate(
                row["market"], row["side"], row["quantity"], chance
            )
            original = json.loads(row["details"])
            # Quote moves against the approved price by >0.3% -> require a fresh preview.
            ratio = D(detail["reference_price"]) / D(original["reference_price"])
            if (
                (row["side"] == "BUY" and ratio > D("1.003"))
                or (row["side"] == "SELL" and ratio < D("0.997"))
                or detail["fee_rate"] != original["fee_rate"]
            ):
                raise OrderBlocked("가격 또는 수수료가 바뀌었습니다. 새로 확인하세요.")
            params["client_order_id"] = oid
            # Freeze exact approved order fields, including budget, across preview/submit.
            if params != json.loads(row["parameters"]):
                raise OrderBlocked("주문 조건 변경: 새로 확인하세요.")
            with self.store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute(
                    "SELECT 1 FROM manual_orders WHERE status IN ('SUBMITTING','ACKNOWLEDGED','UNKNOWN') LIMIT 1"
                ).fetchone():
                    raise OrderBlocked("다른 주문 처리 중")
                # Recheck capital after the previous process may have completed a fill.
                self.execution_guard(_automation, row["signal_id"], row["side"])
                self.validate(row["market"], row["side"], row["quantity"], chance)
                changed = conn.execute(
                    "UPDATE manual_orders SET status='SUBMITTING',updated_at=? WHERE id=? AND status='PREVIEW'",
                    (now(), oid),
                ).rowcount
                if changed != 1:
                    raise OrderBlocked("이미 처리된 주문")
                from .review_events import append_event

                append_event(
                    conn,
                    "ORDER_REQUEST",
                    oid,
                    row["market"],
                    now(),
                    {
                        "side": row["side"],
                        "signal_id": row["signal_id"],
                        "details": original,
                        "mode": "AUTO_APPROVAL"
                        if _automation is not None
                        else "MANUAL_APPROVAL",
                        "exit_reason": "MANUAL_EXIT"
                        if row["side"] == "SELL" and not row["signal_id"]
                        else None,
                    },
                )
                self.store.audit(
                    conn,
                    "MANUAL_ORDER_SUBMITTING",
                    {"id": oid, "market": row["market"], "side": row["side"]},
                )
            try:
                # No retries. A cancellation or crash leaves SUBMITTING for read-only recovery.
                def authorize():
                    try:
                        self.execution_guard(_automation, row["signal_id"], row["side"])
                        if row[
                            "session"
                        ] != self.activated_at or datetime.fromisoformat(
                            row["expires_at"]
                        ) <= datetime.now(UTC):
                            raise OrderBlocked("정지 또는 확인 유효시간 만료")
                        _, current = self.validate(
                            row["market"], row["side"], row["quantity"], chance
                        )
                        current_ratio = D(current["reference_price"]) / D(
                            original["reference_price"]
                        )
                        if (row["side"] == "BUY" and current_ratio > D("1.003")) or (
                            row["side"] == "SELL" and current_ratio < D("0.997")
                        ):
                            raise OrderBlocked("확인한 가격 범위 이탈")
                    except (ValueError, ArithmeticError, KeyError):
                        raise OrderNotSent(
                            "전송 전 재검사 실패: 조건을 새로 확인하세요."
                        ) from None

                order_id = await OrderTransport(self.private.client).submit(
                    params, authorize
                )
            except OrderRejected as error:
                self.active = False
                with self.store.connect() as conn:
                    conn.execute(
                        "UPDATE manual_orders SET status='REJECTED',error=?,updated_at=? WHERE id=?",
                        (str(error), now(), oid),
                    )
                return {"id": oid, "status": "REJECTED", "message": str(error)}
            except OrderNotSent:
                with self.store.connect() as conn:
                    conn.execute(
                        "UPDATE manual_orders SET status='REJECTED',error='전송 전 차단',updated_at=? WHERE id=?",
                        (now(), oid),
                    )
                return {
                    "id": oid,
                    "status": "REJECTED",
                    "message": "정지·만료·조건 변경으로 전송 전에 차단했습니다.",
                }
            except Exception:
                with self.store.connect() as conn:
                    conn.execute(
                        "UPDATE manual_orders SET status='UNKNOWN',error='주문 결과 조회 필요',updated_at=? WHERE id=?",
                        (now(), oid),
                    )
                self.private.wake.set()
                return {
                    "id": oid,
                    "status": "UNKNOWN",
                    "message": "성공 여부 불명: 재전송하지 않습니다. 조회로 확인하세요.",
                }
            with self.store.connect() as conn:
                conn.execute(
                    "UPDATE manual_orders SET status='ACKNOWLEDGED',order_id=?,updated_at=? WHERE id=?",
                    (order_id, now(), oid),
                )
                append_event(
                    conn,
                    "ORDER_ACK",
                    oid,
                    row["market"],
                    now(),
                    {
                        "exchange_order_id": order_id,
                        "side": row["side"],
                        "status": "ACKNOWLEDGED",
                    },
                )
                self.store.audit(
                    conn, "MANUAL_ORDER_ACKNOWLEDGED", {"id": oid, "order_id": order_id}
                )
            self.private.pending.add(order_id)
            self.private.wake.set()
            self.wake.set()
            return {
                "id": oid,
                "status": "ACKNOWLEDGED",
                "order_id": order_id,
                "message": "접수됨 · 실제 체결 확인 중",
            }

    async def recover(self):
        if self.private.client is None:
            return
        with self.store.connect() as conn:
            changes = conn.execute(
                """SELECT m.id,f.fee_krw FROM manual_fills m JOIN private_fills f
                ON m.id='exchange-'||f.trade_id JOIN manual_orders o ON o.order_id=f.order_id AND o.account_id=f.account_id
                WHERE o.account_id=? AND f.fee_krw IS NOT NULL AND m.fee_krw!=f.fee_krw""",
                (self.private.account,),
            ).fetchall()
            for change in changes:
                conn.execute(
                    "UPDATE manual_fills SET fee_krw=? WHERE id=?",
                    (change["fee_krw"], change["id"]),
                )
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM manual_orders WHERE account_id=? AND status IN ('SUBMITTING','ACKNOWLEDGED','UNKNOWN')",
                    (self.private.account,),
                )
            ]
        for row in rows:
            try:
                order = await self.private.client.get(
                    "/v1/order",
                    {"uuid": row["order_id"]}
                    if row["order_id"]
                    else {"client_order_id": row["id"]},
                )
                if order.get("market") != row["market"] or order.get("side") != (
                    "bid" if row["side"] == "BUY" else "ask"
                ):
                    raise OrderBlocked("복구 주문 불일치")
                if (
                    order.get("client_order_id") != row["id"]
                    and row["order_id"] is None
                ):
                    raise OrderBlocked("복구 주문 식별자 불일치")
                self.private.ingest_order(order)
                oid = identifier(order["uuid"])
                # Import only this app's fills into the strategy ledger, with source links.
                with self.store.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    fills = conn.execute(
                        "SELECT * FROM private_fills WHERE account_id=? AND order_id=?",
                        (self.private.account, oid),
                    ).fetchall()
                    if any(f["fee_krw"] is None for f in fills):
                        raise OrderBlocked("수수료 미확인 체결")
                    automatic = (
                        conn.execute(
                            "SELECT 1 FROM auto_signal_claims WHERE order_intent_id=?",
                            (row["id"],),
                        ).fetchone()
                        is not None
                    )
                    for fill in fills:
                        mid = "exchange-" + fill["trade_id"]
                        conn.execute(
                            "INSERT INTO manual_fills VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET fee_krw=excluded.fee_krw",
                            (
                                mid,
                                fill["symbol"],
                                fill["side"],
                                fill["quantity"],
                                fill["price"],
                                fill["fee_krw"],
                                fill["filled_at"],
                                fill["received_at"],
                                "자동매매 체결"
                                if automatic
                                else "앱 직접 주문 자동 체결",
                            ),
                        )
                        conn.execute(
                            "INSERT OR IGNORE INTO fill_links VALUES(?,?,?)",
                            (mid, row["signal_id"], "EXCHANGE"),
                        )
                    # Validate full execution ledger before committing.
                    registered = [
                        dict(r)
                        for r in conn.execute(
                            "SELECT * FROM manual_fills ORDER BY filled_at,recorded_at,id"
                        )
                    ]
                    rebuild(registered, self.store.settings()["initial_capital_krw"])
                    full = sum(D(f["quantity"]) for f in fills) == D(
                        str(order["executed_volume"])
                    )
                    terminal = order.get("state") in ("done", "cancel") and full
                    from .review_events import append_event

                    append_event(
                        conn,
                        "ORDER_STATE",
                        f"{row['id']}:{order.get('state')}:{order['executed_volume']}",
                        row["market"],
                        now(),
                        {
                            "intent_id": row["id"],
                            "exchange_order_id": oid,
                            "exchange_state": order.get("state"),
                            "executed_quantity": str(order["executed_volume"]),
                            "side": row["side"],
                            "partial": D(str(order["executed_volume"])) > 0
                            and order.get("state") == "cancel",
                        },
                    )
                    conn.execute(
                        "UPDATE manual_orders SET order_id=?,status=?,updated_at=? WHERE id=?",
                        (oid, "DONE" if terminal else "ACKNOWLEDGED", now(), row["id"]),
                    )
            except (PrivateError, ValueError, ArithmeticError, KeyError):
                # Unknown stays blocked until positive exchange reconciliation; never resubmit.
                continue

    async def run(self):
        while True:
            try:
                async with self.lock:
                    await self.recover()
            except Exception:
                self.active = False
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=5)
                self.wake.clear()
            except TimeoutError:
                pass
