import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .automation import Automation
from .engine import Engine
from .history import envelope, history
from .history import snapshot as history_snapshot
from .ledger import amount, decimal_text, rebuild, time_at
from .manual_orders import ManualOrders
from .market import Market
from .private_client import PrivateError
from .private_sync import PrivateSync, now, number
from .storage import Storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bsb.manual")
ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    initial_capital_krw: str = "150000"
    max_entry_krw: str = "50000"
    minimum_cash_reserve_krw: str = "50000"
    max_positions: int = 2
    stop_loss_rate: str = "0.008"
    take_profit_rate: str = "0.012"
    daily_loss_alert_krw: str = "3000"
    max_holding_seconds: int = 900
    consecutive_losses_limit: int = 3
    execution_fee_rate: str = "0.0004"
    execution_slippage_rate: str = "0.001"
    costs_confirmed: bool = False
    sound_enabled: bool = False
    browser_alerts_enabled: bool = False

    @model_validator(mode="after")
    def validate_policy(self):
        for key in (
            "initial_capital_krw",
            "max_entry_krw",
            "minimum_cash_reserve_krw",
            "daily_loss_alert_krw",
            "stop_loss_rate",
            "take_profit_rate",
        ):
            text = getattr(self, key)
            pattern = (
                r"(0|[1-9][0-9]*)"
                if key.endswith("_krw")
                else r"(0|[1-9][0-9]*)(\.[0-9]+)?"
            )
            if len(text) > 32 or not re.fullmatch(pattern, text):
                raise ValueError(f"non-canonical decimal: {key}")
            try:
                value = Decimal(text)
            except InvalidOperation as error:
                raise ValueError(f"invalid decimal: {key}") from error
            if not value.is_finite() or value <= 0 or len(getattr(self, key)) > 32:
                raise ValueError(f"invalid amount: {key}")
            if key.endswith("_krw") and value != value.to_integral_value():
                raise ValueError("KRW settings must be whole won")
        if (
            not 1 <= self.max_positions <= 10
            or not 60 <= self.max_holding_seconds <= 86400
        ):
            raise ValueError("invalid position or holding limit")
        if not 1 <= self.consecutive_losses_limit <= 20:
            raise ValueError("invalid loss streak")
        if (
            not Decimal(self.stop_loss_rate) < 1
            or not Decimal(self.take_profit_rate) < 1
        ):
            raise ValueError("rates must be less than 1")
        available = Decimal(self.initial_capital_krw) - Decimal(
            self.minimum_cash_reserve_krw
        )
        if Decimal(self.max_entry_krw) * self.max_positions > available:
            raise ValueError(
                "entry and position limits exceed capital after cash reserve"
            )
        if Decimal(self.daily_loss_alert_krw) >= Decimal(self.initial_capital_krw):
            raise ValueError("daily loss alert must be below capital")
        for key in ("execution_fee_rate", "execution_slippage_rate"):
            value = amount(getattr(self, key), zero=True)
            if value > Decimal("0.05"):
                raise ValueError("비용 가정은 5% 이하")
        return self


