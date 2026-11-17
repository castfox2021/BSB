"""Unified Ledger, Decimal Moving-Average + Lot Allocation P&L Engine, and Task 18/18-A Analytics."""

import csv
import io
import json
import math
import uuid
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from xml.sax.saxutils import escape

from .strategy_report import _csv_safe

D = Decimal
KST = timezone(timedelta(hours=9))
CALC_VERSION = "18.0-ma-lot"


def _d(val, default="0") -> Decimal:
    try:
        if val is None or val == "":
            return D(default)
        return D(str(val))
    except Exception:
        return D(default)


def _fmt(val: Decimal | None, places: int = 2) -> str | None:
    if val is None:
        return None
    q = val.quantize(D(10) ** -places) if places > 0 else val.quantize(D("1"))
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _period_bounds(filters: dict) -> tuple[datetime | None, datetime | None]:
    period = str(filters.get("period") or "all").lower()
    now_kst = datetime.now(KST)
    if period == "today":
        start = now_kst.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        return start, None
    if period == "7d":
        return (now_kst - timedelta(days=7)).astimezone(UTC), None
    if period == "30d":
        return (now_kst - timedelta(days=30)).astimezone(UTC), None
    if period == "90d":
        return (now_kst - timedelta(days=90)).astimezone(UTC), None
    if period == "year":
        start = now_kst.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        return start, None
    start_s = filters.get("start_kst")
    end_s = filters.get("end_kst")
    start_dt = None
    end_dt = None
    if start_s:
        s = str(start_s).strip()
        dt = datetime.fromisoformat(s).replace(tzinfo=KST) if len(s) == 10 else datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        start_dt = dt.astimezone(UTC)
    if end_s:
        s = str(end_s).strip()
        dt = (
            datetime.fromisoformat(s).replace(tzinfo=KST) + timedelta(days=1) - timedelta(microseconds=1)
            if len(s) == 10
            else datetime.fromisoformat(s)
        )
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        end_dt = dt.astimezone(UTC)
    return start_dt, end_dt


def _build_minimal_xlsx(headers: list[str], rows: list[list[str]]) -> bytes:
    """Generate a valid OOXML .xlsx workbook using stdlib zipfile."""
    buf = io.BytesIO()
    all_rows = [headers, *rows]

    def col_letter(idx: int) -> str:
        res = ""
        n = idx + 1
        while n > 0:
            n, rem = divmod(n - 1, 26)
            res = chr(65 + rem) + res
        return res

    sheet_rows = []
    for r_idx, row in enumerate(all_rows, start=1):
        cells = []
        for c_idx, val in enumerate(row):
            ref = f"{col_letter(c_idx)}{r_idx}"
            safe_val = escape(_csv_safe(val))
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{safe_val}</t></is></c>')
        sheet_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="TradingAnalytics" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    wb_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buf.getvalue()


