from decimal import Decimal, localcontext

from .ledger import amount, decimal_text
from .strategy import entry, parameters

D = Decimal


def validate_bars(bars):
    if not 40 <= len(bars) <= 5000:
        raise ValueError("40–5000개의 연속 완료 1분봉이 필요합니다.")
    previous = None
    for bar in bars:
        if (
            type(bar["start"]) is not int
            or bar["start"] % 60
            or not bar.get("complete")
            or not bar.get("closed")
        ):
            raise ValueError("불완전 또는 미확정 캔들")
        if previous is not None and bar["start"] - previous != 60:
            raise ValueError("누락·중복·역순 캔들")
        previous = bar["start"]
        o, h, low, c = [amount(bar[k]) for k in ("open", "high", "low", "close")]
        amount(bar["volume"], True)
        if not low <= min(o, c) <= max(o, c) <= h:
            raise ValueError("invalid OHLC")
    return bars


def run(kind, params, bars, fee="0.0004", slippage="0.001", warmup=None):
    parameters(kind, params)
    validate_bars(bars)
    if warmup and (
        warmup[-1]["start"] >= bars[0]["start"]
        or any(not b.get("complete") or not b.get("closed") for b in warmup)
        or any(
            warmup[i]["start"] - warmup[i - 1]["start"] != 60
            for i in range(1, len(warmup))
        )
    ):
        raise ValueError(
            "warmup must be complete, chronological, and strictly before the evaluation period"
        )
    fee_d = amount(fee, True)
    slip = amount(slippage, True)
    if fee_d > D("0.01") or slip > D("0.05"):
        raise ValueError("비용 가정 범위 오류")
    with localcontext() as ctx:
        ctx.prec = 80
        cash = D("150000")
        quantity = D(0)
        cost = D(0)
        entry_price = D(0)
        held = 0
        pending = False
        peak = cash
        mdd = D(0)
        fees = D(0)
        slip_cost = D(0)
        trades = []
        curve = []
        history = list(warmup or [])
        opened = None
        for bar in bars:
            if pending and quantity == 0:
                price = D(bar["open"]) * (1 + slip)
                budget = min(D("50000"), cash - D("50000"))
                if budget > 0:
                    quantity = budget / (price * (1 + fee_d))
                    cost = budget
                    entry_price = price
                    pos_peak = price
                    trail_active = False
                    cash -= cost
                    held = 0
                    fees += quantity * price * fee_d
                    slip_cost += quantity * (price - D(bar["open"]))
                    opened = bar["start"]
                pending = False
            if quantity:
                held += 1
                stop = entry_price * (1 - D(params["stop_loss"]))
                target = entry_price * (1 + D(params["take_profit"]))
                trailing_drop = D(params.get("trailing_drop", "0.008"))
                pos_peak = max(pos_peak, D(bar["high"]))
                reference = None
                reason = ""
                if not trail_active and D(bar["open"]) <= stop:
                    reference = D(bar["open"])
                    reason = "gap_stop"
                elif not trail_active and D(bar["low"]) <= stop:
                    reference = stop
                    reason = (
                        "stop"  # If both hit, stop wins: conservative OHLC ambiguity.
                    )
                else:
                    if pos_peak >= target:
                        trail_active = True
                if (
                    reference is None
                    and trail_active
                    and D(bar["low"]) <= pos_peak * (1 - trailing_drop)
                ):
                    trigger = pos_peak * (1 - trailing_drop)
                    reference = min(D(bar["open"]), trigger) if D(bar["open"]) <= trigger else trigger
                    reason = "target"
                elif reference is None and held >= params["max_bars"]:
                    reference = D(bar["close"])
                    reason = "time"
                if reference is not None:
                    price = reference * (1 - slip)
                    sell_fee = quantity * price * fee_d
                    proceeds = quantity * price - sell_fee
                    fees += sell_fee
                    slip_cost += quantity * (reference - price)
                    pnl = proceeds - cost
                    cash += proceeds
                    trades.append(
                        {
                            "entry_at": opened,
                            "exit_at": bar["start"] + 60,
                            "net_pnl": decimal_text(pnl),
                            "reason": reason,
                        }
                    )
                    quantity = D(0)
            equity = cash + quantity * D(bar["close"]) * (1 - slip) * (1 - fee_d)
            peak = max(peak, equity)
            mdd = max(mdd, (peak - equity) / peak)
            curve.append({"at": bar["start"] + 60, "equity": decimal_text(equity)})
            history.append(bar)
            pending = quantity == 0 and entry(kind, history, params)[0]
        if quantity:
            reference = D(bars[-1]["close"])
            price = reference * (1 - slip)
            sell_fee = quantity * price * fee_d
            proceeds = quantity * price - sell_fee
            cash += proceeds
            fees += sell_fee
            slip_cost += quantity * (reference - price)
            trades.append(
                {
                    "entry_at": opened,
                    "exit_at": bars[-1]["start"] + 60,
                    "net_pnl": decimal_text(proceeds - cost),
                    "reason": "end",
                }
            )
            peak = max(peak, cash)
            mdd = max(mdd, (peak - cash) / peak)
        wins = [D(t["net_pnl"]) for t in trades if D(t["net_pnl"]) > 0]
        losses = [-D(t["net_pnl"]) for t in trades if D(t["net_pnl"]) < 0]
        count = len(trades)
        net = cash - D("150000")
        return {
            "initial_capital": "150000",
            "final_capital": decimal_text(cash),
            "net_profit": decimal_text(net),
            "net_return": decimal_text(net / D("150000")),
            "trades_count": count,
            "win_rate": decimal_text(D(len(wins)) / count) if count else None,
            "payoff_ratio": decimal_text(
                (sum(wins) / len(wins)) / (sum(losses) / len(losses))
            )
            if wins and losses
            else None,
            "profit_factor": decimal_text(sum(wins) / sum(losses)) if losses else None,
            "expectancy_krw": decimal_text(net / count) if count else None,
            "mdd": decimal_text(mdd),
            "fees_krw": decimal_text(fees),
            "slippage_krw": decimal_text(slip_cost),
            "fee_rate_assumption": fee,
            "slippage_assumption": slippage,
            "trades": trades,
            "equity_curve": curve,
            "execution": "next_open; stop_first; adverse_slippage",
            "simulated": True,
        }
