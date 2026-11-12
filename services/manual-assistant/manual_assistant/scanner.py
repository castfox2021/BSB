"""Bounded in-memory minute statistics for the entire, dynamic KRW universe."""

import math
from collections import OrderedDict
from decimal import Decimal

from .ledger import decimal_math, decimal_text

D = Decimal


def feature_vector(recent, previous):
    """Shared historical/live features. No future bars or cross-market price scale."""
    volume = sum(D(b["volume"]) for b in recent)
    prior_volume = sum(D(b["volume"]) for b in previous)
    turnover = sum(D(b["turnover"]) for b in recent)
    prior_turnover = sum(D(b["turnover"]) for b in previous)
    change = D(recent[-1]["close"]) / D(recent[0]["open"]) - 1
    last_return = D(recent[-1]["close"]) / D(recent[-1]["open"]) - 1
    return [
        math.log1p(min(float(volume / prior_volume), 1000)) if prior_volume else 0.0,
        math.log1p(min(float(turnover / prior_turnover), 1000))
        if prior_turnover
        else 0.0,
        max(-100.0, min(100.0, float(change * 100))),
        max(-100.0, min(100.0, float(last_return * 100))),
    ]


class RollingScanner:
    def __init__(self):
        self.buckets = {}
        self.seen = OrderedDict()
        self.duplicates = 0
        self.since = {}
        self.history_range = {}

    def connected(self, symbol, at):
        self.since[symbol] = at

    @decimal_math
    def tick(self, event):
        if event.get("stream_type") != "REALTIME":
            return False
        eid = event["event_id"]
        if eid in self.seen:
            self.duplicates += 1
            return False
        self.seen[eid] = None
        while len(self.seen) > 100000:
            self.seen.popitem(last=False)
        from .candles import epoch

        at = epoch(event["exchange_at"])
        minute = at // 60 * 60
        symbol = event["symbol"]
        buckets = self.buckets.setdefault(symbol, {})
        price, qty = D(event["price"]), D(event["quantity"])
        key = (event["exchange_at"], eid)
        bar = buckets.get(minute)
        if bar is None:
            bar = dict(
                open=price,
                high=price,
                low=price,
                close=price,
                volume=D(0),
                turnover=D(0),
                buy=D(0),
                sell=D(0),
                trades=0,
                first=key,
                last=key,
                complete=self.since.get(symbol, minute) <= minute,
            )
            buckets[minute] = bar
        if key < bar["first"]:
            bar["first"], bar["open"] = key, price
        if key >= bar["last"]:
            bar["last"], bar["close"] = key, price
        bar["high"], bar["low"] = max(bar["high"], price), min(bar["low"], price)
        bar["volume"] += qty
        bar["turnover"] += price * qty
        bar["trades"] += 1
        if event.get("side") == "BUY":
            bar["buy"] += qty
        elif event.get("side") == "SELL":
            bar["sell"] += qty
        for old in list(buckets):
            if old < minute - 1800:
                del buckets[old]
        return True

    def seed(self, symbol, bars):
        buckets = self.buckets.setdefault(symbol, {})
        for b in bars:
            at = b["start"]
            old = buckets.get(at, {})
            volume = D(b["volume"])
            buy = min(volume, old.get("buy", D(0)))
            sell = min(volume - buy, old.get("sell", D(0)))
            buckets[at] = dict(
                open=D(b["open"]),
                high=D(b["high"]),
                low=D(b["low"]),
                close=D(b["close"]),
                volume=volume,
                turnover=D(b["turnover"]),
                buy=buy,
                sell=sell,
                trades=old.get("trades", 0),
                first=old.get("first", ("", "")),
                last=old.get("last", ("", "")),
                complete=bool(b.get("complete", True)),
            )
        if bars:
            self.history_range[symbol] = (
                min(b["start"] for b in bars),
                max(b["start"] for b in bars) + 60,
            )
            newest = max(b["start"] for b in bars)
            for old in list(buckets):
                if old < newest - 1800:
                    del buckets[old]

    @decimal_math
    def measure(self, symbol, at):
        minute = int(at) // 60 * 60
        bars = self.buckets.get(symbol, {})
        def completed_window(count):
            window = [bars.get(m) for m in range(minute - count * 60, minute, 60)]
            return (
                window
                if len(window) == count
                and all(bar and bar.get("complete", False) for bar in window)
                else []
            )

        completed1_bars = completed_window(1)
        completed3_bars = completed_window(3)
        completed5_bars = completed_window(5)
        current = [bars[m] for m in range(minute - 240, minute + 1, 60) if m in bars]
        previous = [bars[m] for m in range(minute - 540, minute - 240, 60) if m in bars]
        vol = sum(b["volume"] for b in current)
        old_vol = sum(b["volume"] for b in previous)
        money = sum(b["turnover"] for b in current)
        old_money = sum(b["turnover"] for b in previous)
        start, end = self.history_range.get(symbol, (math.inf, -math.inf))
        baseline_ready = (
            (len(previous) == 5 and all(b.get("complete", False) for b in previous))
            or self.since.get(symbol, math.inf) <= minute - 540
            or (start <= minute - 540 and end >= minute - 240)
        )
        ratio = vol / old_vol if old_vol and baseline_ready else None
        turnover_ratio = money / old_money if old_money and baseline_ready else None
        price_change = (
            current[-1]["close"] / current[0]["open"] - 1 if current else D(0)
        )
        latest = current[-1]["close"] / current[-1]["open"] - 1 if current else D(0)
        recent3 = current[-3:]
        change3 = (
            recent3[-1]["close"] / recent3[0]["open"] - 1
            if recent3
            else D(0)
        )
        completed1 = (
            completed1_bars[-1]["close"] / completed1_bars[-1]["open"] - 1
            if completed1_bars
            else None
        )
        completed3 = (
            completed3_bars[-1]["close"] / completed3_bars[0]["open"] - 1
            if completed3_bars
            else None
        )
        completed5 = (
            completed5_bars[-1]["close"] / completed5_bars[0]["open"] - 1
            if completed5_bars
            else None
        )
        buy = sum(b["buy"] for b in current)
        sell = sum(b["sell"] for b in current)
        count = sum(b["trades"] for b in current)
        known = buy + sell
        pressure = buy / known if known else None
        first_activity = bool(baseline_ready and current and vol > 0 and not old_vol)
        burst = (
            (ratio is not None and ratio >= D("1.5"))
            or (turnover_ratio is not None and turnover_ratio >= D("1.5"))
            or first_activity
        )
        candidate = bool(burst and (price_change > 0 or latest > 0) and count >= 3)
        v = min(ratio or (D(5) if first_activity else D(0)), D(10))
        t = min(turnover_ratio or (D(5) if first_activity else D(0)), D(10))
        score = (
            v * 3
            + t * 3
            + max(-D(20), min(D(30), price_change * 1000))
            + max(-D(10), min(D(15), latest * 1000))
        )
        if pressure is not None:
            score += (pressure - D(".5")) * 20
        score = max(D(0), min(D(100), score))
        return dict(
            baseline_ready=baseline_ready,
            volume_5m=decimal_text(vol),
            turnover_5m=decimal_text(money),
            volume_ratio_5m=decimal_text(ratio) if ratio is not None else None,
            turnover_ratio_5m=decimal_text(turnover_ratio)
            if turnover_ratio is not None
            else None,
            price_change_5m=decimal_text(price_change),
            price_change_1m=decimal_text(latest),
            price_change_3m=decimal_text(change3),
            completed_price_change_1m=decimal_text(completed1)
            if completed1 is not None
            else None,
            completed_price_change_3m=decimal_text(completed3)
            if completed3 is not None
            else None,
            completed_price_change_5m=decimal_text(completed5)
            if completed5 is not None
            else None,
            first_activity=first_activity,
            buy_pressure=decimal_text(pressure) if pressure is not None else None,
            known_aggressor_fraction=decimal_text(known / vol) if vol else None,
            trades_5m=count,
            score=decimal_text(score),
            candidate=candidate,
        )