class AnalyticsLedger:
    """Unified cost-basis, lot-allocation, P&L, and multi-view analytics engine."""

    def __init__(self, store, market=None):
        self.store = store
        self.market = market

    def set_baseline(self, account_id: str, market: str, initial_quantity: str, avg_cost_krw: str, note: str = "") -> dict:
        qty = _d(initial_quantity)
        cost = _d(avg_cost_krw)
        if qty < 0 or cost < 0:
            raise ValueError("수량과 평균매수단가는 0 이상이어야 합니다.")
        now_iso = datetime.now(UTC).isoformat()
        mkt = market.strip().upper()
        if not mkt.startswith("KRW-"):
            mkt = f"KRW-{mkt}"
        with self.store.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO asset_cost_baselines VALUES (?,?,?,?,?,?)",
                (account_id, mkt, format(qty, "f"), format(cost, "f"), note, now_iso),
            )
        return self.recalculate(account_id)

    def resolve_issue(self, issue_id: str, resolution: str = "RESOLVED") -> dict:
        now_iso = datetime.now(UTC).isoformat()
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE analytics_reconciliation_issues SET status=?,resolved_at=? WHERE id=?",
                (resolution, now_iso, issue_id),
            )
        return {"id": issue_id, "status": resolution}

    def recalculate(self, account_id: str | None = None) -> dict:
        now_iso = datetime.now(UTC).isoformat()
        with self.store.connect() as conn:
            conn.execute("DELETE FROM trade_positions")
            conn.execute("DELETE FROM trade_lot_allocations")
            conn.execute(
                "DELETE FROM analytics_reconciliation_issues WHERE issue_type='MISSING_COST_BASIS' AND status='OPEN'"
            )

            baselines = {}
            for b in conn.execute("SELECT * FROM asset_cost_baselines").fetchall():
                baselines[(b["account_id"], b["market"])] = dict(b)

            fills = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM exchange_fills ORDER BY filled_at ASC, id ASC"
                ).fetchall()
            ]

            grouped: dict[tuple[str, str], list[dict]] = {}
            for f in fills:
                if account_id and f["account_id"] != account_id:
                    continue
                grouped.setdefault((f["account_id"], f["market"]), []).append(f)

            pos_count = 0
            alloc_count = 0
            missing_cost_count = 0

            for (acc_id, market), m_fills in grouped.items():
                pool_qty = D(0)
                pool_cost = D(0)
                pool_buy_fee = D(0)
                first_open_at = None
                cur_pos_id = None
                cur_origin = "UNKNOWN"
                acquired_total = D(0)
                disposed_total = D(0)
                realized_total = D(0)
                fee_total = D(0)
                incomplete = False
                open_buys: list[dict] = []

                base = baselines.get((acc_id, market))
                if base and _d(base["initial_quantity"]) > 0:
                    b_qty = _d(base["initial_quantity"])
                    b_cost = b_qty * _d(base["avg_cost_krw"])
                    pool_qty += b_qty
                    pool_cost += b_cost
                    acquired_total += b_qty
                    first_open_at = base["updated_at"]
                    cur_pos_id = uuid.uuid4().hex
                    cur_origin = "MANUAL"
                    open_buys.append(
                        {"id": "baseline", "filled_at": base["updated_at"], "qty": b_qty}
                    )

                for f in m_fills:
                    qty = _d(f["fill_quantity"])
                    amt = _d(f["fill_amount"])
                    if amt <= 0:
                        amt = qty * _d(f["fill_price"])
                    fee_krw = _d(f["fee_krw_equiv"])
                    fee_total += fee_krw
                    if f["side"] == "BUY":
                        if pool_qty <= 0:
                            cur_pos_id = uuid.uuid4().hex
                            first_open_at = f["filled_at"]
                            cur_origin = f.get("execution_origin") or "UNKNOWN"
                            acquired_total = D(0)
                            disposed_total = D(0)
                            realized_total = D(0)
                        elif f.get("execution_origin") == "BOT":
                            cur_origin = "BOT"
                        pool_qty += qty
                        pool_cost += amt + fee_krw
                        pool_buy_fee += fee_krw
                        acquired_total += qty
                        open_buys.append(
                            {"id": f["id"], "filled_at": f["filled_at"], "qty": qty}
                        )
                    elif f["side"] == "SELL":
                        if pool_qty < qty:
                            missing_qty = qty - pool_qty
                            incomplete = True
                            missing_cost_count += 1
                            conn.execute(
                                "INSERT INTO analytics_reconciliation_issues VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (
                                    uuid.uuid4().hex,
                                    acc_id,
                                    "MISSING_COST_BASIS",
                                    market,
                                    f["id"],
                                    f"{market} 매도 수량({qty}) 중 취득원가가 없는 잔량({missing_qty})이 있습니다. 기초 원가를 입력하거나 이전 거래내역을 업로드하세요.",
                                    json.dumps({"fill_id": f["id"], "missing_qty": str(missing_qty)}),
                                    "OPEN",
                                    now_iso,
                                    None,
                                ),
                            )
                            # Allocate only the known pool_qty without fabricating 0-cost profit
                            alloc_qty = pool_qty
                        else:
                            alloc_qty = qty

                        if alloc_qty > 0 and pool_qty > 0:
                            avg_unit_cost = pool_cost / pool_qty
                            avg_buy_fee_per_unit = pool_buy_fee / pool_qty
                            alloc_cost = avg_unit_cost * alloc_qty
                            alloc_buy_fee = avg_buy_fee_per_unit * alloc_qty
                            alloc_proceeds = (amt * (alloc_qty / qty)) - fee_krw if qty > 0 else D(0)
                            alloc_pnl = alloc_proceeds - alloc_cost
                            alloc_pnl_pct = (alloc_pnl / alloc_cost * 100) if alloc_cost > 0 else None

                            opened_ref = open_buys[0]["filled_at"] if open_buys else f["filled_at"]
                            buy_ref = open_buys[0]["id"] if open_buys else "unknown"
                            hold_secs = max(
                                0,
                                int(
                                    (
                                        datetime.fromisoformat(f["filled_at"])
                                        - datetime.fromisoformat(opened_ref)
                                    ).total_seconds()
                                ),
                            )
                            pos_id = cur_pos_id or uuid.uuid4().hex
                            conn.execute(
                                "INSERT INTO trade_lot_allocations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (
                                    uuid.uuid4().hex,
                                    pos_id,
                                    buy_ref,
                                    f["id"],
                                    market,
                                    format(alloc_qty, "f"),
                                    format(alloc_cost, "f"),
                                    format(alloc_proceeds, "f"),
                                    format(alloc_buy_fee, "f"),
                                    format(fee_krw, "f"),
                                    format(alloc_pnl, "f"),
                                    format(alloc_pnl_pct, "f") if alloc_pnl_pct is not None else None,
                                    hold_secs,
                                    opened_ref,
                                    f["filled_at"],
                                    cur_origin if cur_origin != "UNKNOWN" else f.get("execution_origin", "UNKNOWN"),
                                    "reversion_watch" if cur_origin == "BOT" else "manual",
                                    "18.0" if cur_origin == "BOT" else "manual",
                                    "LIVE_SELL",
                                ),
                            )
                            alloc_count += 1
                            pool_qty -= alloc_qty
                            pool_cost -= alloc_cost
                            pool_buy_fee -= alloc_buy_fee
                            disposed_total += alloc_qty
                            realized_total += alloc_pnl

                        if pool_qty <= D("0.00000001"):
                            pool_qty = D(0)
                            pool_cost = D(0)
                            pool_buy_fee = D(0)
                            open_buys.clear()
                            if cur_pos_id:
                                status = "INCOMPLETE_COST_BASIS" if incomplete else "CLOSED"
                                cost_basis_total = acquired_total * (
                                    (realized_total / disposed_total) if False else D(0)
                                )
                                conn.execute(
                                    "INSERT INTO trade_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (
                                        cur_pos_id,
                                        acc_id,
                                        market,
                                        cur_origin,
                                        "reversion_watch" if cur_origin == "BOT" else "manual",
                                        "18.0" if cur_origin == "BOT" else "manual",
                                        first_open_at or f["filled_at"],
                                        f["filled_at"],
                                        format(acquired_total, "f"),
                                        format(disposed_total, "f"),
                                        "0",
                                        format(cost_basis_total, "f"),
                                        format(realized_total, "f"),
                                        None,
                                        format(fee_total, "f"),
                                        status,
                                        CALC_VERSION,
                                    ),
                                )
                                pos_count += 1
                                cur_pos_id = None
                                incomplete = False

                if pool_qty > D("0.00000001") and cur_pos_id:
                    conn.execute(
                        "INSERT INTO trade_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            cur_pos_id,
                            acc_id,
                            market,
                            cur_origin,
                            "reversion_watch" if cur_origin == "BOT" else "manual",
                            "18.0" if cur_origin == "BOT" else "manual",
                            first_open_at or now_iso,
                            None,
                            format(acquired_total, "f"),
                            format(disposed_total, "f"),
                            format(pool_qty, "f"),
                            format(pool_cost, "f"),
                            format(realized_total, "f"),
                            None,
                            format(fee_total, "f"),
                            "OPEN",
                            CALC_VERSION,
                        ),
                    )
                    pos_count += 1
        return {
            "calculation_version": CALC_VERSION,
            "positions": pos_count,
            "allocations": alloc_count,
            "missing_cost_basis_issues": missing_cost_count,
        }

    def _live_price(self, market: str) -> Decimal | None:
        if not self.market:
            return None
        row = next((r for r in getattr(self.market, "rows", []) if r.get("symbol") == market), None)
        if row and row.get("price"):
            return _d(row["price"])
        return None

    def query_analytics(self, filters: dict) -> dict:
        start_utc, end_utc = _period_bounds(filters)
        market_f = str(filters.get("market") or "ALL").upper()
        origin_f = str(filters.get("origin") or "ALL").upper()
        strategy_f = str(filters.get("strategy") or "ALL")
        outcome_f = str(filters.get("outcome") or "ALL").upper()
        mode_f = str(filters.get("mode") or "LIVE").upper()  # LIVE | PAPER
        account_f = filters.get("account_id")

        with self.store.connect() as conn:
            if mode_f == "PAPER":
                rows = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM reversion_positions WHERE status='CLOSED' ORDER BY closed_at ASC"
                    ).fetchall()
                ]
                lots = []
                for r in rows:
                    closed_dt = datetime.fromisoformat(r["closed_at"])
                    if start_utc and closed_dt < start_utc:
                        continue
                    if end_utc and closed_dt > end_utc:
                        continue
                    if market_f != "ALL" and r["symbol"] != market_f:
                        continue
                    if strategy_f != "ALL" and r["policy"] != strategy_f:
                        continue
                    pnl = _d(r["net_pnl"])
                    cost = _d(r["cost"])
                    if outcome_f == "WIN" and pnl <= 0:
                        continue
                    if outcome_f == "LOSS" and pnl >= 0:
                        continue
                    lots.append(
                        {
                            "id": r["id"],
                            "market": r["symbol"],
                            "execution_origin": "PAPER",
                            "strategy_name": r["policy"],
                            "strategy_version": r["policy"],
                            "allocated_quantity": r["quantity"],
                            "allocated_cost": r["cost"],
                            "allocated_proceeds": str(cost + pnl),
                            "allocated_buy_fee": str(cost * D("0.0004")),
                            "allocated_sell_fee": str((cost + pnl) * D("0.0004")),
                            "realized_pnl": r["net_pnl"],
                            "realized_pnl_pct": str((pnl / cost * 100) if cost > 0 else D(0)),
                            "holding_seconds": max(
                                0,
                                int((closed_dt - datetime.fromisoformat(r["opened_at"])).total_seconds()),
                            ),
                            "opened_at": r["opened_at"],
                            "closed_at": r["closed_at"],
                            "exit_reason": r.get("exit_reason") or "CLOSED",
                        }
                    )
                open_pos = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM reversion_positions WHERE status='OPEN'"
                    ).fetchall()
                ]
                total_buy = sum((_d(l["allocated_cost"]) for l in lots), D(0))
                total_sell = sum((_d(l["allocated_proceeds"]) for l in lots), D(0))
                unrealized = D(0)
                for op in open_pos:
                    lp = self._live_price(op["symbol"])
                    if lp:
                        unrealized += _d(op["quantity"]) * lp - _d(op["cost"])
                issues = []
            else:
                lots = []
                clauses: list[str] = []
                params: list = []
                if account_f:
                    lot_base = "SELECT l.* FROM trade_lot_allocations l JOIN trade_positions p ON l.position_id=p.id"
                    clauses.append("p.account_id=?")
                    params.append(account_f)
                    col_prefix = "l."
                else:
                    lot_base = "SELECT * FROM trade_lot_allocations"
                    col_prefix = ""
                if start_utc:
                    clauses.append(f"{col_prefix}closed_at>=?")
                    params.append(start_utc.isoformat())
                if end_utc:
                    clauses.append(f"{col_prefix}closed_at<=?")
                    params.append(end_utc.isoformat())
                if market_f != "ALL":
                    clauses.append(f"{col_prefix}market=?")
                    params.append(market_f)
                if origin_f != "ALL":
                    clauses.append(f"{col_prefix}execution_origin=?")
                    params.append(origin_f)
                if strategy_f != "ALL":
                    clauses.append(f"{col_prefix}strategy_name=?")
                    params.append(strategy_f)
                where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
                for r in conn.execute(
                    f"{lot_base}{where_sql} ORDER BY {col_prefix}closed_at ASC", tuple(params)
                ):
                    d = dict(r)
                    closed_dt = datetime.fromisoformat(d["closed_at"])
                    if start_utc and closed_dt < start_utc:
                        continue
                    if end_utc and closed_dt > end_utc:
                        continue
                    pnl = _d(d["realized_pnl"])
                    if outcome_f == "WIN" and pnl <= 0:
                        continue
                    if outcome_f == "LOSS" and pnl >= 0:
                        continue
                    lots.append(d)

                open_pos = [
                    dict(r)
                    for r in (
                        conn.execute("SELECT * FROM trade_positions WHERE status='OPEN' AND account_id=?", (account_f,))
                        if account_f
                        else conn.execute("SELECT * FROM trade_positions WHERE status='OPEN'")
                    ).fetchall()
                ]
                fill_where = " AND account_id=?" if account_f else ""
                fill_p = (account_f,) if account_f else ()
                buy_fills = conn.execute(
                    f"SELECT fill_amount, fee_krw_equiv, filled_at, market, execution_origin FROM exchange_fills WHERE side='BUY'{fill_where}",
                    fill_p,
                ).fetchall()
                sell_fills = conn.execute(
                    f"SELECT fill_amount, fee_krw_equiv, filled_at, market, execution_origin FROM exchange_fills WHERE side='SELL'{fill_where}",
                    fill_p,
                ).fetchall()
                total_buy = sum((_d(b["fill_amount"]) for b in buy_fills), D(0))
                total_sell = sum((_d(s["fill_amount"]) for s in sell_fills), D(0))
                unrealized = D(0)
                for op in open_pos:
                    lp = self._live_price(op["market"])
                    if lp:
                        unrealized += _d(op["remaining_quantity"]) * lp - _d(op["cost_basis"])
                issues = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM analytics_reconciliation_issues WHERE status='OPEN' ORDER BY created_at DESC"
                    ).fetchall()
                ]

        wins = [l for l in lots if _d(l["realized_pnl"]) > 0]
        losses = [l for l in lots if _d(l["realized_pnl"]) < 0]
        total_realized = sum((_d(l["realized_pnl"]) for l in lots), D(0))
        total_fees = sum(
            (_d(l["allocated_buy_fee"]) + _d(l["allocated_sell_fee"]) for l in lots), D(0)
        )
        gross_win = sum((_d(l["realized_pnl"]) for l in wins), D(0))
        gross_loss = abs(sum((_d(l["realized_pnl"]) for l in losses), D(0)))
        pf = (gross_win / gross_loss) if gross_loss > 0 else None
        win_rate = (D(len(wins)) / len(lots) * 100) if lots else None
        avg_win = (gross_win / len(wins)) if wins else None
        avg_loss = (-gross_loss / len(losses)) if losses else None
        payoff_ratio = (avg_win / abs(avg_loss)) if (avg_win and avg_loss and avg_loss != 0) else None
        expectancy = (total_realized / len(lots)) if lots else None
        avg_hold = (
            round(sum(int(l["holding_seconds"]) for l in lots) / len(lots), 1)
            if lots
            else None
        )

        # Cumulative equity, MDD, streaks, daily/monthly/hourly/weekday/market/origin/strategy
        equity = D("150000")
        peak_eq = equity
        max_dd = D(0)
        cur_win_s = cur_loss_s = max_win_s = max_loss_s = 0
        cum_curve = []
        daily_map: dict[str, Decimal] = {}
        monthly_map: dict[str, Decimal] = {}
        hourly_map: dict[int, dict] = {h: {"hour": h, "trades": 0, "pnl": D(0), "wins": 0} for h in range(24)}
        weekday_map: dict[int, dict] = {w: {"weekday": w, "trades": 0, "pnl": D(0), "wins": 0} for w in range(7)}
        market_map: dict[str, dict] = {}
        origin_map: dict[str, dict] = {}
        strategy_map: dict[str, dict] = {}
        returns_pct: list[float] = []

        for l in lots:
            pnl = _d(l["realized_pnl"])
            cost = _d(l["allocated_cost"])
            if pnl > 0:
                cur_win_s += 1
                cur_loss_s = 0
                max_win_s = max(max_win_s, cur_win_s)
            elif pnl < 0:
                cur_loss_s += 1
                cur_win_s = 0
                max_loss_s = max(max_loss_s, cur_loss_s)
            equity += pnl
            peak_eq = max(peak_eq, equity)
            dd = ((peak_eq - equity) / peak_eq * 100) if peak_eq > 0 else D(0)
            max_dd = max(max_dd, dd)

            closed_kst = datetime.fromisoformat(l["closed_at"]).astimezone(KST)
            day_key = closed_kst.strftime("%Y-%m-%d")
            mon_key = closed_kst.strftime("%Y-%m")
            daily_map[day_key] = daily_map.get(day_key, D(0)) + pnl
            monthly_map[mon_key] = monthly_map.get(mon_key, D(0)) + pnl

            h_bucket = hourly_map[closed_kst.hour]
            h_bucket["trades"] += 1
            h_bucket["pnl"] += pnl
            if pnl > 0:
                h_bucket["wins"] += 1

            w_bucket = weekday_map[closed_kst.weekday()]
            w_bucket["trades"] += 1
            w_bucket["pnl"] += pnl
            if pnl > 0:
                w_bucket["wins"] += 1

            mkt = l["market"]
            mb = market_map.setdefault(mkt, {"market": mkt, "trades": 0, "wins": 0, "pnl": D(0), "fees": D(0)})
            mb["trades"] += 1
            mb["pnl"] += pnl
            mb["fees"] += _d(l["allocated_buy_fee"]) + _d(l["allocated_sell_fee"])
            if pnl > 0:
                mb["wins"] += 1

            orig = l.get("execution_origin") or "UNKNOWN"
            ob = origin_map.setdefault(orig, {"origin": orig, "trades": 0, "wins": 0, "pnl": D(0)})
            ob["trades"] += 1
            ob["pnl"] += pnl
            if pnl > 0:
                ob["wins"] += 1

            strat = l.get("strategy_name") or "unknown"
            sb = strategy_map.setdefault(strat, {"strategy": strat, "trades": 0, "wins": 0, "pnl": D(0), "hold_sum": 0})
            sb["trades"] += 1
            sb["pnl"] += pnl
            sb["hold_sum"] += int(l["holding_seconds"])
            if pnl > 0:
                sb["wins"] += 1

            if cost > 0:
                returns_pct.append(float(pnl / cost))

            cum_curve.append(
                {
                    "timestamp": l["closed_at"],
                    "cumulative_pnl_krw": _fmt(equity - D("150000"), 2),
                    "equity_krw": _fmt(equity, 2),
                    "drawdown_pct": _fmt(dd, 3),
                }
            )

        sharpe = None
        sortino = None
        if len(returns_pct) >= 5:
            mean_r = sum(returns_pct) / len(returns_pct)
            var_r = sum((r - mean_r) ** 2 for r in returns_pct) / len(returns_pct)
            std_r = math.sqrt(var_r)
            if std_r > 0:
                sharpe = round(mean_r / std_r * math.sqrt(len(returns_pct)), 3)
            downside = [r for r in returns_pct if r < 0]
            if downside:
                dd_std = math.sqrt(sum(r ** 2 for r in downside) / len(downside))
                if dd_std > 0:
                    sortino = round(mean_r / dd_std * math.sqrt(len(returns_pct)), 3)

        summary = {
            "mode": mode_f,
            "calculation_version": CALC_VERSION,
            "total_buy_krw": _fmt(total_buy, 2),
            "total_sell_krw": _fmt(total_sell, 2),
            "total_fees_krw": _fmt(total_fees, 2),
            "realized_pnl_krw": _fmt(total_realized, 2),
            "unrealized_pnl_krw": _fmt(unrealized, 2),
            "net_equity_krw": _fmt(equity + unrealized, 2),
            "trade_count": len(lots),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": _fmt(win_rate, 2),
            "avg_profit_krw": _fmt(avg_win, 2),
            "avg_loss_krw": _fmt(avg_loss, 2),
            "payoff_ratio": _fmt(payoff_ratio, 3),
            "profit_factor": _fmt(pf, 3),
            "expectancy_krw": _fmt(expectancy, 2),
            "max_drawdown_pct": _fmt(max_dd, 3),
            "avg_holding_seconds": avg_hold,
            "max_consecutive_wins": max_win_s,
            "max_consecutive_losses": max_loss_s,
            "open_positions_count": len(open_pos),
            "open_issues_count": len(issues),
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
        }

        return {
            "summary": summary,
            "charts": {
                "cumulative_pnl": cum_curve[-200:],
                "daily_pnl": [{"date": k, "pnl_krw": _fmt(v, 2)} for k, v in sorted(daily_map.items())],
                "monthly_pnl": [{"month": k, "pnl_krw": _fmt(v, 2)} for k, v in sorted(monthly_map.items())],
                "by_market": [
                    {
                        "market": v["market"],
                        "trades": v["trades"],
                        "win_rate": _fmt(D(v["wins"]) / v["trades"] * 100, 1) if v["trades"] else None,
                        "pnl_krw": _fmt(v["pnl"], 2),
                        "fees_krw": _fmt(v["fees"], 2),
                    }
                    for v in sorted(market_map.values(), key=lambda x: x["pnl"], reverse=True)
                ],
                "by_origin": [
                    {
                        "origin": v["origin"],
                        "trades": v["trades"],
                        "win_rate": _fmt(D(v["wins"]) / v["trades"] * 100, 1) if v["trades"] else None,
                        "pnl_krw": _fmt(v["pnl"], 2),
                    }
                    for v in origin_map.values()
                ],
                "by_strategy": [
                    {
                        "strategy": v["strategy"],
                        "trades": v["trades"],
                        "win_rate": _fmt(D(v["wins"]) / v["trades"] * 100, 1) if v["trades"] else None,
                        "pnl_krw": _fmt(v["pnl"], 2),
                        "avg_hold_seconds": round(v["hold_sum"] / v["trades"], 1) if v["trades"] else 0,
                    }
                    for v in strategy_map.values()
                ],
                "hourly_kst": [
                    {
                        "hour": h["hour"],
                        "trades": h["trades"],
                        "pnl_krw": _fmt(h["pnl"], 2),
                        "low_sample": h["trades"] < 5,
                    }
                    for h in hourly_map.values()
                ],
                "weekday_kst": [
                    {
                        "weekday": w["weekday"],
                        "trades": w["trades"],
                        "pnl_krw": _fmt(w["pnl"], 2),
                        "win_rate": _fmt(D(w["wins"]) / w["trades"] * 100, 1) if w["trades"] else None,
                        "low_sample": w["trades"] < 5,
                    }
                    for w in weekday_map.values()
                ],
            },
            "trades": list(reversed(lots[-100:])),
            "open_positions": open_pos,
            "issues": issues,
        }

    def export_data(self, fmt: str, filters: dict) -> tuple[bytes, str, str]:
        data = self.query_analytics(filters)
        fmt = fmt.lower()
        if fmt == "json":
            return (
                json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
                "application/json",
                "bsb-trading-analytics.json",
            )
        if fmt == "markdown":
            s = data["summary"]
            md = "\n".join(
                [
                    "# BITHUMB Unified Trading Analytics Report",
                    f"- Mode: **{s['mode']}** · Calculation: `{s['calculation_version']}`",
                    f"- Trades: **{s['trade_count']}** (Wins {s['wins']} / Losses {s['losses']} · Win Rate {s['win_rate'] or 'N/A'}%)",
                    f"- Realized P&L: **{s['realized_pnl_krw']} KRW** · Fees: **{s['total_fees_krw']} KRW**",
                    f"- Profit Factor: **{s['profit_factor'] or 'N/A'}** · Max Drawdown: **-{s['max_drawdown_pct']}%**",
                    "",
                    "## By Market",
                    *[
                        f"- {m['market']}: {m['trades']} trades, P&L {m['pnl_krw']} KRW"
                        for m in data["charts"]["by_market"]
                    ],
                ]
            )
            return md.encode("utf-8"), "text/markdown; charset=utf-8", "bsb-trading-analytics.md"

        headers = [
            "closed_at",
            "market",
            "execution_origin",
            "strategy_name",
            "allocated_quantity",
            "allocated_cost",
            "allocated_proceeds",
            "realized_pnl",
            "realized_pnl_pct",
            "holding_seconds",
            "exit_reason",
        ]
        rows = [[str(t.get(h) or "") for h in headers] for t in data["trades"]]
        if fmt == "xlsx":
            return (
                _build_minimal_xlsx(headers, rows),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "bsb-trading-analytics.xlsx",
            )
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(headers)
        for r in rows:
            w.writerow([_csv_safe(c) for c in r])
        return (
            buf.getvalue().encode("utf-8-sig"),
            "text/csv; charset=utf-8",
            "bsb-trading-analytics.csv",
        )
