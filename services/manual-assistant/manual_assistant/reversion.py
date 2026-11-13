"""Paper-only crash/rebound lab.  This module has no exchange order dependency."""

import hashlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from decimal import localcontext

from .exit_engine import evaluate_exit_step
from .ledger import decimal_text
from .reversion_schema import DEFAULT_EXIT_POLICY, POLICIES

PRIMARY = "structure_trailing"
logger = logging.getLogger("bsb.manual.reversion")


def sweep(levels, budget=None, quantity=None, buy=True):
    total = D(0)
    filled = D(0)
    fee, slip = D("0.0004"), D("0.001")
    with localcontext() as ctx:
        ctx.prec = 80
        for raw_price, raw_quantity in levels:
            price = D(raw_price) * (1 + slip if buy else 1 - slip)
            available = D(raw_quantity)
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


class ReversionLab:
    def __init__(self, store, market):
        self.store, self.market = store, market
        self.watch = {}
        self.pending_auto_buys = []
        self.last_scan = 0.0
        self.last_error = None

    def config(self):
        with self.store.connect() as conn:
            return json.loads(
                conn.execute(
                    "SELECT value FROM strategy_state WHERE key='reversion_config'"
                ).fetchone()[0]
            )

    def coin_name(self, symbol):
        return (
            getattr(self.market, "market_metadata", {}).get(symbol, {}).get("korean_name")
            or symbol.removeprefix("KRW-")
        )

    @staticmethod
    def crash_signal(changes, config):
        one, three, five = changes
        return bool(
            three <= -D(config["drop_3m"])
            or five <= -D(config["drop_5m"])
            or (
                one <= -D(config["drop_1m"])
                and three <= -D(config.get("drop_1m_confirm_3m", "0.012"))
            )
        )

    @staticmethod
    def rebound_trigger(confirmation, base_high, effective_low, config):
        if len(confirmation) < int(config.get("confirmation_bars", 2)):
            return None
        if not all(
            D(later["close"]) > D(earlier["close"])
            and D(later["low"]) > D(earlier["low"])
            for earlier, later in zip(confirmation, confirmation[1:])
        ):
            return None
        if any(D(bar["close"]) <= D(bar["open"]) for bar in confirmation):
            return None
        trigger = D(confirmation[-1]["close"])
        if trigger < effective_low * (1 + D(config.get("rebound_rate", "0.008"))):
            return None
        if trigger < base_high * (
            1 + D(config.get("breakout_buffer_rate", "0.002"))
        ):
            return None
        return trigger

    @staticmethod
    def atr(bars, periods=14):
        if len(bars) < periods + 1:
            return D(0)
        values = []
        for previous, bar in zip(bars[-periods - 1 : -1], bars[-periods:]):
            values.append(
                max(
                    D(bar["high"]) - D(bar["low"]),
                    abs(D(bar["high"]) - D(previous["close"])),
                    abs(D(bar["low"]) - D(previous["close"])),
                )
            )
        return sum(values) / len(values)

    def exit_policy(self):
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT value FROM strategy_state WHERE key='exit_policy_config'"
            ).fetchone()
            return {**DEFAULT_EXIT_POLICY, **(json.loads(row[0]) if row else {})}

    def update_exit_policy(self, patch: dict):
        current = self.exit_policy()
        merged = {**current, **patch}
        tp1_pct = D(str(merged["tp1_pct"]))
        tp2_pct = D(str(merged["tp2_pct"]))
        tp1_frac = D(str(merged["tp1_fraction"]))
        tp2_frac = D(str(merged["tp2_fraction"]))
        trail_pct = D(str(merged["trailing_drawdown_pct"]))
        max_stop = D(str(merged["maximum_stop_rate"]))
        min_krw = D(str(merged["min_order_krw"]))
        if tp1_pct <= 0 or tp2_pct <= tp1_pct or tp2_pct > D("0.5"):
            raise ValueError("TP1 < TP2 범위가 올바르지 않습니다.")
        if tp1_frac <= 0 or tp2_frac <= 0 or tp1_frac + tp2_frac >= D("1"):
            raise ValueError("TP1 + TP2 매도 비율 합계는 100% 미만이어야 합니다.")
        if trail_pct <= 0 or trail_pct > D("0.2") or max_stop <= 0 or max_stop > D("0.1"):
            raise ValueError("트레일링 또는 최대 손절 한도 범위가 올바르지 않습니다.")
        if min_krw < D("500"):
            raise ValueError("최소 주문 금액이 너무 작습니다.")
        merged["policy_version"] = int(current.get("policy_version", 1)) + 1
        merged["mode"] = "PAPER"  # LIVE mode requires explicit approval gate.
        with self.store.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO strategy_state VALUES('exit_policy_config',?)",
                (json.dumps(merged),),
            )
        return merged

    def _record_exit_event(
        self, conn, position_id, event_type, market_price, state_before, state_after, reason, metadata=None
    ):
        conn.execute(
            "INSERT INTO position_exit_event VALUES (?,?,?,?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                position_id,
                event_type,
                decimal_text(market_price),
                state_before,
                state_after,
                reason,
                json.dumps(metadata or {}),
                datetime.now(UTC).isoformat(),
            ),
        )

    def _paper_trade(self, conn, position, side, **extra):
        at = datetime.now(UTC).isoformat()
        payload = {
            "symbol": position["symbol"],
            "side": side,
            "quantity": position["quantity"],
            "fill_price": extra.get("fill_price", position["entry_price"]),
            "signal_at": position["opened_at"],
            "at": at,
            "version_id": f"mean-reversion:{position['policy']}",
            "simulated": True,
            "channel": "PAPER",
            "strategy_family": "MEAN_REVERSION",
            "policy": position["policy"],
            "stop_price": position["current_stop"],
            "initial_stop_price": position["initial_stop"],
            "trailing_activation_rate": json.loads(position["config"])[
                "trailing_activation_rate"
            ],
            "trailing_drawdown_rate": json.loads(position["config"])[
                "trailing_drawdown_rate"
            ],
            **extra,
        }
        identifier = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        conn.execute(
            "INSERT OR IGNORE INTO paper_trades VALUES (?,?,?,?)",
            (identifier, payload["version_id"], json.dumps(payload), at),
        )
        return identifier

    def _enter(
        self,
        symbol,
        low,
        bars,
        quote,
        config,
        drops,
        base_count=None,
        base_high=None,
    ):
        group = hashlib.sha256(
            f"reversion:{symbol}:{bars[-1]['start']}".encode()
        ).hexdigest()[:24]
        now = datetime.now(UTC).isoformat()
        atr = self.atr(bars)
        summary = (
            f"급락 후 바닥 횡보({base_count}분) 및 반등 돌파 확인"
            if base_count is not None
            else "급락 후 저점 형성·반등 확인"
        )
        reasons_list = [
            f"1·3·5분 급락률 {drops}",
            f"바닥 횡보 다지기 {base_count}분 지지"
            if base_count is not None
            else "급락 저점 형성",
            f"저점 대비 {config.get('rebound_rate', '0.006')} 반등 돌파",
            "실제 주문 없는 가상매매",
        ]
        with self.store.connect() as conn:
            for policy in POLICIES:
                account = conn.execute(
                    "SELECT * FROM reversion_accounts WHERE policy=?", (policy,)
                ).fetchone()
                open_count = conn.execute(
                    "SELECT count(*) FROM reversion_positions WHERE policy=? AND status='OPEN'",
                    (policy,),
                ).fetchone()[0]
                if D(account["realized"]) <= -D("3000") or open_count >= 2:
                    continue
                qty, cost = sweep(quote["asks"], budget=D(config["entry_budget_krw"]))
                if not qty or D(account["cash"]) - cost < D("50000"):
                    continue
                entry = cost / (qty * D("1.0004"))
                maximum = entry * (1 - D(config["maximum_stop_rate"]))
                structure = low * (1 - D(config["structure_buffer_rate"]))
                fixed = maximum
                atr_stop = entry - atr * D("1.5") if atr else maximum
                stop = max(
                    maximum,
                    atr_stop
                    if policy == "atr_trailing"
                    else fixed
                    if policy == "fixed_trailing"
                    else structure,
                )
                identifier = hashlib.sha256(f"{group}:{policy}".encode()).hexdigest()
                position = {
                    "id": identifier,
                    "entry_group": group,
                    "policy": policy,
                    "symbol": symbol,
                    "status": "OPEN",
                    "quantity": decimal_text(qty),
                    "cost": decimal_text(cost),
                    "entry_price": decimal_text(entry),
                    "structure_low": decimal_text(low),
                    "initial_stop": decimal_text(stop),
                    "current_stop": decimal_text(stop),
                    "peak_price": decimal_text(entry),
                    "trail_active": 0,
                    "opened_at": now,
                    "closed_at": None,
                    "exit_price": None,
                    "net_pnl": None,
                    "exit_reason": None,
                    "config": json.dumps(config),
                }
                conn.execute(
                    "INSERT OR IGNORE INTO reversion_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(position.values()),
                )
                if not conn.execute("SELECT changes()").fetchone()[0]:
                    continue
                conn.execute(
                    "UPDATE reversion_accounts SET cash=? WHERE policy=?",
                    (decimal_text(D(account["cash"]) - cost), policy),
                )
                self._paper_trade(
                    conn,
                    position,
                    "BUY",
                    cost_krw=decimal_text(cost),
                    fee_krw=decimal_text(cost * D("0.0004") / D("1.0004")),
                    slippage_krw=decimal_text(
                        cost / D("1.0004") * D("0.001") / D("1.001")
                    ),
                    reason_summary=summary,
                    reasons=reasons_list,
                    basis=bars[-40:],
                )
                if policy == "partial_tp_trailing":
                    ep_row = conn.execute(
                        "SELECT value FROM strategy_state WHERE key='exit_policy_config'"
                    ).fetchone()
                    ep = {**DEFAULT_EXIT_POLICY, **(json.loads(ep_row[0]) if ep_row else {})}
                    conn.execute(
                        "INSERT OR IGNORE INTO position_exit_policy VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            identifier,
                            int(ep.get("policy_version", 1)),
                            decimal_text(entry),
                            decimal_text(qty),
                            str(ep["tp1_pct"]),
                            str(ep["tp1_fraction"]),
                            str(ep["tp2_pct"]),
                            str(ep["tp2_fraction"]),
                            str(ep["trailing_drawdown_pct"]),
                            str(ep.get("runner_activation", "AFTER_TP2")),
                            int(bool(ep.get("break_even_after_tp1", False))),
                            decimal_text(entry),
                            decimal_text(stop),
                            "OPEN",
                            "PAPER",
                            now,
                            now,
                        ),
                    )
                    self._record_exit_event(
                        conn,
                        identifier,
                        "ENTRY_OPENED",
                        entry,
                        "NONE",
                        "OPEN",
                        "INITIAL_ENTRY",
                        {"initial_quantity": decimal_text(qty), "initial_stop": decimal_text(stop)},
                    )

    def _close(self, conn, row, quote, reason):
        quantity = D(row["quantity"])
        filled, proceeds = sweep(quote["bids"], quantity=quantity, buy=False)
        if filled != quantity:
            return False
        pnl = proceeds - D(row["cost"])
        price = proceeds / (quantity * D("0.9996"))
        at = datetime.now(UTC).isoformat()
        conn.execute(
            "UPDATE reversion_positions SET status='CLOSED',closed_at=?,exit_price=?,net_pnl=?,exit_reason=? WHERE id=? AND status='OPEN'",
            (at, decimal_text(price), decimal_text(pnl), reason, row["id"]),
        )
        account = conn.execute(
            "SELECT * FROM reversion_accounts WHERE policy=?", (row["policy"],)
        ).fetchone()
        cash = D(account["cash"]) + proceeds
        realized = D(account["realized"]) + pnl
        equity = cash
        for remaining in conn.execute(
            "SELECT symbol,quantity FROM reversion_positions WHERE policy=? AND status='OPEN' AND id!=?",
            (row["policy"], row["id"]),
        ):
            remaining_quote = self.market.quote(remaining["symbol"])
            if remaining_quote:
                equity += (
                    D(remaining["quantity"])
                    * D(remaining_quote["bids"][0][0])
                    * D("0.999")
                    * D("0.9996")
                )
        peak = max(D(account["peak_equity"]), equity)
        drawdown = (peak - equity) / peak if peak else D(0)
        conn.execute(
            "UPDATE reversion_accounts SET cash=?,realized=?,peak_equity=?,max_drawdown=? WHERE policy=?",
            (
                decimal_text(cash),
                decimal_text(realized),
                decimal_text(peak),
                decimal_text(max(D(account["max_drawdown"]), drawdown)),
                row["policy"],
            ),
        )
        closed = {**dict(row), "current_stop": row["current_stop"]}
        self._paper_trade(
            conn,
            closed,
            "SELL",
            fill_price=decimal_text(price),
            proceeds=decimal_text(proceeds),
            net_pnl=decimal_text(pnl),
            fee_krw=decimal_text(proceeds * D("0.0004") / D("0.9996")),
            slippage_krw=decimal_text(proceeds / D("0.9996") * D("0.001") / D("0.999")),
            reason=reason,
            reason_summary={
                "STRUCTURE_STOP": "구조적 저점·최대 손절선 도달",
                "TRAILING_STOP": "상승 추세 추적 후 트레일링 청산",
                "REBOUND_FAILURE": "10분 내 반등 지속 실패",
                "FIXED_TAKE_COMPARISON": "고정 +2.5% 비교 실험 청산",
                "DUST_REMAINDER": "최소 주문 금액 미만 잔여 수량 정리",
            }.get(reason, reason),
        )
        if row["policy"] == PRIMARY:
            until = datetime.now(UTC) + timedelta(
                seconds=json.loads(row["config"])["cooldown_seconds"]
            )
            conn.execute(
                "INSERT OR REPLACE INTO reversion_cooldowns VALUES (?,?,?)",
                (row["symbol"], until.isoformat(), reason),
            )
        return True

    def _protect(self, config):
        with self.store.connect() as conn:
            positions = conn.execute(
                "SELECT * FROM reversion_positions WHERE status='OPEN'"
            ).fetchall()
            for row in positions:
                position_config = {**config, **json.loads(row["config"])}
                quote = self.market.quote(row["symbol"])
                if not quote:
                    continue
                bid = D(quote["bids"][0][0])
                if row["policy"] == "partial_tp_trailing":
                    self._protect_partial(conn, row, quote, position_config, bid)
                    continue
                entry, peak, stop = map(
                    D, (row["entry_price"], row["peak_price"], row["current_stop"])
                )
                peak = max(peak, bid)
                trail = bool(row["trail_active"])
                if peak >= entry * (
                    1 + D(position_config["trailing_activation_rate"])
                ):
                    trail = True
                if trail and row["policy"] != "structure_fixed_take":
                    stop = max(
                        stop,
                        peak * (1 - D(position_config["trailing_drawdown_rate"])),
                    )
                conn.execute(
                    "UPDATE reversion_positions SET peak_price=?,current_stop=?,trail_active=? WHERE id=?",
                    (decimal_text(peak), decimal_text(stop), int(trail), row["id"]),
                )
                row = {
                    **dict(row),
                    "peak_price": decimal_text(peak),
                    "current_stop": decimal_text(stop),
                    "trail_active": int(trail),
                }
                reason = None
                if bid <= stop:
                    reason = "TRAILING_STOP" if trail else "STRUCTURE_STOP"
                elif row["policy"] == "structure_fixed_take" and bid >= entry * (
                    1 + D(position_config["fixed_take_comparison_rate"])
                ):
                    reason = "FIXED_TAKE_COMPARISON"
                else:
                    elapsed = (
                        datetime.now(UTC) - datetime.fromisoformat(row["opened_at"])
                    ).total_seconds()
                    bars = self.market.book.load_contiguous(row["symbol"], limit=4)
                    rising = (
                        bid > entry
                        and len(bars) >= 3
                        and all(
                            D(b["close"]) >= D(a["close"])
                            for a, b in zip(bars[-3:], bars[-2:])
                        )
                    )
                    if elapsed >= position_config["failure_seconds"] and not rising:
                        reason = "REBOUND_FAILURE"
                if reason:
                    self._close(conn, row, quote, reason)

    def _protect_partial(self, conn, row, quote, position_config, bid):
        pos_id = row["id"]
        ep_row = conn.execute(
            "SELECT * FROM position_exit_policy WHERE position_id=?", (pos_id,)
        ).fetchone()
        if not ep_row:
            return
        ep = dict(ep_row)
        leg_rows = conn.execute(
            "SELECT * FROM position_exit_leg WHERE position_id=?", (pos_id,)
        ).fetchall()
        legs = {r["leg"]: dict(r) for r in leg_rows}

        entry = D(ep["entry_avg_price"])
        elapsed = (
            datetime.now(UTC) - datetime.fromisoformat(row["opened_at"])
        ).total_seconds()
        bars = self.market.book.load_contiguous(row["symbol"], limit=4)
        rising = (
            bid > entry
            and len(bars) >= 3
            and all(
                D(b["close"]) >= D(a["close"]) for a, b in zip(bars[-3:], bars[-2:])
            )
        )
        rebound_failure = elapsed >= position_config["failure_seconds"] and not rising

        ep_cfg_row = conn.execute(
            "SELECT value FROM strategy_state WHERE key='exit_policy_config'"
        ).fetchone()
        ep_cfg = {**DEFAULT_EXIT_POLICY, **(json.loads(ep_cfg_row[0]) if ep_cfg_row else {})}
        min_order_krw = D(str(ep_cfg.get("min_order_krw", "5000")))

        decision = evaluate_exit_step(
            position_id=pos_id,
            state=ep["state"],
            entry_avg_price=entry,
            initial_quantity=D(ep["initial_quantity"]),
            remaining_quantity=D(row["quantity"]),
            peak_price=D(ep["peak_price"]),
            protective_stop_price=D(ep["protective_stop_price"]),
            task16_stop_price=D(row["initial_stop"]),
            latest_price=bid,
            tp1_pct=D(ep["tp1_pct"]),
            tp1_fraction=D(ep["tp1_fraction"]),
            tp2_pct=D(ep["tp2_pct"]),
            tp2_fraction=D(ep["tp2_fraction"]),
            trailing_drawdown_pct=D(ep["trailing_drawdown_pct"]),
            break_even_after_tp1=bool(ep["break_even_after_tp1"]),
            min_order_krw=min_order_krw,
            legs=legs,
            rebound_failure_triggered=rebound_failure,
        )

        now = datetime.now(UTC).isoformat()
        state_before = ep["state"]
        final_state = decision.state_after
        final_stop = decision.protective_stop_price

        for ev_type, ev_reason, ev_meta in decision.events:
            self._record_exit_event(
                conn, pos_id, ev_type, bid, state_before, final_state, ev_reason, ev_meta
            )
            if ev_type == "DUST_DEFERRED":
                leg_name = "TP1" if "TP1" in ev_reason else "TP2"
                conn.execute(
                    "INSERT OR IGNORE INTO position_exit_leg VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid.uuid4().hex,
                        pos_id,
                        leg_name,
                        str(ev_meta.get("target_qty", "0")),
                        "0",
                        None,
                        None,
                        f"{pos_id}:{leg_name}:DEFERRED",
                        "DEFERRED",
                        "0",
                        "0",
                        now,
                        now,
                    ),
                )

        current_row = dict(row)
        for intent in decision.intents:
            if intent.leg in ("TP1", "TP2"):
                leg_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT OR IGNORE INTO position_exit_leg VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        leg_id,
                        pos_id,
                        intent.leg,
                        decimal_text(intent.target_quantity),
                        "0",
                        None,
                        None,
                        intent.client_order_id,
                        "PENDING",
                        "0",
                        "0",
                        now,
                        now,
                    ),
                )
                if not conn.execute("SELECT changes()").fetchone()[0]:
                    continue
                filled, proceeds = sweep(
                    quote["bids"], quantity=intent.target_quantity, buy=False
                )
                if filled <= 0:
                    continue
                rem_qty = D(current_row["quantity"])
                rem_cost = D(current_row["cost"])
                cost_share = rem_cost * (filled / rem_qty)
                partial_pnl = proceeds - cost_share
                fill_price = proceeds / (filled * D("0.9996"))
                fees = proceeds * D("0.0004") / D("0.9996")
                slippage = proceeds / D("0.9996") * D("0.001") / D("0.999")

                conn.execute(
                    "UPDATE position_exit_leg SET filled_quantity=?,avg_fill_price=?,status='FILLED',realized_pnl=?,fees=?,updated_at=? WHERE client_order_id=?",
                    (
                        decimal_text(filled),
                        decimal_text(fill_price),
                        decimal_text(partial_pnl),
                        decimal_text(fees),
                        now,
                        intent.client_order_id,
                    ),
                )
                new_rem_qty = rem_qty - filled
                new_rem_cost = rem_cost - cost_share
                current_row["quantity"] = decimal_text(new_rem_qty)
                current_row["cost"] = decimal_text(new_rem_cost)

                if intent.leg == "TP1":
                    final_state = "TP1_DONE"
                elif intent.leg == "TP2":
                    final_state = "RUNNER_ACTIVE"
                    final_stop = max(
                        final_stop,
                        decision.peak_price * (1 - D(ep["trailing_drawdown_pct"])),
                    )

                conn.execute(
                    "UPDATE reversion_positions SET quantity=?,cost=?,peak_price=?,current_stop=?,trail_active=? WHERE id=?",
                    (
                        current_row["quantity"],
                        current_row["cost"],
                        decimal_text(decision.peak_price),
                        decimal_text(final_stop),
                        int(final_state == "RUNNER_ACTIVE"),
                        pos_id,
                    ),
                )
                account = conn.execute(
                    "SELECT * FROM reversion_accounts WHERE policy=?",
                    (current_row["policy"],),
                ).fetchone()
                cash = D(account["cash"]) + proceeds
                realized = D(account["realized"]) + partial_pnl
                conn.execute(
                    "UPDATE reversion_accounts SET cash=?,realized=? WHERE policy=?",
                    (decimal_text(cash), decimal_text(realized), current_row["policy"]),
                )
                self._paper_trade(
                    conn,
                    {**current_row, "current_stop": decimal_text(final_stop)},
                    "SELL",
                    fill_price=decimal_text(fill_price),
                    proceeds=decimal_text(proceeds),
                    net_pnl=decimal_text(partial_pnl),
                    fee_krw=decimal_text(fees),
                    slippage_krw=decimal_text(slippage),
                    reason=f"{intent.leg}_PARTIAL_EXIT",
                    reason_summary=(
                        f"TP1 +{(D(ep['tp1_pct']) * 100):.1f}% 1차 분할 익절"
                        if intent.leg == "TP1"
                        else f"TP2 +{(D(ep['tp2_pct']) * 100):.1f}% 2차 분할 익절 · Runner 추종 시작"
                    ),
                )
                self._record_exit_event(
                    conn,
                    pos_id,
                    f"{intent.leg}_FILLED",
                    fill_price,
                    state_before,
                    final_state,
                    intent.reason,
                    {
                        "filled_quantity": decimal_text(filled),
                        "realized_pnl": decimal_text(partial_pnl),
                    },
                )
                state_before = final_state
            elif intent.leg in ("STOP", "RUNNER"):
                conn.execute(
                    "INSERT OR IGNORE INTO position_exit_leg VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid.uuid4().hex,
                        pos_id,
                        intent.leg,
                        decimal_text(intent.target_quantity),
                        decimal_text(intent.target_quantity),
                        decimal_text(bid),
                        None,
                        intent.client_order_id,
                        "FILLED",
                        "0",
                        "0",
                        now,
                        now,
                    ),
                )
                current_row["current_stop"] = decimal_text(final_stop)
                if self._close(conn, current_row, quote, intent.reason):
                    final_state = "CLOSED"
                    self._record_exit_event(
                        conn,
                        pos_id,
                        f"{intent.leg}_CLOSED",
                        bid,
                        state_before,
                        "CLOSED",
                        intent.reason,
                        {"remaining_qty": current_row["quantity"]},
                    )

        if final_state == "CLOSED" and not decision.intents:
            conn.execute(
                "UPDATE reversion_positions SET status='CLOSED',closed_at=?,exit_price=?,net_pnl='0',exit_reason='DUST_REMAINDER' WHERE id=? AND status='OPEN'",
                (now, decimal_text(bid), pos_id),
            )

        conn.execute(
            "UPDATE reversion_positions SET peak_price=?,current_stop=?,trail_active=? WHERE id=? AND status='OPEN'",
            (
                decimal_text(decision.peak_price),
                decimal_text(final_stop),
                int(final_state == "RUNNER_ACTIVE"),
                pos_id,
            ),
        )
        conn.execute(
            "UPDATE position_exit_policy SET peak_price=?,protective_stop_price=?,state=?,updated_at=? WHERE position_id=?",
            (
                decimal_text(decision.peak_price),
                decimal_text(final_stop),
                final_state,
                now,
                pos_id,
            ),
        )

    def tick(self):
        config = self.config()
        if not config["enabled"]:
            return
        self._protect(config)
        if time.monotonic() - self.last_scan < 2:
            return
        self.last_scan = time.monotonic()
        now = datetime.now(UTC)
        with self.store.connect() as conn:
            cooldowns = {
                r["symbol"]
                for r in conn.execute("SELECT * FROM reversion_cooldowns")
                if datetime.fromisoformat(r["until_at"]) > now
            }
            open_symbols = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT symbol FROM reversion_positions WHERE status='OPEN'"
                )
            }
        for row in self.market.rows:
            symbol = row["symbol"]
            raw_changes = [
                row.get(k)
                for k in (
                    "completed_price_change_1m",
                    "completed_price_change_3m",
                    "completed_price_change_5m",
                )
            ]
            if any(value is None for value in raw_changes):
                continue
            changes = [D(value) for value in raw_changes]
            crashed = self.crash_signal(changes, config)
            if crashed and symbol not in cooldowns and symbol not in open_symbols:
                now_mono = time.monotonic()
                if symbol not in self.watch:
                    self.watch[symbol] = {
                        "low": D(row["price"]),
                        "drops": [decimal_text(x) for x in changes],
                        "until": now_mono + 900,
                        "base_bars": 0,
                    }
                    self.pending_auto_buys.append({"symbol": symbol, "version_id": "reversion_watch"})
                    logger.warning("reversion_watch added: %s", symbol)
                else:
                    existing = self.watch[symbol]
                    cur_p = D(row["price"])
                    if cur_p < D(existing["low"]):
                        existing["low"] = cur_p
                        existing["base_bars"] = 0
                    existing["drops"] = [decimal_text(x) for x in changes]
                    existing["until"] = now_mono + 900
                self.market.manual_detail[symbol] = time.monotonic() + 120
        for symbol, watched in list(self.watch.items()):
            if watched["until"] < time.monotonic() or symbol in open_symbols:
                self.watch.pop(symbol, None)
                continue
            row = next((r for r in self.market.rows if r["symbol"] == symbol), None)
            if not row or not row.get("price"):
                continue
            price = D(row["price"])
            if price < D(watched["low"]):
                watched["low"] = price
                watched["base_bars"] = 0
            if not row["ready"]:
                continue
            bars = self.market.book.load_contiguous(symbol, limit=100)
            if len(bars) < 16:
                continue

            # Drop-Base-Rebound pattern check
            recent_lookback = min(len(bars), 16)
            recent_bars = bars[-recent_lookback:]
            low_idx = min(range(len(recent_bars)), key=lambda i: D(recent_bars[i]["low"]))
            low_bar = recent_bars[low_idx]
            confirmation_count = int(config.get("confirmation_bars", 2))
            base_bars = recent_bars[low_idx + 1 : -confirmation_count]
            base_count = len(base_bars)
            watched["base_bars"] = base_count

            min_base_bars = int(config.get("min_base_bars", 3))
            max_base_bars = int(config.get("max_base_bars", 15))
            base_range_rate = D(str(config.get("base_range_rate", "0.025")))
            max_chase = D(str(config.get("max_chase_rate", "0.003")))

            if base_count < min_base_bars:
                continue
            if base_count > max_base_bars:
                self.watch.pop(symbol, None)
                continue

            crash_low = min(D(watched["low"]), D(low_bar["low"]))
            base_low = min(D(b["low"]) for b in base_bars)
            base_high = max(D(b["high"]) for b in base_bars)

            # Check base low hasn't broken down below crash low
            if base_low < crash_low * D("0.995"):
                continue

            # Check base spread is within base_range_rate (narrow consolidation)
            if base_low <= 0 or (base_high - base_low) / base_low > base_range_rate:
                continue

            effective_low = min(crash_low, base_low)
            confirmation = recent_bars[-confirmation_count:]
            trigger = self.rebound_trigger(
                confirmation, base_high, effective_low, config
            )
            if trigger is None:
                continue

            safe, _ = self.market.safe(symbol)
            quote = self.market.quote(symbol)
            if safe and quote:
                ask = D(quote["asks"][0][0])
                if ask > trigger * (1 + max_chase):
                    continue
                self._enter(
                    symbol,
                    effective_low,
                    bars,
                    quote,
                    config,
                    watched["drops"],
                    base_count=base_count,
                    base_high=base_high,
                )
                self.watch.pop(symbol, None)

    def summary(self):
        with self.store.connect() as conn:
            accounts = [
                dict(r) for r in conn.execute("SELECT * FROM reversion_accounts")
            ]
            positions = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM reversion_positions WHERE policy=? ORDER BY opened_at DESC",
                    (PRIMARY,),
                )
            ]
            all_closed = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM reversion_positions WHERE status='CLOSED'"
                )
            ]
        open_items = [p for p in positions if p["status"] == "OPEN"]
        for item in open_items:
            row = next(
                (r for r in self.market.rows if r["symbol"] == item["symbol"]), None
            )
            item["current_price"] = row.get("price") if row else None
            item["coin_name"] = self.coin_name(item["symbol"])
            item["return_rate"] = (
                decimal_text(D(item["current_price"]) / D(item["entry_price"]) - 1)
                if item["current_price"]
                else None
            )
        closed = [p for p in positions if p["status"] == "CLOSED"]
        for item in closed:
            item["coin_name"] = self.coin_name(item["symbol"])
        comparisons = []
        for account in accounts:
            trades = [p for p in all_closed if p["policy"] == account["policy"]]
            wins = [D(p["net_pnl"]) for p in trades if D(p["net_pnl"]) > 0]
            losses = [D(p["net_pnl"]) for p in trades if D(p["net_pnl"]) < 0]
            comparisons.append(
                {
                    **account,
                    "trades": len(trades),
                    "win_rate": decimal_text(D(len(wins)) / len(trades))
                    if trades
                    else None,
                    "average_profit": decimal_text(sum(wins) / len(wins))
                    if wins
                    else None,
                    "average_loss": decimal_text(sum(losses) / len(losses))
                    if losses
                    else None,
                }
            )
        watching_items = [
            {
                "symbol": symbol,
                "coin_name": self.coin_name(symbol),
                "low": decimal_text(item["low"]),
                "drops": item.get("drops", []),
                "base_bars": item.get("base_bars", 0),
            }
            for symbol, item in sorted(self.watch.items())
        ]
        return {
            "paper_only": True,
            "live_execution": False,
            "watching": len(self.watch),
            "watching_symbols": [s for s in sorted(self.watch.keys())],
            "watching_items": watching_items,
            "positions": open_items,
            "recent_results": closed[:5],
            "comparison": comparisons,
            "config": self.config(),
            "exit_policy": self.exit_policy(),
            "partial_positions": self._partial_positions_summary(),
        }

    def _partial_positions_summary(self):
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT p.*, ep.state as exit_state, ep.initial_quantity, ep.tp1_pct, ep.tp2_pct, ep.trailing_drawdown_pct, ep.protective_stop_price, ep.peak_price as ep_peak FROM reversion_positions p JOIN position_exit_policy ep ON p.id=ep.position_id WHERE p.policy='partial_tp_trailing' AND p.status='OPEN' ORDER BY p.opened_at DESC"
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                legs = [
                    dict(l)
                    for l in conn.execute(
                        "SELECT leg,status,target_quantity,filled_quantity,avg_fill_price,realized_pnl,fees FROM position_exit_leg WHERE position_id=?",
                        (d["id"],),
                    )
                ]
                mkt = next((m for m in self.market.rows if m["symbol"] == d["symbol"]), None)
                cur = mkt.get("price") if mkt else None
                entry_p = D(d["entry_price"])
                d["coin_name"] = self.coin_name(d["symbol"])
                d["current_price"] = cur
                d["return_rate"] = (
                    decimal_text(D(cur) / entry_p - 1) if cur and entry_p > 0 else None
                )
                d["tp1_target"] = decimal_text(entry_p * (1 + D(d["tp1_pct"])))
                d["tp2_target"] = decimal_text(entry_p * (1 + D(d["tp2_pct"])))
                d["realized_partial_pnl"] = decimal_text(
                    sum((D(l["realized_pnl"]) for l in legs), D(0))
                )
                d["legs"] = legs
                result.append(d)
            return result

    def get_exit_state(self, position_id: str):
        with self.store.connect() as conn:
            ep = conn.execute(
                "SELECT * FROM position_exit_policy WHERE position_id=?", (position_id,)
            ).fetchone()
            pos = conn.execute(
                "SELECT * FROM reversion_positions WHERE id=?", (position_id,)
            ).fetchone()
            if not ep or not pos:
                return None
            legs = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM position_exit_leg WHERE position_id=? ORDER BY created_at",
                    (position_id,),
                )
            ]
        ep_d = dict(ep)
        pos_d = dict(pos)
        entry_p = D(ep_d["entry_avg_price"])
        return {
            "position_id": position_id,
            "symbol": pos_d["symbol"],
            "coin_name": self.coin_name(pos_d["symbol"]),
            "state": ep_d["state"],
            "mode": ep_d["mode"],
            "entry_avg_price": ep_d["entry_avg_price"],
            "initial_quantity": ep_d["initial_quantity"],
            "remaining_quantity": pos_d["quantity"],
            "peak_price": ep_d["peak_price"],
            "protective_stop_price": ep_d["protective_stop_price"],
            "tp1_target": decimal_text(entry_p * (1 + D(ep_d["tp1_pct"]))),
            "tp2_target": decimal_text(entry_p * (1 + D(ep_d["tp2_pct"]))),
            "legs": legs,
        }

    def get_exit_events(self, position_id: str | None = None, limit: int = 50):
        with self.store.connect() as conn:
            if position_id:
                rows = conn.execute(
                    "SELECT * FROM position_exit_event WHERE position_id=? ORDER BY created_at DESC LIMIT ?",
                    (position_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM position_exit_event ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            {**dict(r), "metadata": json.loads(r["metadata_json"])} for r in rows
        ]

    def get_exit_comparison(self):
        with self.store.connect() as conn:
            accounts = {
                r["policy"]: dict(r)
                for r in conn.execute("SELECT * FROM reversion_accounts")
            }
            closed_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM reversion_positions WHERE status='CLOSED'"
                )
            ]
            leg_rows = [
                dict(r)
                for r in conn.execute("SELECT position_id,leg,status,realized_pnl FROM position_exit_leg")
            ]
        tp1_hits = sum(1 for l in leg_rows if l["leg"] == "TP1" and l["status"] == "FILLED")
        tp2_hits = sum(1 for l in leg_rows if l["leg"] == "TP2" and l["status"] == "FILLED")
        runner_reasons = {}
        for p in closed_rows:
            if p["policy"] == "partial_tp_trailing":
                r = p.get("exit_reason") or "UNKNOWN"
                runner_reasons[r] = runner_reasons.get(r, 0) + 1
        out = []
        for pol in POLICIES:
            acc = accounts.get(pol, {})
            trades = [p for p in closed_rows if p["policy"] == pol]
            wins = [D(p["net_pnl"]) for p in trades if D(p["net_pnl"]) > 0]
            losses = [D(p["net_pnl"]) for p in trades if D(p["net_pnl"]) < 0]
            gross_win = sum(wins, D(0))
            gross_loss = abs(sum(losses, D(0)))
            pf = decimal_text(gross_win / gross_loss) if gross_loss > 0 else None
            holds = [
                (
                    datetime.fromisoformat(p["closed_at"])
                    - datetime.fromisoformat(p["opened_at"])
                ).total_seconds()
                for p in trades
                if p.get("closed_at") and p.get("opened_at")
            ]
            item = {
                "policy": pol,
                "trades": len(trades),
                "realized": acc.get("realized", "0"),
                "max_drawdown": acc.get("max_drawdown", "0"),
                "win_rate": decimal_text(D(len(wins)) / len(trades)) if trades else None,
                "profit_factor": pf,
                "avg_hold_seconds": round(sum(holds) / len(holds), 1) if holds else None,
            }
            if pol == "partial_tp_trailing":
                total_started = len(trades) + sum(
                    1
                    for _ in self._partial_positions_summary()
                )
                item["tp1_reach_rate"] = (
                    decimal_text(D(tp1_hits) / total_started) if total_started else None
                )
                item["tp2_reach_rate"] = (
                    decimal_text(D(tp2_hits) / total_started) if total_started else None
                )
                item["runner_exit_reasons"] = runner_reasons
            out.append(item)
        return {"policies": out}
