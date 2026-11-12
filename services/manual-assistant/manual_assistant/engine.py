import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

from .lab import Lab
from .ledger import decimal_math, decimal_text, rebuild
from .strategy import entry

D = Decimal
logger = logging.getLogger("bsb.manual.engine")


def sweep(
    levels, budget=None, quantity=None, fee=D("0.0004"), slip=D("0.001"), buy=True
):
    total = D(0)
    filled = D(0)
    with localcontext() as ctx:
        ctx.prec = 80
        for p, q in levels:
            price = D(p) * (1 + slip if buy else 1 - slip)
            available = D(q)
            take = (
                min(available, budget / (price * (1 + fee)))
                if buy
                else min(available, quantity)
            )
            if take <= 0:
                break
            value = take * price * (1 + fee if buy else 1 - fee)
            filled += take
            total += value
            if buy:
                budget -= value
            else:
                quantity -= take
        return filled, total


class Engine:
    def __init__(self, store, market):
        self.store = store
        self.market = market
        self.lab = Lab(store)
        self.last_error = None
        self.last_tick_at = None
        self.paper_diagnostics = {}
        self.paper_last_evaluations = {}
        self.performance_minute = None
        self.paper_entry_at = 0.0
        self.private_guard = lambda: []
        from .reversion import ReversionLab

        self.reversion = ReversionLab(store, market)

    def ledger(self):
        with self.store.connect() as conn:
            fills = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM manual_fills ORDER BY filled_at,recorded_at,id"
                )
            ]
        marks = {
            s: q["bids"][0][0]
            for s in list(self.market.latest)
            if (q := self.market.quote(s))
        }
        return rebuild(fills, self.store.settings()["initial_capital_krw"], marks)

    @decimal_math
    def risk(self):
        settings = self.store.settings()
        ledger = self.ledger()
        positions = ledger["positions"]
        reasons = list(self.private_guard())
        unreal = ledger["unrealized_before_exit_fee_krw"]
        fee = D(settings.get("execution_fee_rate", "0.0004"))
        exit_fees = sum(
            D(p["quantity"]) * D(p["mark_price"]) * fee
            for p in positions
            if p["mark_price"]
        )
        net = D(ledger["daily_realized_krw"]) + (
            D(unreal) - exit_fees if unreal is not None else 0
        )
        if unreal is None:
            reasons.append("보유 포지션의 신선한 평가 시세 없음")
        if net <= -D(settings["daily_loss_alert_krw"]):
            reasons.append("실현+현재평가 손실 한도")
        if ledger["consecutive_losses"] >= settings["consecutive_losses_limit"]:
            reasons.append("연속 손실 한도")
        if len(positions) >= settings["max_positions"]:
            reasons.append("보유 종목 한도")
        if D(ledger["cash_krw"]) - D(
            getattr(self.market, "analysis_budget", None) or settings["max_entry_krw"]
        ) < D(settings["minimum_cash_reserve_krw"]):
            reasons.append("현금 여유 부족 또는 체결/입출금 미확인")
        if not settings.get("costs_confirmed", False):
            reasons.append("수수료·슬리피지 가정 사용자 확인 필요")
        active = self.lab.active()
        if not active:
            reasons.append("사용자 승인된 실전 전략 없음")
        else:
            with self.store.connect() as conn:
                approval = conn.execute(
                    """SELECT r.created_at,r.report FROM promotion_history p JOIN lab_runs r ON p.report_id=r.id
                    WHERE p.version_id=? ORDER BY p.id DESC LIMIT 1""",
                    (active,),
                ).fetchone()
            if (
                not approval
                or (
                    datetime.now(UTC) - datetime.fromisoformat(approval[0])
                ).total_seconds()
                > 72 * 3600
            ):
                reasons.append("실전 전략 검증 유효기간 초과")
            elif approval:
                assumptions = json.loads(approval["report"])["validation"]
                fee_assumed = assumptions.get("fee_rate_assumption", "0.0004")
                slip_assumed = assumptions.get(
                    "slippage_assumption"
                ) or assumptions.get("slippage_rate_assumption", "0.001")
                if D(str(fee_assumed)) < D(settings["execution_fee_rate"]) or D(
                    str(slip_assumed)
                ) < D(settings["execution_slippage_rate"]):
                    reasons.append(
                        "검증한 비용보다 실전 비용 가정이 큽니다. 재검증 필요"
                    )
        if not any(self.market.safe(symbol)[0] for symbol in list(self.market.latest)):
            reasons.append("신선한 시장 데이터·초기 캔들 대기")
        if self.last_error:
            reasons.append("신호 엔진 오류")
        return {
            "buy_signals_enabled": not reasons,
            "position_monitoring_enabled": True,
            "account_confirmed": False,
            "reasons": reasons,
            "daily_realized_plus_current_estimate_krw": decimal_text(net),
            "valuation_complete": ledger["valuation_complete"],
            "capital_channel": "MANUAL",
            "initial_capital_krw": settings["initial_capital_krw"],
            "max_entry_krw": settings["max_entry_krw"],
            "automatic_size_increase": False,
            "active_version": active,
        }

    def signal(
        self,
        symbol,
        kind,
        version,
        price,
        reasons,
        key,
        *,
        basis=None,
        exit_reason=None,
    ):
        now = datetime.now(UTC)
        identifier = hashlib.sha256(key.encode()).hexdigest()
        payload = {
            "id": identifier,
            "symbol": symbol,
            "type": kind,
            "strategy_version": version,
            "reference_price": price,
            "reasons": reasons,
            "channel": "MANUAL",
            "source": "bithumb_public",
            "generated_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=30)).isoformat(),
            "data_at": self.market.latest.get(symbol, {})
            .get("orderbook", {})
            .get("exchange_at"),
            "invalidation": [
                "시세 15초 초과",
                "연결 단절",
                "스프레드 0.3% 초과",
                "신호 만료",
            ],
            "basis": basis,
            "order_execution": "사용자 직접 확인 후 앱 주문 또는 빗썸에서 실행",
        }
        policy = self.store.settings()
        risk_snapshot = self.risk()
        version_snapshot = next(
            (v for v in self.lab.versions() if v["id"] == version), None
        )
        with self.store.connect() as conn:
            inserted = conn.execute(
                "INSERT OR IGNORE INTO signals VALUES (?,?,?,?,?,?,?)",
                (
                    identifier,
                    symbol,
                    kind,
                    version,
                    json.dumps(payload),
                    payload["generated_at"],
                    payload["expires_at"],
                ),
            )
            if inserted.rowcount:
                from .review_events import capture_signal

                capture_signal(
                    conn,
                    self.market,
                    payload,
                    policy,
                    risk_snapshot,
                    version_snapshot,
                    exit_reason,
                )

    @decimal_math
    def paper(self, version, candidates, allow_entries=True):
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM paper_accounts WHERE version_id=?", (version["id"],)
            ).fetchone()
        initial_cap = self.store.settings().get("initial_capital_krw", "150000")
        cash = D(row["cash"]) if row else D(initial_cap)
        realized = D(row["realized"]) if row else D(0)
        state = json.loads(row["position"]) if row else {"holdings": {}, "seen": {}}
        holdings = state["holdings"]
        changed = row is None
        params = version["params"]
        pending_trades = []
        diagnostic = {
            "checked_at": datetime.now(UTC).isoformat(),
            "entries_scheduled": allow_entries,
            "eligible_candidates": sum(bool(c["ready"]) for c in candidates),
            "evaluated": 0,
            "reasons": {},
            "last_evaluations": list(
                self.paper_last_evaluations.get(version["id"], {}).values()
            )[-5:],
        }

        def waiting(reason):
            diagnostic["reasons"][reason] = diagnostic["reasons"].get(reason, 0) + 1

        for evaluation in diagnostic["last_evaluations"]:
            if not evaluation["passed"]:
                reason = evaluation["reasons"][0] if evaluation["reasons"] else "조건 미충족"
                waiting(f"최근 평가 · {evaluation['symbol']} · {reason}")

        symbols = set(holdings) | {
            item["symbol"] for item in candidates if item["ready"] and allow_entries
        }
        for symbol in sorted(symbols):
            safe, _ = self.market.safe(symbol)
            quote = self.market.quote(symbol)
            if not safe or not quote:
                waiting("시세·호가 안전검사 대기")
                continue
            if symbol in holdings:
                pos = holdings[symbol]
                bid = D(quote["bids"][0][0])
                avg = D(pos["entry_price"])
                peak = max(D(pos.get("peak_price", avg)), bid)
                if peak > D(pos.get("peak_price", avg)):
                    pos["peak_price"] = decimal_text(peak)
                    changed = True

                trail_active = bool(pos.get("trail_active", False))
                target_rate = D(params["take_profit"])
                trailing_drop = D(params.get("trailing_drop", "0.008"))

                if peak >= avg * (1 + target_rate) and not trail_active:
                    trail_active = True
                    pos["trail_active"] = True
                    changed = True

                reason = None
                if not trail_active and bid <= avg * (1 - D(params["stop_loss"])):
                    reason = "stop"
                elif trail_active and bid <= peak * (1 - trailing_drop):
                    reason = "target"
                elif time.time() - pos["opened_at"] >= params["max_bars"] * 60:
                    reason = "time"
                if reason:
                    qty, proceeds = sweep(
                        quote["bids"], quantity=D(pos["quantity"]), buy=False
                    )
                    if qty:
                        fraction = qty / D(pos["quantity"])
                        basis = D(pos["cost"]) * fraction
                        pnl = proceeds - basis
                        cash += proceeds
                        realized += pnl
                        pos["quantity"] = decimal_text(D(pos["quantity"]) - qty)
                        pos["cost"] = decimal_text(D(pos["cost"]) - basis)
                        if D(pos["quantity"]) == 0:
                            del holdings[symbol]
                        at = datetime.now(UTC).isoformat()
                        trade = {
                            "symbol": symbol,
                            "side": "SELL",
                            "quantity": decimal_text(qty),
                            "net_pnl": decimal_text(pnl),
                            "fill_price": decimal_text(proceeds / (qty * D("0.9996"))),
                            "signal_at": datetime.now(UTC).isoformat(),
                            "reason_summary": {
                                "stop": f"손절 기준 도달 (진입가 대비 -{decimal_text(D(params['stop_loss']) * 100)}%)",
                                "target": (
                                    f"트레일링 익절 청산 (고점 {decimal_text(peak)}원 대비 -{decimal_text(trailing_drop * 100)}% 꺾임)"
                                    if trail_active
                                    else "익절 기준 도달"
                                ),
                                "time": "보유 시간 기준 청산",
                            }[reason],
                            "strategy_snapshot": version,
                            "fee_rate": "0.0004",
                            "slippage_rate": "0.001",
                            "proceeds": decimal_text(proceeds),
                            "fee_krw": decimal_text(
                                proceeds * D("0.0004") / D("0.9996")
                            ),
                            "slippage_krw": decimal_text(
                                proceeds / D("0.9996") * D("0.001") / D("0.999")
                            ),
                            "reason": reason,
                            "version_id": version["id"],
                            "at": at,
                            "simulated": True,
                            "channel": "PAPER",
                        }
                        pending_trades.append(trade)
                        changed = True
                continue
            bars = self.market.book.load_contiguous(symbol, limit=100)
            if (
                not bars
                or bars[-1]["start"] <= state["seen"].get(symbol, 0)
                or time.time() - (bars[-1]["start"] + 60) > 120
            ):
                waiting("새 완료 캔들 대기")
                continue
            state["seen"][symbol] = bars[-1]["start"]
            changed = True
            if (
                realized <= -D("3000")
                or len(holdings) >= 2
                or cash - D("50000") < D("50000")
            ):
                waiting("손실·보유·현금 한도")
                continue
            diagnostic["evaluated"] += 1
            passed, entry_reasons = entry(version["kind"], bars, params)
            evaluations = self.paper_last_evaluations.setdefault(version["id"], {})
            evaluations[symbol] = {
                "symbol": symbol,
                "candle_start": bars[-1]["start"],
                "evaluated_at": datetime.now(UTC).isoformat(),
                "passed": passed,
                "reasons": entry_reasons,
            }
            diagnostic["last_evaluations"] = list(evaluations.values())[-5:]
            if not passed:
                waiting(entry_reasons[0] if entry_reasons else "매수 조건 미충족")
            if passed:
                signal_at = datetime.now(UTC).isoformat()
                qty, cost = sweep(quote["asks"], budget=D("50000"))
                if qty:
                    pending_trades.append(
                        {
                            "symbol": symbol,
                            "side": "BUY",
                            "quantity": decimal_text(qty),
                            "cost_krw": decimal_text(cost),
                            "fill_price": decimal_text(cost / (qty * D("1.0004"))),
                            "signal_at": signal_at,
                            "reasons": entry_reasons,
                            "reason_summary": " · ".join(entry_reasons),
                            "strategy_snapshot": version,
                            "basis": bars[-max(params["lookback"] + 1, 40) :],
                            "fee_rate": "0.0004",
                            "slippage_rate": "0.001",
                            "fee_krw": decimal_text(cost * D("0.0004") / D("1.0004")),
                            "slippage_krw": decimal_text(
                                cost / D("1.0004") * D("0.001") / D("1.001")
                            ),
                            "version_id": version["id"],
                            "at": datetime.now(UTC).isoformat(),
                            "simulated": True,
                            "channel": "PAPER",
                        }
                    )
                    cash -= cost
                    entry_price = decimal_text(cost / (qty * D("1.0004")))
                    holdings[symbol] = {
                        "quantity": decimal_text(qty),
                        "cost": decimal_text(cost),
                        "entry_price": entry_price,
                        "peak_price": entry_price,
                        "trail_active": False,
                        "opened_at": time.time(),
                    }
        if allow_entries or version["id"] not in self.paper_diagnostics:
            self.paper_diagnostics[version["id"]] = diagnostic
        if changed:
            with self.store.connect() as conn:
                for trade in pending_trades:
                    conn.execute(
                        "INSERT INTO paper_trades VALUES (?,?,?,?)",
                        (
                            hashlib.sha256(json.dumps(trade).encode()).hexdigest(),
                            version["id"],
                            json.dumps(trade),
                            trade["at"],
                        ),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO paper_accounts VALUES (?,?,?,?,?)",
                    (
                        version["id"],
                        decimal_text(cash),
                        json.dumps(state),
                        decimal_text(realized),
                        int(time.time()),
                    ),
                )

    @decimal_math
    def tick(self):
        started = time.perf_counter()
        policy = self.store.settings()
        ledger = self.ledger()
        risk = self.risk()
        versions = self.lab.versions()
        positions = {p["symbol"]: p for p in ledger["positions"]}
        for symbol, p in positions.items():
            if symbol not in self.market.seeds or symbol in self.market.dirty:
                self.market.seed_later(symbol)
            quote = self.market.quote(symbol)
            key = f"{symbol}:{p['quantity']}:{p['average_entry_price']}:{int(time.time()) // 60}"
            if not quote:
                self.signal(
                    symbol,
                    "STALE",
                    "manual-risk",
                    None,
                    ["시세 미수신/오래됨 · 손절 가격 판단 불가"],
                    "stale:" + key,
                )
                continue
            bid = D(quote["bids"][0][0])
            avg = D(p["average_entry_price"])
            spread = (D(quote["asks"][0][0]) - bid) / bid
            if bid <= avg * (1 - D(policy["stop_loss_rate"])) or spread > D("0.003"):
                self.signal(
                    symbol,
                    "RISK_EXIT",
                    "manual-risk",
                    decimal_text(bid),
                    ["손절 참고선 이탈 또는 유동성 악화"],
                    "risk:" + key,
                    exit_reason="STOP_LOSS"
                    if bid <= avg * (1 - D(policy["stop_loss_rate"]))
                    else "RISK_EXIT",
                )
            elif (
                bid >= avg * (1 + D(policy["take_profit_rate"]))
                or (
                    datetime.now(UTC) - datetime.fromisoformat(p["opened_at"])
                ).total_seconds()
                >= policy["max_holding_seconds"]
            ):
                self.signal(
                    symbol,
                    "SELL_CANDIDATE",
                    "manual-risk",
                    decimal_text(bid),
                    ["목표 참고선 또는 보유 시간 재평가"],
                    "sell:" + key,
                    exit_reason="TAKE_PROFIT"
                    if bid >= avg * (1 + D(policy["take_profit_rate"]))
                    else "TIME_EXIT",
                )
        with self.store.connect() as conn:
            paper_held = set()
            for row in conn.execute("SELECT position FROM paper_accounts"):
                paper_held.update(json.loads(row[0]).get("holdings", {}))
        if hasattr(self.market, "set_positions"):
            self.market.set_positions(set(positions), paper_held)
        candidates = self.market.candidates()
        # Observation alerts do not approve a strategy or execute any order.
        for item in candidates:
            if (
                not item["ready"]
                or item["symbol"] in positions
                or D(item["volume_ratio"]) < 1
            ):
                continue
            bars = self.market.book.load(item["symbol"], limit=30)
            if (
                len(bars) >= 2
                and time.time() - (bars[-1]["start"] + 60) <= 120
                and D(bars[-1]["close"]) > D(bars[-1]["open"])
                and D(bars[-1]["close"]) > D(bars[-2]["close"])
            ):
                self.signal(
                    item["symbol"],
                    "WATCH_ENTRY",
                    "observation-v1",
                    item["price"],
                    [
                        "관찰용: 완료 1분봉 상승·직전 종가 상향·평균 이상 거래량",
                        "미검증 관찰 신호 · 사용자 직접 판단",
                    ],
                    f"watch:{item['symbol']}:{bars[-1]['start']}",
                )
        active = next((v for v in versions if v["id"] == risk["active_version"]), None)
        slots = min(
            policy["max_positions"] - len(positions),
            max(
                0,
                int(
                    (D(ledger["cash_krw"]) - D(policy["minimum_cash_reserve_krw"]))
                    / D(
                        getattr(self.market, "analysis_budget", None)
                        or policy["max_entry_krw"]
                    )
                ),
            ),
        )
        if active and risk["buy_signals_enabled"]:
            for item in candidates:
                if slots <= 0:
                    break
                symbol = item["symbol"]
                if not item["ready"] or symbol in positions:
                    continue
                bars = self.market.book.load_contiguous(symbol, limit=100)
                passed, reasons = entry(active["kind"], bars, active["params"])
                if passed and bars and time.time() - (bars[-1]["start"] + 60) <= 120:
                    # Costs must leave positive reward vs stop; no automatic loss-chasing size changes.
                    costs = 2 * (
                        D(policy["execution_fee_rate"])
                        + D(policy["execution_slippage_rate"])
                    )
                    if (
                        D(active["params"]["take_profit"]) - costs
                        <= D(active["params"]["stop_loss"]) + costs
                    ):
                        continue
                    quote = self.market.quote(symbol)
                    slots -= 1
                    self.signal(
                        symbol,
                        "BUY_CANDIDATE",
                        active["id"],
                        quote["asks"][0][0],
                        reasons,
                        f"{active['id']}:{symbol}:{bars[-1]['start']}",
                        basis=bars[-max(active["params"]["lookback"] + 1, 40) :],
                    )
        selected = ([active] if active else []) + [
            v for v in reversed(versions) if not active or v["id"] != active["id"]
        ]
        selected_ids = {v["id"] for v in selected[:12]}
        paper_interval = 10 if getattr(self.market, "overloaded", False) else 2
        paper_due = time.monotonic() - self.paper_entry_at >= paper_interval
        if paper_due:
            self.paper_entry_at = time.monotonic()
        for version in versions:
            self.paper(version, candidates, version["id"] in selected_ids and paper_due)
        minute = int(time.time()) // 60 * 60
        if self.performance_minute != minute:
            self.performance_minute = minute
            with self.store.connect() as conn:
                if ledger["valuation_complete"]:
                    equity = D(ledger["cash_krw"]) + sum(
                        D(p["quantity"])
                        * D(p["mark_price"])
                        * (1 - D(policy["execution_fee_rate"]))
                        for p in ledger["positions"]
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO performance_samples VALUES (?,?,?,?)",
                        ("MANUAL", "MANUAL_ACCOUNT", minute, decimal_text(equity)),
                    )
                for account in conn.execute("SELECT * FROM paper_accounts").fetchall():
                    holdings = json.loads(account["position"])["holdings"]
                    equity = D(account["cash"])
                    valid = True
                    for symbol, pos in holdings.items():
                        quote = self.market.quote(symbol)
                        if not quote:
                            valid = False
                            break
                        equity += (
                            D(pos["quantity"]) * D(quote["bids"][0][0]) * D("0.9996")
                        )
                    if valid:
                        conn.execute(
                            "INSERT OR REPLACE INTO performance_samples VALUES (?,?,?,?)",
                            (
                                "PAPER",
                                account["version_id"],
                                minute,
                                decimal_text(equity),
                            ),
                        )
                conn.execute(
                    "DELETE FROM market_events WHERE rowid NOT IN (SELECT rowid FROM market_events ORDER BY rowid DESC LIMIT 50000)"
                )
                conn.execute(
                    "DELETE FROM performance_samples WHERE at<?", (minute - 30 * 86400,)
                )
        self.market.metrics["signal_compute_ms"] = (
            time.perf_counter() - started
        ) * 1000
        try:
            self.reversion.tick()
            self.reversion.last_error = None
        except Exception as error:
            self.reversion.last_error = type(error).__name__
            logger.exception("paper mean-reversion evaluation paused")

    async def run(self):
        while True:
            try:
                await asyncio.to_thread(self.tick)
                self.last_tick_at = datetime.now(UTC).isoformat()
                self.last_error = None
            except Exception as error:
                self.last_error = type(error).__name__
                logger.exception("signal evaluation paused")
            await asyncio.sleep(1)
