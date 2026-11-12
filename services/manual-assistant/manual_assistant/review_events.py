"""Allowlisted, immutable decision evidence; contains no transport credentials."""

import hashlib
import json
from datetime import UTC, datetime


def utcnow():
    return datetime.now(UTC).isoformat()


def append_event(conn, kind, identifier, symbol, at, payload):
    key = hashlib.sha256(f"{kind}:{identifier}".encode()).hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO decision_events VALUES(?,?,?,?,?,?,?)",
        (
            key,
            kind,
            identifier,
            symbol,
            at,
            utcnow(),
            json.dumps(payload, ensure_ascii=False),
        ),
    )


def archive_candles(conn, rows, known_at=None, source=None):
    at = known_at or utcnow()
    for row in rows:
        bar = dict(row)
        if bar["timeframe"] != 60:
            continue
        if source:
            bar["archive_source"] = source
        previous = conn.execute(
            "SELECT payload FROM replay_candles WHERE symbol=? AND start=? ORDER BY id DESC LIMIT 1",
            (bar["symbol"], bar["start"]),
        ).fetchone()
        fields = (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
            "closed",
            "complete",
            "origin",
        )
        if previous and all(
            json.loads(previous[0]).get(k) == bar.get(k) for k in fields
        ):
            continue
        conn.execute(
            "INSERT OR IGNORE INTO replay_candles(symbol,start,known_at,payload) VALUES(?,?,?,?)",
            (bar["symbol"], bar["start"], at, json.dumps(bar)),
        )


def capture_signal(conn, market, payload, policy, risk, version=None, exit_reason=None):
    symbol = payload["symbol"]
    latest = market.latest.get(symbol, {})
    # Snapshot only public normalized data, never a client/headers/config object.
    public = {
        key: latest[key] for key in ("trade", "ticker", "orderbook") if key in latest
    }
    at = payload["generated_at"]
    detail = getattr(market, "detail_cache", {}).get(symbol, {})
    feature_snapshot = {k: v for k, v in detail.items() if k != "at"}
    if hasattr(market, "rolling"):
        feature_snapshot["rolling"] = market.rolling.measure(
            symbol, datetime.now(UTC).timestamp()
        )
    ages = {}
    for kind, event in public.items():
        if event.get("exchange_at"):
            ages[kind] = (
                datetime.now(UTC)
                - datetime.fromisoformat(event["exchange_at"].replace("Z", "+00:00"))
            ).total_seconds() * 1000
    append_event(
        conn,
        "SIGNAL",
        payload["id"],
        symbol,
        at,
        {
            "signal": payload,
            "strategy": version,
            "thresholds": {
                k: policy.get(k)
                for k in (
                    "stop_loss_rate",
                    "take_profit_rate",
                    "max_holding_seconds",
                    "execution_fee_rate",
                    "execution_slippage_rate",
                    "max_entry_krw",
                    "daily_loss_alert_krw",
                )
            },
            "risk_checks": risk,
            "market_snapshot": public,
            "ai": detail.get("ai"),
            "feature_snapshot": feature_snapshot,
            "data_age_ms": ages,
            "exit_reason": exit_reason,
            "evidence_status": "CAPTURED_AT_DECISION",
        },
    )
    rows = conn.execute(
        "SELECT * FROM market_candles WHERE symbol=? AND timeframe=60 ORDER BY start DESC LIMIT 100",
        (symbol,),
    ).fetchall()
    archive_candles(conn, rows, source="DECISION_SNAPSHOT")


def capture_fill(
    conn,
    identifier,
    symbol,
    at,
    side,
    quantity,
    price,
    fee,
    order_id=None,
    source="EXCHANGE",
    partial=False,
):
    append_event(
        conn,
        "FILL",
        identifier,
        symbol,
        at,
        {
            "side": side,
            "quantity": quantity,
            "price": price,
            "fee_krw": fee,
            "exchange_order_id": order_id,
            "source": source,
            "partial": partial,
        },
    )
