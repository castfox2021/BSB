import re
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from functools import wraps
from zoneinfo import ZoneInfo

D = Decimal


def decimal_text(value):
    return format(value, "f") if isinstance(value, Decimal) else str(value)


def decimal_math(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with localcontext() as context:
            context.prec = 80
            return function(*args, **kwargs)

    return wrapped


def amount(value, zero=False):
    if (
        not isinstance(value, str)
        or len(value) > 64
        or not re.fullmatch(r"(0|[1-9][0-9]*)(\.[0-9]{1,24})?", value)
    ):
        raise ValueError("금액·수량은 양의 Decimal 문자열로 입력하세요.")
    number = D(value)
    if number < 0 or (number == 0 and not zero):
        raise ValueError("금액·수량 범위 오류")
    return number


def time_at(value):
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if (
        moment.tzinfo is None
        or moment.year < 2020
        or (moment - datetime.now(UTC)).total_seconds() > 60
    ):
        raise ValueError("유효한 체결 시각과 시간대가 필요합니다.")
    return moment.astimezone(UTC)


def rebuild(fills, capital="150000", marks=None):
    marks = marks or {}
    with localcontext() as ctx:
        ctx.prec = 80
        cash = D(capital)
        realized = D(0)
        positions = {}
        executions = []
        campaigns = []
        today = datetime.now(ZoneInfo("Asia/Seoul")).date()
        daily_realized = D(0)
        for row in fills:
            symbol = row["symbol"]
            side = row["side"]
            qty = amount(row["quantity"])
            price = amount(row["price"])
            fee = amount(row["fee_krw"], True)
            p = positions.setdefault(
                symbol,
                {
                    "quantity": D(0),
                    "cost": D(0),
                    "raw_cost": D(0),
                    "campaign": D(0),
                    "opened_at": row["filled_at"],
                },
            )
            net = D(0)
            if side == "BUY":
                if p["quantity"] == 0:
                    p["opened_at"] = row["filled_at"]
                    p["campaign"] = D(0)
                p["quantity"] += qty
                p["cost"] += qty * price + fee
                p["raw_cost"] += qty * price
                cash -= qty * price + fee
            elif side == "SELL":
                if qty > p["quantity"]:
                    raise ValueError(
                        "등록된 보유 수량보다 매도 수량이 많습니다. 누락된 매수 체결을 먼저 등록하세요."
                    )
                ratio = qty / p["quantity"]
                basis = p["cost"] * ratio
                raw = p["raw_cost"] * ratio
                net = qty * price - fee - basis
                cash += qty * price - fee
                realized += net
                p["campaign"] += net
                p["quantity"] -= qty
                p["cost"] -= basis
                p["raw_cost"] -= raw
                if p["quantity"] == 0:
                    campaigns.append(p["campaign"])
            else:
                raise ValueError("side must be BUY or SELL")
            if (
                time_at(row["filled_at"]).astimezone(ZoneInfo("Asia/Seoul")).date()
                == today
            ):
                daily_realized += net
            executions.append({**dict(row), "realized_net_krw": decimal_text(net)})
        output = []
        unreal = D(0)
        valuation_complete = True
        for symbol, p in positions.items():
            if p["quantity"] == 0:
                continue
            mark = marks.get(symbol)
            gross = D(mark) * p["quantity"] - p["cost"] if mark is not None else None
            if gross is not None:
                unreal += gross
            else:
                valuation_complete = False
            output.append(
                {
                    "symbol": symbol,
                    "quantity": decimal_text(p["quantity"]),
                    "average_entry_price": decimal_text(p["raw_cost"] / p["quantity"]),
                    "cost_basis_krw": decimal_text(p["cost"]),
                    "opened_at": p["opened_at"],
                    "mark_price": decimal_text(mark) if mark is not None else None,
                    "unrealized_before_exit_fee_krw": decimal_text(gross)
                    if gross is not None
                    else None,
                }
            )
        streak = 0
        for pnl in reversed(campaigns):
            if pnl >= 0:
                break
            streak += 1
        return {
            "cash_krw": decimal_text(cash),
            "realized_net_krw": decimal_text(realized),
            "daily_realized_krw": decimal_text(daily_realized),
            "unrealized_before_exit_fee_krw": decimal_text(unreal)
            if valuation_complete
            else None,
            "valuation_complete": valuation_complete,
            "positions": output,
            "executions": executions,
            "consecutive_losses": streak,
            "closed_campaign_pnls": [decimal_text(value) for value in campaigns],
            "capital_channel": "MANUAL",
            "account_confirmed": False,
        }