def create_app(
    database_path: Path | None = None,
    origins: list[str] | None = None,
    market_enabled: bool | None = None,
    credential_path: Path | None = None,
):
    path = database_path or Path(
        os.getenv("BSB_MANUAL_DB", decimal_text(ROOT / "data/manual-assistant.sqlite3"))
    )
    allowed = origins or os.getenv(
        "BSB_MANUAL_ORIGINS", "http://127.0.0.1:13000,http://localhost:13000,https://careerbook.kr,http://careerbook.kr"
    ).split(",")
    extra_hosts = [h.strip() for h in os.getenv("BSB_ALLOWED_HOSTS", "").split(",") if h.strip()]
    trusted_hosts = {"127.0.0.1", "localhost", "careerbook.kr", "www.careerbook.kr", *extra_hosts}
    if any(
        urlparse(origin).hostname not in trusted_hosts
        or urlparse(origin).scheme not in ("http", "https")
        for origin in allowed
    ):
        raise ValueError("only local or trusted origins are allowed")
    store = Storage(path)
    jobs: set[asyncio.Task] = set()
    semaphore = asyncio.Semaphore(1)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.initialize(Settings().model_dump())
        with store.connect() as conn:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS paper_board_history ON paper_trades(version_id,created_at DESC,id DESC)"
            )
        from .quiet_board import QuietBoard

        QuietBoard(store, None).preferences()
        Settings.model_validate(store.settings())
        market = Market(
            store,
            market_enabled
            if market_enabled is not None
            else os.getenv("BSB_MARKET_ENABLED", "1") == "1",
        )
        private = PrivateSync(
            store,
            credential_path
            or (database_path.parent / ".env" if database_path else ROOT / ".env"),
        )
        engine = Engine(store, market)
        engine.private_guard = private.guard
        app.state.private = private
        app.state.orders = ManualOrders(store, market, private, engine)
        private.orders = app.state.orders
        app.state.orders.live_execution_disabled = True
        app.state.automation = Automation(app.state.orders)
        engine.private_guard = app.state.automation.account_guard
        with store.connect() as conn:
            conn.execute(
                "UPDATE trading_control SET state='STOPPED',mode='MANUAL_APPROVAL',updated_at=? WHERE id=1",
                (now(),),
            )
        app.state.storage = store
        app.state.market = market
        app.state.engine = engine
        with store.connect() as conn:
            conn.execute(
                "UPDATE manual_orders SET status='EXPIRED',updated_at=? WHERE status='PREVIEW'",
                (now(),),
            )
            conn.execute(
                "UPDATE auto_signal_claims SET status='INTERRUPTED',message='프로그램 재시작' WHERE status='CLAIMED'"
            )
            conn.execute(
                "UPDATE lab_jobs SET status='INTERRUPTED',error='프로그램 재시작으로 중단됨' WHERE status IN ('QUEUED','RUNNING')"
            )
        async with market.start(), private.start():
            evaluator = asyncio.create_task(engine.run())
            order_monitor = asyncio.create_task(app.state.orders.run())
            automation_runner = asyncio.create_task(app.state.automation.run())
            try:
                yield
            finally:
                evaluator.cancel()
                order_monitor.cancel()
                automation_runner.cancel()
                for task in jobs:
                    task.cancel()
                await asyncio.gather(
                    evaluator,
                    order_monitor,
                    automation_runner,
                    *jobs,
                    return_exceptions=True,
                )

    app = FastAPI(title="Bithumb Manual Trading Strategy Lab", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type", "X-BSB-Local"],
    )

    @app.middleware("http")
    async def guard(request, call_next):
        if request.method in ("POST", "PATCH", "DELETE"):
            if (
                request.headers.get("origin") not in allowed
                or request.headers.get("x-bsb-local") != "1"
            ):
                return JSONResponse(
                    {"error": "local origin and X-BSB-Local required"}, status_code=403
                )
            if (
                request.headers.get("content-type", "").split(";")[0]
                != "application/json"
            ):
                return JSONResponse(
                    {"error": "application/json required"}, status_code=415
                )
        return await call_next(request)

    @app.exception_handler(ValueError)
    @app.exception_handler(ArithmeticError)
    @app.exception_handler(KeyError)
    async def invalid(request, error):
        return JSONResponse({"error": decimal_text(error)}, status_code=422)

    async def body(request):
        raw = await request.body()
        if len(raw) > 65536:
            raise HTTPException(413, "request too large")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("JSON object required")
        return data

    def engine():
        return app.state.engine

    def market():
        return app.state.market

    def signal_rows(limit=50):
        with store.connect() as conn:
            rows = [
                json.loads(row[0])
                for row in conn.execute(
                    """SELECT payload FROM signals ORDER BY CASE WHEN expires_at>? THEN CASE type WHEN 'RISK_EXIT' THEN 0 WHEN 'SELL_CANDIDATE' THEN 1 WHEN 'BUY_CANDIDATE' THEN 2 ELSE 3 END ELSE 4 END,generated_at DESC LIMIT ?""",
                    (now(), limit),
                )
            ]
        return signal_status(rows)

    def signal_status(rows):
        risk = engine().risk()
        for signal in rows:
            fresh = datetime.fromisoformat(signal["expires_at"]) > datetime.now(UTC)
            if signal["type"] == "BUY_CANDIDATE":
                fresh = (
                    fresh
                    and risk["buy_signals_enabled"]
                    and market().safe(signal["symbol"])[0]
                    and signal["strategy_version"] == risk["active_version"]
                )
            elif signal["type"] != "STALE":
                fresh = fresh and market().quote(signal["symbol"]) is not None
            signal["status"] = "ACTIVE" if fresh else "INVALIDATED"
        return rows

    @app.get("/api/health")
    def health():
        with store.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        return {
            "status": "ok",
            "mode": "MANUAL LIVE",
            "data_status": "WEBSOCKET" if market().enabled else "OFFLINE",
            "signals_enabled": engine().risk()["buy_signals_enabled"],
            "orders_supported": True,
            "manual_orders_active": app.state.orders.active,
            "auto_orders_supported": True,
            "auto_orders_active": app.state.automation.active,
            "credentials_required": False,
            "schema_version": version,
            "server_time": datetime.now(UTC).isoformat(),
        }

    @app.exception_handler(PrivateError)
    async def private_error(request, error):
        return JSONResponse({"error": str(error), "code": error.code}, status_code=503)

    @app.get("/api/private/status")
    def private_status():
        return {
            **app.state.private.status(),
            "read_only": not app.state.orders.active,
            "live_order_enabled": app.state.orders.active,
        }

    @app.get("/api/private/accounts")
    def private_accounts():
        return {
            "items": app.state.private.rows("private_accounts"),
            "strategy_capital_krw": store.settings()["initial_capital_krw"],
            "allocated_to_strategy": False,
        }

    @app.get("/api/private/orders")
    def private_orders():
        return {"items": app.state.private.rows("private_orders")}

    @app.get("/api/private/journal")
    def private_journal(day: str | None = None):
        if day is not None:
            datetime.strptime(day, "%Y-%m-%d")
        return {
            "items": app.state.private.rows("private_fills", day=day),
            "timezone": "Asia/Seoul",
            "limit": 200,
            "merged_into_manual_ledger": False,
        }

    @app.get("/api/private/chance")
    async def private_chance(market: str):
        if not re.fullmatch(r"KRW-[A-Z0-9]+", market):
            raise ValueError("invalid market")
        private = app.state.private
        if private.client is None:
            raise PrivateError("network_error")
        try:
            data = await private.client.get("/v1/orders/chance", {"market": market})
        except PrivateError as error:
            private.error = error.code
            private.rest_at = None
            raise
        # Only whitelisted numeric values, never raw exchange fields.
        result = {
            k: number(data[k])
            for k in ("bid_fee", "ask_fee", "maker_bid_fee", "maker_ask_fee")
            if k in data
        }
        result["market"] = market
        rules = data.get("market", {})
        for side in ("bid", "ask"):
            result[side] = {
                k: number(rules.get(side, {})[k])
                for k in ("min_total", "price")
                if rules.get(side, {}).get(k) is not None
            }
        if rules.get("max_total") is not None:
            result["max_total"] = number(rules["max_total"])
        return result

    @app.post("/api/private/sync")
    async def private_refresh(request: Request):
        await body(request)
        app.state.private.wake.set()
        return {"queued": app.state.private.client is not None, "orders_sent": False}

    @app.post("/api/private/reconcile")
    async def reconcile_private(request: Request):
        data = await body(request)
        private = app.state.private
        status = private.status()
        if (
            data.get("approval") != "계좌·전략원장 대조 완료"
            or not status["rest_fresh"]
            or not status["ws_connected"]
            or not status["history_complete"]
            or status["error_code"]
            or status["pending_orders"]
        ):
            raise HTTPException(
                409, "최신 계좌·체결 동기화 및 명시적 원장 대조가 필요합니다."
            )
        # Check registered strategy holdings/cash against available actual balances.
        balances = {
            r["currency"]: Decimal(r["balance"]) + Decimal(r["locked"])
            for r in private.rows("private_accounts")
        }
        ledger = engine().ledger()
        if any(
            Decimal(p["quantity"]) > balances.get(p["symbol"].split("-")[1], Decimal(0))
            for p in ledger["positions"]
        ):
            raise HTTPException(409, "전략 보유 수량이 실제 계좌 수량을 초과합니다.")
        available_krw = next(
            (
                Decimal(r["balance"])
                for r in private.rows("private_accounts")
                if r["currency"] == "KRW"
            ),
            Decimal(0),
        )
        if Decimal(ledger["cash_krw"]) > available_krw:
            raise HTTPException(409, "전략 현금이 실제 주문 가능 KRW를 초과합니다.")
        with store.connect() as conn:
            conn.execute(
                "UPDATE private_state SET reconciled=1 WHERE account_id=?",
                (private.account,),
            )
            store.audit(conn, "PRIVATE_LEDGER_RECONCILED", {"manual_allocation": True})
        return {"reconciled": True, "orders_sent": False}

    @app.post("/api/private/notes")
    async def private_note(request: Request):
        data = await body(request)
        tid, note = data.get("trade_id"), data.get("notes")
        if not isinstance(tid, str) or not isinstance(note, str) or len(note) > 2000:
            raise ValueError("invalid note")
        with store.connect() as conn:
            if not conn.execute(
                "SELECT 1 FROM private_fills WHERE account_id=? AND trade_id=?",
                (app.state.private.account, tid),
            ).fetchone():
                raise HTTPException(404, "체결 없음")
            conn.execute(
                "INSERT INTO private_notes VALUES(?,?,?) ON CONFLICT(account_id,trade_id) DO UPDATE SET notes=excluded.notes",
                (app.state.private.account, tid, note),
            )
        return {"saved": True}

    @app.get("/api/trading/control")
    def trading_control():
        with store.connect() as conn:
            state = dict(
                conn.execute(
                    "SELECT mode,state,updated_at FROM trading_control WHERE id=1"
                ).fetchone()
            )
        return {
            **state,
            "execution_mode": "LIVE_TRADING",
            "live_order_enabled": app.state.orders.active,
            "order_transport_implemented": True,
            "initial_capital_krw": "150000",
            "max_entry_krw": "50000",
            "daily_loss_limit_krw": "3000",
            "automatic_size_increase": False,
        }

    @app.post("/api/trading/control")
    async def change_control(request: Request):
        data = await body(request)
        mode = data.get("mode")
        action = data.get("action")
        if action == "start":
            raise HTTPException(
                409,
                "전역 자동 시작은 지원하지 않습니다. 직접 주문 탭에서 수동 주문을 활성화하세요.",
            )
        if mode is not None and mode not in ("MANUAL_APPROVAL", "AUTO_APPROVAL"):
            raise ValueError("invalid planning mode")
        if action not in (None, "stop", "emergency-stop"):
            raise ValueError("invalid action")
        app.state.orders.stop()
        with store.connect() as conn:
            current = conn.execute(
                "SELECT mode FROM trading_control WHERE id=1"
            ).fetchone()[0]
            conn.execute(
                "UPDATE trading_control SET mode=?,state=?,updated_at=? WHERE id=1",
                (
                    mode or current,
                    "EMERGENCY_STOP" if action == "emergency-stop" else "STOPPED",
                    now(),
                ),
            )
            store.audit(
                conn,
                "TRADING_DESIGN_CONTROL",
                {"mode": mode or current, "action": action, "orders_sent": False},
            )
        return {**trading_control(), "cancel_attempted": False}

    from .review_api import register_review

    register_review(app, store, body)
    from .quiet_board import register_board

    register_board(app, store, body)

    @app.get("/api/automation/status")
    def automation_status():
        return app.state.automation.state()

    @app.post("/api/automation/activate")
    async def automation_activate(request: Request):
        data = await body(request)
        async with app.state.automation.lock:
            async with app.state.orders.lock:
                return app.state.automation.activate(
                    data.get("approval"),
                    data.get("version"),
                    data.get("budget_krw"),
                    data.get("permissions_confirmed"),
                )

    @app.post("/api/automation/stop")
    async def automation_stop(request: Request):
        await body(request)
        # Stop immediately, including while a transport is waiting for its rate limit.
        return app.state.automation.stop()

    @app.get("/api/history/{kind}")
    def paged_history(
        kind: str,
        page: int = Query(1, ge=1, le=1000000),
        page_size: int = Query(10, ge=1, le=50),
        before: str | None = None,
        day: str | None = None,
    ):
        if kind == "trades":
            at = history_snapshot(before)
            rows = [
                r for r in engine().ledger()["executions"] if r["recorded_at"] <= at
            ]
            rows.sort(key=lambda r: (r["recorded_at"], r["id"]), reverse=True)
            total = len(rows)
            page = min(page, max(1, (total + page_size - 1) // page_size))
            return envelope(
                rows[(page - 1) * page_size : page * page_size],
                total,
                page,
                page_size,
                at,
            )
        result = history(
            store, app.state.private.account, kind, page, page_size, before, day
        )
        if kind == "signals":
            result["items"] = signal_status(result["items"])
        return result

    @app.get("/api/orders/status")
    def manual_order_status():
        return app.state.orders.state()

    @app.post("/api/orders/activate")
    async def manual_order_activate(request: Request):
        data = await body(request)
        return app.state.orders.activate(data.get("approval"))

    @app.post("/api/orders/stop")
    async def manual_order_stop(request: Request):
        await body(request)
        app.state.orders.stop()
        return app.state.orders.state()

    @app.post("/api/orders/preview")
    async def manual_order_preview(request: Request):
        data = await body(request)
        if not isinstance(data.get("market"), str) or not re.fullmatch(
            r"KRW-[A-Z0-9]+", data["market"]
        ):
            raise ValueError("invalid market")
        return await app.state.orders.preview(
            data["market"], data["side"], data["value"], data.get("signal_id")
        )

    @app.post("/api/orders/submit")
    async def manual_order_submit(request: Request):
        data = await body(request)
        return await app.state.orders.submit(data.get("id"), data.get("confirmation"))

    @app.post("/api/orders/recover")
    async def manual_order_recover(request: Request):
        await body(request)
        async with app.state.orders.lock:
            await app.state.orders.recover()
        return app.state.orders.state()

    @app.get("/api/settings")
    def settings():
        return store.settings()

    @app.patch("/api/settings")
    async def change_settings(request: Request):
        patch = await body(request)
        if not patch:
            raise ValueError("empty patch")
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            before = {
                row["key"]: json.loads(row["value"])
                for row in conn.execute("SELECT key,value FROM settings")
            }
            if (
                any(
                    k in patch and patch[k] != before.get(k)
                    for k in ("execution_fee_rate", "execution_slippage_rate")
                )
                and patch.get("costs_confirmed") is not True
            ):
                patch["costs_confirmed"] = False
            updated = Settings.model_validate({**before, **patch}).model_dump()
            at = datetime.now(UTC).isoformat()
            for key, value in updated.items():
                conn.execute(
                    "UPDATE settings SET value=?,updated_at=? WHERE key=?",
                    (json.dumps(value), at, key),
                )
            store.audit(conn, "settings_changed", {"before": before, "after": updated})
        return updated

    @app.get("/api/market/status")
    def market_status():
        return {
            **market().status(),
            "new_buy_signals_enabled": engine().risk()["buy_signals_enabled"],
        }

    @app.get("/api/market/scanner")
    def scanner_rows(
        page: int = Query(1, ge=1, le=100000),
        page_size: int = Query(20, ge=1, le=100),
        candidates_only: bool = False,
    ):
        rows = [r for r in market().rows if not candidates_only or r["candidate"]]
        total = len(rows)
        page = min(page, max(1, (total + page_size - 1) // page_size))
        return {
            **envelope(
                rows[(page - 1) * page_size : page * page_size],
                total,
                page,
                page_size,
                now(),
            ),
            "status": market().status()["scanner"],
            "live_ranking": True,
        }

    @app.get("/api/market/candidates")
    def candidates():
        return {
            "source": "bithumb_public" if market().enabled else "OFFLINE",
            "items": market().candidates()[:5],
            "experimental": True,
        }

    @app.get("/api/market/candles/{symbol}")
    def candles(
        symbol: str, timeframe: int = 60, limit: int = Query(200, ge=1, le=5000)
    ):
        if timeframe not in (60, 180, 300) or not re.fullmatch(
            r"KRW-[A-Z0-9]+", symbol
        ):
            raise ValueError("invalid symbol/timeframe")
        return {
            "items": market().book.load(symbol, timeframe, limit, complete=False),
            "source": "bithumb_public",
        }

    @app.get("/api/signals")
    def signals(limit: int = Query(50, ge=1, le=200)):
        return {
            "items": signal_rows(limit),
            "enabled": engine().risk()["buy_signals_enabled"],
        }

    @app.get("/api/positions")
    def positions():
        data = engine().ledger()
        policy = store.settings()
        for p in data["positions"]:
            p["stop_reference"] = decimal_text(
                Decimal(p["average_entry_price"])
                * (1 - Decimal(policy["stop_loss_rate"]))
            )
            p["target_reference"] = decimal_text(
                Decimal(p["average_entry_price"])
                * (1 + Decimal(policy["take_profit_rate"]))
            )
            p["net_estimate_krw"] = (
                decimal_text(
                    Decimal(p["unrealized_before_exit_fee_krw"])
                    - Decimal(p["quantity"])
                    * Decimal(p["mark_price"])
                    * Decimal(policy["execution_fee_rate"])
                )
                if policy["costs_confirmed"] and p["mark_price"]
                else None
            )
        return {**data, "items": data["positions"], "manual_fill_entry_supported": True}

    @app.get("/api/trades")
    def trades():
        return {
            "items": engine().ledger()["executions"],
            "manual_fill_entry_supported": True,
        }

    @app.post("/api/manual-fills")
    async def fill(request: Request):
        data = await body(request)
        if (
            set(data)
            - {
                "id",
                "symbol",
                "side",
                "quantity",
                "price",
                "fee_krw",
                "filled_at",
                "notes",
                "signal_id",
                "paper_trade_id",
                "confirmed",
            }
            or data.get("confirmed") is not True
        ):
            raise ValueError("사용자가 실제 체결을 확인해야 합니다.")
        identifier = data.get("id")
        symbol = data.get("symbol", "")
        side = data.get("side")
        if (
            not isinstance(identifier, str)
            or not 1 <= len(identifier) <= 100
            or not re.fullmatch(r"KRW-[A-Z0-9]+", symbol)
            or side not in ("BUY", "SELL")
        ):
            raise ValueError("invalid fill envelope")
        for key in ("quantity", "price", "fee_krw"):
            amount(data.get(key), zero=key == "fee_krw")
        filled = time_at(data["filled_at"]).isoformat()
        notes = data.get("notes", "")
        if not isinstance(notes, str) or len(notes) > 2000:
            raise ValueError("notes too long")
        normalized = {
            "id": identifier,
            "symbol": symbol,
            "side": side,
            "quantity": data["quantity"],
            "price": data["price"],
            "fee_krw": data["fee_krw"],
            "filled_at": filled,
            "notes": notes,
        }
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            paper_link = None
            paper_id = data.get("paper_trade_id")
            if paper_id is not None and (
                not isinstance(paper_id, str)
                or not re.fullmatch(r"[a-f0-9]{64}", paper_id)
            ):
                raise ValueError("유효한 가상 체결 ID가 필요합니다.")
            if paper_id:
                if data.get("signal_id"):
                    raise ValueError(
                        "실전 신호와 가상 체결 귀속을 동시에 지정할 수 없습니다."
                    )
                paper_row = conn.execute(
                    "SELECT payload FROM paper_trades WHERE id=?", (paper_id,)
                ).fetchone()
                paper = json.loads(paper_row[0]) if paper_row else None
                if (
                    not paper
                    or paper.get("simulated") is not True
                    or paper.get("channel") != "PAPER"
                    or paper.get("symbol") != symbol
                    or paper.get("side") != side
                ):
                    raise ValueError(
                        "종목·매수/매도 방향이 일치하는 실제 저장된 가상 체결을 선택하세요."
                    )
                if datetime.fromisoformat(filled) < datetime.fromisoformat(
                    (paper.get("signal_at") or paper["at"]).replace("Z", "+00:00")
                ):
                    raise ValueError(
                        "가상 신호 발생 이전의 실제 체결을 수동 추종 결과로 연결할 수 없습니다."
                    )
                paper_link = {
                    "paper_trade_id": paper_id,
                    "version_id": paper["version_id"],
                    "symbol": symbol,
                    "side": side,
                }
            existing = conn.execute(
                "SELECT * FROM manual_fills WHERE id=?", (identifier,)
            ).fetchone()
            if existing:
                if any(existing[key] != value for key, value in normalized.items()):
                    raise ValueError("같은 등록 ID에 다른 체결 내용")
                previous = conn.execute(
                    "SELECT payload FROM decision_events WHERE entity_type='PAPER_FOLLOW_LINK' AND entity_id=?",
                    (identifier,),
                ).fetchone()
                if (json.loads(previous[0]) if previous else None) != paper_link:
                    raise ValueError(
                        "이미 기록한 실제 체결의 가상 전략 귀속은 변경할 수 없습니다."
                    )
                return {"id": identifier, "duplicate": True}
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM manual_fills ORDER BY filled_at,recorded_at,id"
                )
            ]
            if rows and filled < rows[-1]["filled_at"]:
                raise ValueError(
                    "체결 시각은 마지막 등록 이후여야 합니다. 과거 정정은 원장 대조가 필요합니다."
                )
            recorded = datetime.now(UTC).isoformat()
            normalized["recorded_at"] = recorded
            result = rebuild(
                [*rows, normalized], store.settings()["initial_capital_krw"]
            )
            signal_id = data.get("signal_id")
            if (
                signal_id
                and not conn.execute(
                    "SELECT 1 FROM signals WHERE id=? AND symbol=?", (signal_id, symbol)
                ).fetchone()
            ):
                raise ValueError("signal not found for this symbol")
            conn.execute(
                "INSERT INTO manual_fills VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    symbol,
                    side,
                    data["quantity"],
                    data["price"],
                    data["fee_krw"],
                    filled,
                    recorded,
                    notes,
                ),
            )
            conn.execute(
                "INSERT INTO fill_links VALUES (?,?,?)",
                (identifier, signal_id, "MANUAL"),
            )
            from .review_events import append_event, capture_fill

            if paper_link:
                append_event(
                    conn, "PAPER_FOLLOW_LINK", identifier, symbol, recorded, paper_link
                )

            capture_fill(
                conn,
                identifier,
                symbol,
                filled,
                side,
                data["quantity"],
                data["price"],
                data["fee_krw"],
                source="MANUAL_RECORDED",
            )
            conn.execute("DELETE FROM positions")
            for p in result["positions"]:
                conn.execute(
                    "INSERT INTO positions VALUES (?,?,?,?)",
                    (p["symbol"], p["quantity"], p["average_entry_price"], recorded),
                )
            conn.execute("UPDATE private_state SET reconciled=0")
            store.audit(
                conn, "user_confirmed_fill", {**normalized, "signal_id": signal_id}
            )
        market().seed_later(symbol)
        return {
            "id": identifier,
            "duplicate": False,
            "cash_krw": result["cash_krw"],
            "balance_warning": Decimal(result["cash_krw"]) < 0,
        }

    @app.get("/api/risk/status")
    def risk():
        return engine().risk()

    @app.get("/api/lab/overview")
    def lab_overview():
        with store.connect() as conn:
            datasets = [
                {k: r[k] for k in ("id", "symbol", "created_at", "source")}
                for r in conn.execute(
                    "SELECT * FROM lab_datasets ORDER BY created_at DESC LIMIT 20"
                )
            ]
            paper = [
                {**dict(r), "position": json.loads(r["position"])}
                for r in conn.execute("SELECT * FROM paper_accounts")
            ]
            jobs_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id,status,error,created_at FROM lab_jobs ORDER BY created_at DESC LIMIT 10"
                )
            ]
        return {
            "versions": engine().lab.versions(),
            "active_version": engine().lab.active(),
            "datasets": datasets,
            "reports": [
                {
                    **r,
                    "training": {
                        k: v
                        for k, v in r["training"].items()
                        if k not in ("trades", "equity_curve")
                    },
                    "validation": {
                        k: v
                        for k, v in r["validation"].items()
                        if k not in ("trades", "equity_curve")
                    },
                }
                for r in engine().lab.reports()
            ],
            "paper_accounts": paper,
            "jobs": jobs_rows,
            "paper_initial_capital": "150000",
            "manual_initial_capital": store.settings()["initial_capital_krw"],
            "comparison": engine().lab.compare(),
            "automatic_promotion": False,
        }

    @app.post("/api/lab/versions")
    async def add_version(request: Request):
        data = await body(request)
        return {"id": engine().lab.add_version(data["kind"], data["params"])}

    @app.post("/api/lab/datasets")
    async def dataset(request: Request):
        data = await body(request)
        symbol = data.get("symbol", "")
        if not re.fullmatch(r"KRW-[A-Z0-9]+", symbol):
            raise ValueError("invalid symbol")
        bars = market().book.load(symbol, limit=5000)
        chunks: list[list[dict]] = []
        current: list[dict] = []
        for b in bars:
            if not b.get("complete") or not b.get("closed"):
                continue
            if current and b["start"] - current[-1]["start"] != 60:
                if len(current) >= 40:
                    chunks.append(current)
                current = []
            current.append(b)
        if len(current) >= 40:
            chunks.append(current)
        if not chunks:
            raise ValueError("40개 이상의 연속 완료 1분봉이 없습니다.")
        chosen = max(chunks, key=len)
        return {
            "id": engine().lab.dataset(symbol, chosen),
            "candles_count": len(chosen),
        }

    async def job(identifier, data):
        async with semaphore:
            with store.connect() as conn:
                conn.execute(
                    "UPDATE lab_jobs SET status='RUNNING' WHERE id=?", (identifier,)
                )
            try:
                if data.get("operation") == "history":
                    symbol = data.get("symbol", "")
                    pages = data.get("pages", 5)
                    if (
                        not re.fullmatch(r"KRW-[A-Z0-9]+", symbol)
                        or type(pages) is not int
                        or not 1 <= pages <= 25
                    ):
                        raise ValueError("invalid history request")
                    cursor = None
                    loaded = 0
                    for _ in range(pages):
                        query = {"market": symbol, "count": 200}
                        if cursor:
                            query["to"] = cursor
                        raw = await market().request("/v1/candles/minutes/1", query)
                        if not raw:
                            break
                        loaded += len(
                            market().book.seed(
                                symbol, raw, datetime.now(UTC).timestamp()
                            )
                        )
                        oldest = min(row["candle_date_time_utc"] for row in raw)
                        cursor = (
                            datetime.fromisoformat(oldest)
                            .replace(tzinfo=UTC)
                            .astimezone(ZoneInfo("Asia/Seoul"))
                            .strftime("%Y-%m-%dT%H:%M:%S")
                        )
                    report = {
                        "operation": "history",
                        "symbol": symbol,
                        "loaded": loaded,
                        "source": "bithumb_public",
                    }
                else:
                    report = await asyncio.to_thread(
                        engine().lab.evaluate,
                        data["version_id"],
                        data["dataset_id"],
                        data.get("fee_rate", "0.0004"),
                        data.get("slippage", "0.001"),
                        data.get("grid"),
                    )
                with store.connect() as conn:
                    conn.execute(
                        "UPDATE lab_jobs SET status='DONE',result=? WHERE id=?",
                        (json.dumps(report), identifier),
                    )
            except Exception as error:
                with store.connect() as conn:
                    conn.execute(
                        "UPDATE lab_jobs SET status='FAILED',error=? WHERE id=?",
                        (decimal_text(error), identifier),
                    )

    @app.post("/api/lab/runs")
    async def lab_run(request: Request):
        data = await body(request)
        if len(jobs) >= 5:
            raise HTTPException(429, "lab queue full")
        identifier = decimal_text(uuid4())
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO lab_jobs VALUES (?,?,?,?,?,?)",
                (
                    identifier,
                    "QUEUED",
                    json.dumps(data),
                    None,
                    None,
                    datetime.now(UTC).isoformat(),
                ),
            )
        task = asyncio.create_task(job(identifier, data))
        jobs.add(task)
        task.add_done_callback(jobs.discard)
        return {"job_id": identifier, "status": "QUEUED"}

    @app.get("/api/lab/jobs/{identifier}")
    def lab_job(identifier: str):
        with store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM lab_jobs WHERE id=?", (identifier,)
            ).fetchone()
        if not row:
            raise HTTPException(404, "job not found")
        return {
            **dict(row),
            "result": json.loads(row["result"]) if row["result"] else None,
        }

    @app.post("/api/lab/promote")
    async def promote(request: Request):
        data = await body(request)
        return {
            "active_version": engine().lab.promote(
                data["version_id"], data["report_id"], data.get("approval")
            )
        }

    @app.post("/api/lab/pause")
    async def pause(request: Request):
        await body(request)
        with store.connect() as conn:
            conn.execute("DELETE FROM strategy_state WHERE key='active_version'")
            store.audit(conn, "user_paused_live_signals", {})
        return {"active_version": None}

    @app.websocket("/ws/stream")
    async def stream(ws: WebSocket):
        if ws.headers.get("origin") not in allowed or ws.headers.get("host", "").split(
            ":"
        )[0] not in ("localhost", "127.0.0.1"):
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            while True:
                try:
                    await ws.send_json(
                        {
                            "type": "snapshot",
                            "at": datetime.now(UTC).isoformat(),
                            "market": market_status(),
                            "candidates": candidates(),
                            "signals": signals(20),
                            "positions": positions(),
                            "risk": risk(),
                        }
                    )
                except (WebSocketDisconnect, RuntimeError):
                    raise
                except Exception:
                    pass
                await asyncio.sleep(1)
        except (WebSocketDisconnect, RuntimeError):
            pass

    return app


app = create_app()
