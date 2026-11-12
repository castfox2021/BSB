"""Bounded history pages with a fixed insertion-time snapshot for older pages."""

import json
from datetime import UTC, datetime


def snapshot(value):
    if value is None:
        return datetime.now(UTC).isoformat()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.year < 2020 or parsed > datetime.now(UTC):
        raise ValueError("invalid history snapshot")
    return parsed.astimezone(UTC).isoformat()


def envelope(items, total, page, size, at):
    pages = max(1, (total + size - 1) // size)
    return dict(
        items=items,
        total=total,
        page=min(page, pages),
        page_size=size,
        pages=pages,
        snapshot=at,
    )


def history(store, account, kind, page, size, before=None, day=None):
    cap = None
    if before and "|" in before:
        before, raw_cap = before.rsplit("|", 1)
        if not raw_cap.isdigit() or not 0 <= int(raw_cap) <= 2**63 - 1:
            raise ValueError("invalid history snapshot")
        cap = int(raw_cap)
    at = snapshot(before)
    if kind == "signals":
        table, fields, when, order, extra, args = (
            "signals",
            "payload",
            "generated_at",
            "generated_at DESC,id DESC",
            "",
            [],
        )
    elif kind == "orders":
        table = "manual_orders"
        fields = "id,market,side,budget,quantity,status,order_id,signal_id,created_at,updated_at,error"
        when, order, extra, args = (
            "created_at",
            "created_at DESC,id DESC",
            " AND account_id=?",
            [account],
        )
    elif kind == "journal":
        table = "private_fills"
        fields = "trade_id,order_id,symbol,side,quantity,price,fee_krw,fee_status,filled_at,received_at,source"
        when, order, extra, args = (
            "received_at",
            "received_at DESC,trade_id DESC",
            " AND account_id=?",
            [account],
        )
        if day:
            datetime.strptime(day, "%Y-%m-%d")
            extra += " AND date(datetime(filled_at,'+9 hours'))=?"
            args.append(day)
    elif kind == "private-orders":
        table = "private_orders"
        fields = "order_id,market,side,state,executed_quantity,paid_fee,created_at,updated_at"
        when, order, extra, args = (
            "created_at",
            "created_at DESC,order_id DESC",
            " AND account_id=?",
            [account],
        )
    elif kind == "automation":
        table, fields, when, order, extra, args = (
            "automation_events",
            "id,kind,message,signal_id,created_at",
            "created_at",
            "created_at DESC,id DESC",
            " AND account_id=?",
            [account],
        )
    else:
        raise ValueError("invalid history category")
    where = f"{when}<=?{extra}"
    params = [at, *args]
    with store.connect() as conn:
        conn.execute("BEGIN")
        if cap is None:
            cap = conn.execute(
                f"SELECT COALESCE(MAX(rowid),0) FROM {table} WHERE {where}", params
            ).fetchone()[0]
        where += " AND rowid<=?"
        params.append(cap)
        total = conn.execute(
            f"SELECT count(*) FROM {table} WHERE {where}", params
        ).fetchone()[0]
        page = min(page, max(1, (total + size - 1) // size))
        rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT {fields} FROM {table} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                params + [size, (page - 1) * size],
            )
        ]
        if kind == "signals":
            rows = [json.loads(r["payload"]) for r in rows]
        if kind == "journal":
            for row in rows:
                note = conn.execute(
                    "SELECT notes FROM private_notes WHERE account_id=? AND trade_id=?",
                    (account, row["trade_id"]),
                ).fetchone()
                row["notes"] = note[0] if note else ""
    return envelope(rows, total, page, size, at + "|" + str(cap))
