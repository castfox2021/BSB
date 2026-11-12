from decimal import Decimal

from .ledger import decimal_math

D = Decimal
KINDS = ("breakout", "momentum", "pullback")


def parameters(kind, value):
    allowed = {
        "lookback",
        "volume_ratio",
        "stop_loss",
        "take_profit",
        "trailing_drop",
        "max_bars",
    }
    required = {
        "lookback",
        "volume_ratio",
        "stop_loss",
        "take_profit",
        "max_bars",
    }
    if (
        kind not in KINDS
        or not required.issubset(set(value))
        or not set(value).issubset(allowed)
    ):
        raise ValueError("invalid strategy or parameter fields")
    if (
        type(value["lookback"]) is not int
        or not 5 <= value["lookback"] <= 60
        or type(value["max_bars"]) is not int
        or not 1 <= value["max_bars"] <= 120
    ):
        raise ValueError("invalid window")
    ranges = [
        ("volume_ratio", D("0.1"), D("10")),
        ("stop_loss", D("0.001"), D("0.2")),
        ("take_profit", D("0.001"), D("0.5")),
    ]
    if "trailing_drop" in value:
        ranges.append(("trailing_drop", D("0.001"), D("0.1")))
    for key, lo, hi in ranges:
        if not isinstance(value[key], str):
            raise ValueError("Decimal strings required")
        number = D(value[key])
        if not number.is_finite() or not lo <= number <= hi:
            raise ValueError("invalid parameter range")
    if D(value["take_profit"]) <= D(value["stop_loss"]):
        raise ValueError("take profit must exceed stop loss before costs")
    return value


@decimal_math
def entry(kind, history, params):
    lookback = params["lookback"]
    if len(history) < max(lookback + 1, 16):
        return False, ["데이터 준비 중"]
    req_len = max(lookback + 5, 20)
    required = history[-req_len:]
    if any(
        later["start"] - earlier["start"] != 60
        for earlier, later in zip(required, required[1:])
    ):
        return False, ["최근 연속 완료 캔들 부족"]
    recent = history[-lookback - 1 : -1]
    now = history[-1]
    if any(not b.get("complete", True) for b in required):
        return False, ["불완전 캔들"]
    # Every 3m/5m trend uses only groups whose UTC window has fully ended by this decision time.
    end = now["start"] + 60
    trends = []
    for tf in (180, 300):
        grouped = {}
        for bar in required:
            start = bar["start"] // tf * tf
            if start + tf <= end:
                grouped.setdefault(start, []).append(bar)
        full = [
            bars
            for _, bars in sorted(grouped.items())
            if len(bars) == tf // 60 and len({b["start"] for b in bars}) == tf // 60
        ]
        trends.append(bool(full) and D(full[-1][-1]["close"]) > D(full[-1][0]["open"]))
    volume = sum(D(b["volume"]) for b in recent) / len(recent)
    if (
        not all(trends)
        or volume <= 0
        or D(now["volume"]) < volume * D(params["volume_ratio"])
    ):
        return False, ["1·3·5분 추세 또는 거래량 조건 불충족"]
    price = D(now["close"])
    average = sum(D(b["close"]) for b in recent) / len(recent)
    if kind == "breakout":
        passed = price > max(D(b["high"]) for b in recent)
    elif kind == "momentum":
        passed = price > average and price > D(history[-2]["close"])
    else:
        passed = (
            D(history[-2]["close"]) < D(history[-2]["open"])
            and price > D(history[-2]["high"])
            and price > average
        )
    return passed, [
        f"{kind} · 완료 봉 돌파/추세 확인",
        f"거래량 {D(now['volume']) / volume:.2f}배",
        "3·5분 완료 구간 상승 정렬",
    ]
