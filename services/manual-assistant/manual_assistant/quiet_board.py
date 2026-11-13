"""Read model over existing paper executions; never creates simulated or live orders."""

import json
import re
from datetime import UTC, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from fastapi import Query, Request

from .ledger import amount, decimal_math, decimal_text
from .trade_review import TradeReview, moment

KST = ZoneInfo("Asia/Seoul")
DEFAULTS = {
    "follow_version": None,
    "recent_seconds": 120,
    "stale_seconds": 600,
    "chase_rate": "0.01",
}
NAMES = {"breakout": "거래량 돌파", "pullback": "눌림목", "momentum": "추세 모멘텀"}


class QuietBoard:
    def __init__(self, store, market, account=None, engine=None):
        self.store, self.market, self.account = store, market, account
        self.engine = engine

    def preferences(self):
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT value FROM strategy_state WHERE key='quiet_board'"
            ).fetchone()
            if row:
                return {**DEFAULTS, **json.loads(row[0])}
            version = conn.execute(
                "SELECT id FROM strategy_versions ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            prefs = {**DEFAULTS, "follow_version": version[0] if version else None}
            conn.execute(
                "INSERT OR IGNORE INTO strategy_state VALUES('quiet_board',?)",
                (json.dumps(prefs),),
            )
        return prefs

    def save_preferences(self, patch):
        if set(patch) - set(DEFAULTS):
            raise ValueError("지원하지 않는 신호판 설정입니다.")
        prefs = {**self.preferences(), **patch}
        if (
            type(prefs["recent_seconds"]) is not int
            or type(prefs["stale_seconds"]) is not int
            or not 10 <= prefs["recent_seconds"] < prefs["stale_seconds"] <= 86400
        ):
            raise ValueError(
                "최근 기준은 10초 이상, 오래된 기준은 최근 기준 이후~86400초입니다."
            )
        if not D("0.001") <= amount(prefs["chase_rate"]) <= D("0.5"):
            raise ValueError("추격 주의 가격 변동 기준은 0.1~50%입니다.")
        with self.store.connect() as conn:
            if not conn.execute(
                "SELECT 1 FROM strategy_versions WHERE id=?", (prefs["follow_version"],)
            ).fetchone():
                raise ValueError("실제 저장된 전략 버전을 선택하세요.")
            conn.execute(
                "INSERT INTO strategy_state VALUES('quiet_board',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(prefs),),
            )
            self.store.audit(conn, "QUIET_BOARD_PREFERENCES", prefs)
        return prefs

    def mark(self, symbol):
        latest = self.market.latest.get(symbol, {})
        choices = [
            r
            for kind, r in list(latest.items())
            if kind in ("trade", "ticker") and r.get("price")
        ]
        row = max(choices, key=lambda r: moment(r["received_at"])) if choices else None
        if not row:
            return {"price": None, "last_price": None, "at": None, "fresh": False}
        age = (datetime.now(UTC) - moment(row["received_at"])).total_seconds()
        lag = (moment(row["received_at"]) - moment(row["exchange_at"])).total_seconds()
        connected = getattr(self.market, "connections", {}).get(symbol, False)
        fresh = (
            connected
            and 0 <= age <= 15
            and -1 <= lag <= 15
            and row.get("stream_type") == "REALTIME"
        )
        return {
            "price": row["price"] if fresh else None,
            "last_price": row["price"],
            "at": row["received_at"],
            "fresh": fresh,
        }

    def coin_name(self, symbol):
        meta = getattr(self.market, "market_metadata", {}).get(symbol, {})
        return meta.get("korean_name") or symbol.removeprefix("KRW-")

    @decimal_math
    def trade(self, row, prefs):
        t = json.loads(row["payload"])
        if t.get("simulated") is not True or t.get("channel") != "PAPER":
            return None
        mark = self.mark(t["symbol"])
        price = t.get("fill_price")
        derived_price = False
        if not price and t.get("quantity") and t.get("fee_krw") is not None:
            funds = t.get("cost_krw") if t["side"] == "BUY" else t.get("proceeds")
            if funds is not None:
                price = decimal_text(
                    (D(funds) + D(t["fee_krw"]) * (1 if t["side"] == "SELL" else -1))
                    / D(t["quantity"])
                )
                derived_price = True

        elapsed = max(
            0,
            int(
                (
                    datetime.now(UTC) - moment(t.get("signal_at") or t["at"])
                ).total_seconds()
            ),
        )
        change = (D(mark["price"]) / D(price) - 1) if mark["price"] and price else None
        status = (
            "OLD"
            if elapsed >= prefs["stale_seconds"]
            else "CHASE_CAUTION"
            if elapsed > prefs["recent_seconds"]
            or change is not None
            and change >= D(prefs["chase_rate"])
            else "RECENT"
        )
        legacy_reason = {
            "stop": "손절 기준 도달",
            "target": "익절 기준 도달",
            "time": "보유 시간 기준 청산",
        }.get(t.get("reason"))
        return {
            **t,
            "coin_name": self.coin_name(t["symbol"]),
            "id": row["id"],
            "execution_label": "가상매수 체결"
            if t["side"] == "BUY"
            else "가상매도 체결",
            "fill_price": price,
            "derived_fill_price": derived_price,
            "elapsed_basis": "SIGNAL" if t.get("signal_at") else "FILL_FALLBACK",
            "current_price": mark["price"],
            "last_price": mark["last_price"],
            "market_at": mark["at"],
            "market_fresh": mark["fresh"],
            "price_change_rate": decimal_text(change) if change is not None else None,
            "elapsed_seconds": elapsed,
            "signal_status": status,
            "reason_summary": t.get("reason_summary")
            or legacy_reason
            or "이전 가상 체결 · 당시 상세 사유 미기록",
            "legacy_missing_execution_fields": not bool(price and t.get("quantity")),
            "actual_exchange_fill": False,
            "amount_krw": t.get("cost_krw")
            if t["side"] == "BUY"
            else t.get("proceeds"),
            "result": "PROFIT"
            if D(t.get("net_pnl", "0")) > 0
            else "LOSS"
            if D(t.get("net_pnl", "0")) < 0
            else "BREAKEVEN"
            if t["side"] == "SELL"
            else None,
        }

    @decimal_math
    def snapshot(self, buy_page=1, sell_page=1, page_size=5):
        prefs = self.preferences()
        version = prefs["follow_version"]
        with self.store.connect() as conn:
            # Capture account and executions in the same SQLite snapshot, not independently polled sources.
            conn.execute("BEGIN")
            account = conn.execute(
                "SELECT * FROM paper_accounts WHERE version_id=?", (version,)
            ).fetchone()
            versions = [
                {**dict(r), "params": json.loads(r["params"])}
                for r in conn.execute(
                    "SELECT * FROM strategy_versions ORDER BY created_at,id"
                )
            ]
            predicate = "version_id=? AND json_type(payload,'$.simulated')='true' AND json_extract(payload,'$.channel')='PAPER'"

            def read_page(side, number):
                total = conn.execute(
                    f"SELECT count(*) FROM paper_trades WHERE {predicate} AND json_extract(payload,'$.side')=?",
                    (version, side),
                ).fetchone()[0]
                pages = max(1, (total + page_size - 1) // page_size)
                number = min(number, pages)
                rows = conn.execute(
                    f"SELECT * FROM paper_trades WHERE {predicate} AND json_extract(payload,'$.side')=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                    (version, side, page_size, (number - 1) * page_size),
                ).fetchall()
                return {
                    "items": [
                        {
                            k: v
                            for k, v in self.trade(r, prefs).items()
                            if k not in ("basis", "strategy_snapshot", "reasons")
                        }
                        for r in rows
                    ],
                    "page": number,
                    "pages": pages,
                    "total": total,
                }

            buys = read_page("BUY", buy_page)
            sells = read_page("SELL", sell_page)
            entry_ids = {}
            for symbol in (
                json.loads(account["position"])["holdings"] if account else {}
            ):
                entry = conn.execute(
                    f"SELECT id FROM paper_trades WHERE {predicate} AND json_extract(payload,'$.side')='BUY' AND json_extract(payload,'$.symbol')=? ORDER BY created_at DESC,id DESC LIMIT 1",
                    (version, symbol),
                ).fetchone()
                entry_ids[symbol] = entry[0] if entry else None
            open_buy_ids = {v for v in entry_ids.values() if v}
            for item in buys["items"]:
                item["is_closed"] = item["id"] not in open_buy_ids

            daily = sum(
                (
                    D(r[0])
                    for r in conn.execute(
                        f"SELECT json_extract(payload,'$.net_pnl') FROM paper_trades WHERE {predicate} AND json_extract(payload,'$.side')='SELL' AND date(datetime(created_at,'+9 hours'))=?",
                        (version, datetime.now(KST).date().isoformat()),
                    )
                ),
                D(0),
            )
        holdings = []
        state = json.loads(account["position"])["holdings"] if account else {}
        for symbol, p in sorted(state.items()):
            mark = self.mark(symbol)
            qty, cost = D(p["quantity"]), D(p["cost"])
            net = (
                D(mark["price"]) * qty * D("0.999") * D("0.9996") - cost
                if mark["price"]
                else None
            )
            holdings.append(
                {
                    "id": version + ":" + symbol,
                    "entry_trade_id": entry_ids.get(symbol),
                    "symbol": symbol,
                    "coin_name": self.coin_name(symbol),
                    "quantity": p["quantity"],
                    "entry_price": p["entry_price"],
                    "current_price": mark["price"],
                    "last_price": mark["last_price"],
                    "market_fresh": mark["fresh"],
                    "market_at": mark["at"],
                    "peak_price": p.get("peak_price"),
                    "trail_active": bool(p.get("trail_active", False)),
                    "net_estimate_krw": decimal_text(net) if net is not None else None,
                    "net_return_rate": decimal_text(net / cost)
                    if net is not None and cost
                    else None,
                    "opened_at": datetime.fromtimestamp(
                        p["opened_at"], UTC
                    ).isoformat(),
                    "version_id": version,
                    "simulated": True,
                }
            )
        selected = next((v for v in versions if v["id"] == version), None)
        runtime = self.runtime(version)
        settings = self.store.settings()
        initial_cap = settings.get("initial_capital_krw", "150000")
        return {
            "runtime": runtime,
            "operation_mode": "QUIET_FOLLOW",
            "trading_mode": "PAPER",
            "approval_mode": "USER_EXTERNAL_MANUAL",
            "live_execution_allowed": False,
            "notifications": {"sound": False, "popup": False, "browser": False},
            "at": datetime.now(UTC).isoformat(),
            "preferences": prefs,
            "versions": versions,
            "selected_version": selected,
            "selected_strategy_name": NAMES.get(selected["kind"], selected["kind"])
            if selected
            else "전략 없음",
            "summary": {
                "today_realized_krw": decimal_text(daily),
                "holding_count": len(holdings),
                "initial_capital_krw": initial_cap,
                "cash_krw": account["cash"] if account else initial_cap,
            },
            "buys": buys,
            "sells": sells,
            "holdings": holdings,
            "simulation_costs": {
                "fee_rate": "0.0004",
                "slippage_rate": "0.001",
                "basis": "기존 가상 엔진의 고정 가정. 실제 거래소 수수료 아님",
            },
            "engine_changed": False,
            "reversion": (
                self.engine.reversion.summary()
                if getattr(self.engine, "reversion", None)
                else None
            ),
        }

    def runtime(self, version):
        engine = self.engine
        at = getattr(engine, "last_tick_at", None)
        age = (datetime.now(UTC) - moment(at)).total_seconds() if at else None
        connections = sum(
            bool(v) for v in getattr(self.market, "connections", {}).values()
        )
        rows = list(getattr(self.market, "rows", []))
        ready = sum(bool(r.get("ready")) for r in rows)
        diagnostic = getattr(engine, "paper_diagnostics", {}).get(version)
        error = getattr(engine, "last_error", None)
        if error:
            state, message = "ENGINE_ERROR", "전략 분석 오류 · 신규 가상매수 대기"
        elif age is None:
            state, message = "STARTING", "전략 엔진 시작·첫 분석 대기"
        elif age > 10 or age < 0:
            state, message = "ENGINE_STALLED", "전략 분석 응답 지연"
        elif not connections:
            state, message = "DATA_WAIT", "시세 연결 대기 · 신규 가상매수 대기"
        elif not ready:
            state, message = (
                "MARKET_WAIT",
                "정상 감시 중 · 시세·호가·캔들 안전검사를 통과한 후보 대기",
            )
        elif not diagnostic or not diagnostic.get("entries_scheduled"):
            state, message = "VERSION_WAIT", "선택한 전략의 신규 진입 분석 대기"
        else:
            state, message = (
                "WATCHING",
                "정상 감시 중 · 선택 전략의 매수 조건 충족 대기",
            )
        return {
            "state": state,
            "message": message,
            "last_analysis_at": at,
            "analysis_age_seconds": age,
            "connected_markets": connections,
            "ready_candidates": ready,
            "selected_check": diagnostic,
            "error_type": error,
            "running": state in ("WATCHING", "MARKET_WAIT", "VERSION_WAIT"),
        }

    def detail(self, identifier):
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM paper_trades WHERE id=?", (identifier,)
            ).fetchone()
        if not row:
            raise ValueError("가상 체결 기록이 없습니다.")
        trade = self.trade(row, self.preferences())
        if trade is None:
            raise ValueError("가상 체결이 아닌 기록입니다.")
        return trade

    @decimal_math
    def comparison(self):
        with self.store.connect() as conn:
            versions = [
                dict(r) for r in conn.execute("SELECT id,kind FROM strategy_versions")
            ]
            rows = conn.execute(
                "SELECT * FROM paper_trades ORDER BY created_at,id"
            ).fetchall()
        grouped = {
            v["id"]: {
                "version_id": v["id"],
                "name": NAMES.get(v["kind"], v["kind"]),
                "paper_sell_fills": 0,
                "paper_net_krw": D(0),
                "paper_fees_krw": D(0),
                "paper_slippage_krw": D(0),
                "live_sell_fills": 0,
                "live_net_krw": D(0),
            }
            for v in versions
        }
        for row in rows:
            t = json.loads(row["payload"])
            if t.get("simulated") is not True or t.get("channel") != "PAPER":
                continue
            g = grouped.get(row["version_id"])
            if g is None:
                continue
            g["paper_fees_krw"] += D(t.get("fee_krw", "0"))
            g["paper_slippage_krw"] += D(t.get("slippage_krw", "0"))
            if t["side"] == "SELL":
                g["paper_sell_fills"] += 1
                g["paper_net_krw"] += D(t["net_pnl"])
        for p in TradeReview(self.store, self.market, self.account).positions():
            if p["scope"] != "STRATEGY":
                continue
            for f in p["fills"]:
                g = grouped.get(f["strategy_version"])
                if g and f["side"] == "SELL" and f["realized_net_krw"] is not None:
                    g["live_sell_fills"] += 1
                    g["live_net_krw"] += D(f["realized_net_krw"])
        return {
            "items": [
                {
                    **{
                        k: decimal_text(v) if isinstance(v, D) else v
                        for k, v in g.items()
                    },
                    "paper_realized_return_rate": decimal_text(
                        g["paper_net_krw"] / D("150000")
                    ),
                }
                for g in grouped.values()
            ],
            "note": "가상 자본은 버전별 150,000원으로 독립됩니다. 실전은 실제 확인 등록 체결만 집계하며 미귀속·비용·수동 실행 지연 때문에 동일 조건의 성과가 아닙니다. 거래 수는 매도 체결 수입니다.",
        }


def register_board(app, store, body):
    def board():
        return QuietBoard(
            store, app.state.market, app.state.private.account, app.state.engine
        )

    @app.get("/api/board")
    def snapshot(
        buy_page: int = Query(1, ge=1),
        sell_page: int = Query(1, ge=1),
        page_size: int = Query(5, ge=1, le=20),
    ):
        return board().snapshot(buy_page, sell_page, page_size)

    @app.patch("/api/board/preferences")
    async def preferences(request: Request):
        return board().save_preferences(await body(request))

    @app.get("/api/board/comparison")
    def comparison():
        return board().comparison()

    @app.get("/api/board/trades/{identifier}")
    def detail(identifier: str):
        if not re.fullmatch("[a-f0-9]{64}", identifier):
            raise ValueError("invalid paper execution identifier")
        return board().detail(identifier)
