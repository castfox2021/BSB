import json
from datetime import UTC, datetime
from decimal import Decimal, localcontext

from .ledger import amount, decimal_text
from .review_events import archive_candles


def epoch(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def row_bar(row):
    return {
        **dict(row),
        "closed": bool(row["closed"]),
        "complete": bool(row["complete"]),
    }


class Candles:
    def __init__(self, store):
        self.store = store

    def load(self, symbol, tf=60, limit=300, complete=True):
        with self.store.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM market_candles WHERE symbol=? AND timeframe=?
               AND closed=1 AND (?=0 OR complete=1) ORDER BY start DESC LIMIT ?""",
                (symbol, tf, int(complete), limit),
            ).fetchall()
            return [row_bar(row) for row in reversed(rows)]

    def load_contiguous(self, symbol, tf=60, limit=300, complete=True):
        """Return the newest uninterrupted closed-candle run only."""
        rows = self.load(symbol, tf=tf, limit=limit, complete=complete)
        if not rows:
            return []
        start = len(rows) - 1
        while start > 0 and rows[start]["start"] - rows[start - 1]["start"] == tf:
            start -= 1
        return rows[start:]

    def aggregate(self, conn, symbol, start, tf, now):
        begin = start // tf * tf
        rows = conn.execute(
            "SELECT * FROM market_candles WHERE symbol=? AND timeframe=60 AND start>=? AND start<? ORDER BY start",
            (symbol, begin, begin + tf),
        ).fetchall()
        if not rows:
            return
        complete = (
            len(rows) == tf // 60
            and [r["start"] for r in rows] == list(range(begin, begin + tf, 60))
            and all(r["closed"] and r["complete"] for r in rows)
        )
        with localcontext() as ctx:
            ctx.prec = 80
            values = (
                symbol,
                tf,
                begin,
                begin + tf,
                rows[0]["open"],
                decimal_text(max(Decimal(r["high"]) for r in rows)),
                decimal_text(min(Decimal(r["low"]) for r in rows)),
                rows[-1]["close"],
                decimal_text(sum(Decimal(r["volume"]) for r in rows)),
                decimal_text(sum(Decimal(r["turnover"]) for r in rows)),
                rows[0]["first_key"],
                rows[-1]["last_key"],
                int(begin + tf <= now and all(r["closed"] for r in rows)),
                int(complete),
                "AGGREGATE",
                datetime.now(UTC).isoformat(),
            )
        conn.execute(
            "INSERT OR REPLACE INTO market_candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            values,
        )

    def seed(self, symbol, raw, now):
        if not isinstance(raw, list):
            raise ValueError("invalid REST candle response")
        starts = []
        with self.store.connect() as conn:
            for item in sorted(raw, key=lambda x: x["candle_date_time_utc"]):
                start = epoch(item["candle_date_time_utc"] + "Z")
                if start % 60 or start + 60 > now - 5:
                    continue
                o, h, low, c = [
                    amount(format(Decimal(decimal_text(item[k])), "f"))
                    for k in ("opening_price", "high_price", "low_price", "trade_price")
                ]
                if not low <= min(o, c) <= max(o, c) <= h:
                    raise ValueError("invalid REST OHLC")
                volume = amount(
                    format(Decimal(decimal_text(item["candle_acc_trade_volume"])), "f"),
                    True,
                )
                turnover = amount(
                    format(Decimal(decimal_text(item["candle_acc_trade_price"])), "f"),
                    True,
                )
                conn.execute(
                    "INSERT OR REPLACE INTO market_candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        symbol,
                        60,
                        start,
                        start + 60,
                        decimal_text(o),
                        decimal_text(h),
                        decimal_text(low),
                        decimal_text(c),
                        decimal_text(volume),
                        decimal_text(turnover),
                        "REST",
                        "REST",
                        1,
                        1,
                        "REST",
                        datetime.now(UTC).isoformat(),
                    ),
                )
                starts.append(start)
            if starts:
                archive_candles(
                    conn,
                    conn.execute(
                        "SELECT * FROM market_candles WHERE symbol=? AND timeframe=60 AND start>=? AND start<=?",
                        (symbol, min(starts), max(starts)),
                    ).fetchall(),
                    source="REST_RECEIVED",
                )
            for start in starts:
                for tf in (180, 300):
                    self.aggregate(conn, symbol, start, tf, now)
        return starts

    def tick(self, event, coverage_start, now, _connection=None):
        at = epoch(event["exchange_at"])
        start = at // 60 * 60
        symbol = event["symbol"]
        key = event["exchange_at"] + ":" + event["event_id"]
        repair = False
        from contextlib import nullcontext

        with (
            nullcontext(_connection)
            if _connection is not None
            else self.store.connect()
        ) as conn:
            result = conn.execute(
                "INSERT OR IGNORE INTO market_events VALUES (?,?,?,?,?,?)",
                (
                    event["event_id"],
                    symbol,
                    event["event_type"],
                    event["exchange_at"],
                    event["received_at"],
                    json.dumps(event),
                ),
            )
            if not result.rowcount or event.get("stream_type") != "REALTIME":
                return False
            row = conn.execute(
                "SELECT * FROM market_candles WHERE symbol=? AND timeframe=60 AND start=?",
                (symbol, start),
            ).fetchone()
            if row and (row["origin"] == "REST" or row["closed"]):
                if epoch(event["received_at"]) > epoch(row["updated_at"]):
                    conn.execute(
                        "UPDATE market_candles SET complete=0 WHERE symbol=? AND start<? AND end>?",
                        (symbol, start + 60, start),
                    )
                    repair = True
                return repair
            price = amount(event["price"])
            qty = amount(event["quantity"])
            with localcontext() as ctx:
                ctx.prec = 80
                if row:
                    values = (
                        row["open"] if row["first_key"] <= key else decimal_text(price),
                        decimal_text(max(Decimal(row["high"]), price)),
                        decimal_text(min(Decimal(row["low"]), price)),
                        row["close"] if row["last_key"] >= key else decimal_text(price),
                        decimal_text(Decimal(row["volume"]) + qty),
                        decimal_text(Decimal(row["turnover"]) + price * qty),
                        min(row["first_key"], key),
                        max(row["last_key"], key),
                    )
                else:
                    values = (
                        decimal_text(price),
                        decimal_text(price),
                        decimal_text(price),
                        decimal_text(price),
                        decimal_text(qty),
                        decimal_text(price * qty),
                        key,
                        key,
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO market_candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        symbol,
                        60,
                        start,
                        start + 60,
                        *values,
                        0,
                        int(
                            coverage_start <= start and (row is None or row["complete"])
                        ),
                        "WS",
                        datetime.now(UTC).isoformat(),
                    ),
                )
        return repair

    def tick_batch(self, events):
        repairs = set()
        with self.store.connect() as conn:
            for event, coverage, at in events:
                if self.tick(event, coverage, at, conn):
                    repairs.add(event["symbol"])
        return repairs

    def finalize_all(self, now):
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT symbol,start FROM market_candles WHERE timeframe=60 AND closed=0 AND end<=?",
                (now - 40,),
            ).fetchall()
            conn.execute(
                "UPDATE market_candles SET closed=1 WHERE timeframe=60 AND closed=0 AND end<=?",
                (now - 40,),
            )
            for row in rows:
                archive_candles(
                    conn,
                    conn.execute(
                        "SELECT * FROM market_candles WHERE symbol=? AND timeframe=60 AND start=?",
                        (row["symbol"], row["start"]),
                    ).fetchall(),
                    source="WS_CLOSED",
                )
                for tf in (180, 300):
                    self.aggregate(conn, row["symbol"], row["start"], tf, now)

    def finalize(self, symbol, now):
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT start FROM market_candles WHERE symbol=? AND timeframe=60 AND closed=0 AND end<=?",
                (symbol, now - 40),
            ).fetchall()
            conn.execute(
                "UPDATE market_candles SET closed=1 WHERE symbol=? AND timeframe=60 AND closed=0 AND end<=?",
                (symbol, now - 40),
            )
            for row in rows:
                for tf in (180, 300):
                    self.aggregate(conn, symbol, row["start"], tf, now)

    def gap(self, symbol, since, reason):
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE market_candles SET complete=0 WHERE symbol=? AND end>=? AND origin!=?",
                (symbol, int(since), "REST"),
            )
            archive_candles(
                conn,
                conn.execute(
                    "SELECT * FROM market_candles WHERE symbol=? AND timeframe=60 AND end>=? AND origin!=?",
                    (symbol, int(since), "REST"),
                ).fetchall(),
                source="GAP_DETECTED",
            )
            conn.execute(
                "INSERT INTO market_issues(symbol,kind,details,created_at) VALUES (?,?,?,?)",
                (symbol, "GAP", reason, datetime.now(UTC).isoformat()),
            )
