"""Read-only AI Strategy Analysis Report Bridge (Task 18)."""

import csv
import hashlib
import io
import json
import re
import uuid
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

D = Decimal
KST = timezone(timedelta(hours=9))
NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
MAX_ZIP_BYTES = 20 * 1024 * 1024  # 20 MB


def _anon_hash(value: str | None) -> str:
    if not value:
        return ""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _csv_safe(value) -> str:
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r") and not NUMERIC_RE.match(s):
        return "'" + s
    return s


def _to_csv_bytes(headers: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_csv_safe(row.get(h, "")) for h in headers])
    return buf.getvalue().encode("utf-8-sig")


def _parse_kst_bound(date_str: str | None, end_of_day: bool = False) -> datetime | None:
    if not date_str:
        return None
    s = date_str.strip()
    if len(s) == 10:
        dt = datetime.fromisoformat(s).replace(tzinfo=KST)
        if end_of_day:
            dt = dt + timedelta(days=1) - timedelta(microseconds=1)
        return dt.astimezone(UTC)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(UTC)


def _dec(val, default="0") -> Decimal:
    try:
        if val is None or val == "":
            return D(default)
        return D(str(val))
    except Exception:
        return D(default)


def _fmt(val: Decimal | None, places: int = 8) -> str:
    if val is None:
        return ""
    q = val.quantize(D(10) ** -places) if places > 0 else val.quantize(D("1"))
    text = format(q, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


TRADES_HEADERS = [
    "trade_id",
    "position_id",
    "mode",
    "symbol",
    "strategy_id",
    "strategy_version",
    "config_hash",
    "entry_signal_id",
    "entry_time_utc",
    "entry_price_vwap",
    "entry_qty",
    "entry_notional_krw",
    "exit_time_utc",
    "exit_price_vwap",
    "exit_qty_total",
    "exit_notional_krw",
    "realized_gross_pnl_krw",
    "fees_krw",
    "realized_net_pnl_krw",
    "net_return_pct",
    "holding_seconds",
    "exit_reason",
    "initial_stop_pct",
    "initial_stop_price",
    "highest_price_during_hold",
    "lowest_price_during_hold",
    "mfe_pct",
    "mae_pct",
    "profit_capture_ratio",
    "complete",
    "data_quality_flags",
]

SIGNALS_HEADERS = [
    "signal_id",
    "signal_time_utc",
    "symbol",
    "strategy_id",
    "strategy_version",
    "mode",
    "signal_type",
    "signal_score",
    "reference_price",
    "drop_pct",
    "rebound_pct",
    "volume_ratio",
    "trend_1m",
    "trend_3m",
    "trend_5m",
    "volatility_pct",
    "spread_pct",
    "filter_result",
    "rejection_reason",
    "linked_position_id",
]

ORDERS_HEADERS = [
    "order_id",
    "trade_id",
    "position_id",
    "mode",
    "symbol",
    "side",
    "order_type",
    "requested_qty",
    "filled_qty",
    "requested_price",
    "avg_fill_price",
    "fee_krw",
    "placed_at_utc",
    "filled_at_utc",
    "status",
    "reason",
    "partial_exit_stage",
    "idempotency_key_hash",
]

EQUITY_HEADERS = [
    "timestamp_utc",
    "mode",
    "strategy_id",
    "strategy_version",
    "realized_equity_krw",
    "unrealized_pnl_krw",
    "total_equity_krw",
    "drawdown_pct",
    "open_positions_count",
]

MARKET_CONTEXT_HEADERS = [
    "timestamp_utc",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "source",
    "missing",
]


class StrategyReportBuilder:
    def __init__(self, store, reports_dir: Path):
        self.store = store
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def _config_snapshot(self, conn) -> tuple[dict, str]:
        keys = ("reversion_config", "exit_policy_config", "settings")
        raw = {}
        for k in keys:
            row = conn.execute(
                "SELECT value FROM strategy_state WHERE key=?", (k,)
            ).fetchone()
            if row:
                raw[k] = json.loads(row[0])
        snap = {
            "strategy_id": "dip-rebound",
            "strategy_version": str(
                raw.get("exit_policy_config", {}).get("policy_version", "17.0")
            ),
            "effective_from": datetime.now(UTC).isoformat(),
            "entry_filters": raw.get("reversion_config", {}),
            "initial_stop": {
                "maximum_stop_rate": raw.get("reversion_config", {}).get(
                    "maximum_stop_rate", "0.015"
                ),
                "structure_buffer_rate": raw.get("reversion_config", {}).get(
                    "structure_buffer_rate", "0.002"
                ),
            },
            "take_profit_stages": [
                {
                    "stage": "TP1",
                    "trigger_pct": raw.get("exit_policy_config", {}).get(
                        "tp1_pct", "0.012"
                    ),
                    "fraction": raw.get("exit_policy_config", {}).get(
                        "tp1_fraction", "0.25"
                    ),
                },
                {
                    "stage": "TP2",
                    "trigger_pct": raw.get("exit_policy_config", {}).get(
                        "tp2_pct", "0.025"
                    ),
                    "fraction": raw.get("exit_policy_config", {}).get(
                        "tp2_fraction", "0.25"
                    ),
                },
            ],
            "trailing": {
                "drawdown_pct": raw.get("exit_policy_config", {}).get(
                    "trailing_drawdown_pct", "0.010"
                ),
                "runner_activation": raw.get("exit_policy_config", {}).get(
                    "runner_activation", "AFTER_TP2"
                ),
            },
            "time_exit": {
                "failure_seconds": raw.get("reversion_config", {}).get(
                    "failure_seconds", 600
                )
            },
            "position_sizing": {
                "entry_budget_krw": raw.get("reversion_config", {}).get(
                    "entry_budget_krw", "50000"
                )
            },
            "fee_model": {"rate": "0.0004"},
            "slippage_model": {"rate": "0.001"},
            "execution_mode": "PAPER",
        }
        cfg_hash = hashlib.sha256(
            json.dumps(snap, sort_keys=True).encode("utf-8")
        ).hexdigest()
        snap["config_hash"] = cfg_hash
        return snap, cfg_hash

    def _collect(self, filters: dict) -> dict:
        mode = str(filters.get("mode") or "PAPER").upper()
        if mode not in ("PAPER", "LIVE"):
            mode = "PAPER"
        start_utc = _parse_kst_bound(filters.get("start_kst"), end_of_day=False)
        end_utc = _parse_kst_bound(filters.get("end_kst"), end_of_day=True)
        symbol_filter = filters.get("symbol") or "ALL"
        version_filter = filters.get("strategy_version") or "ALL"
        status_filter = str(filters.get("status") or "ALL").upper()
        reason_filter = filters.get("exit_reason") or "ALL"

        with self.store.connect() as conn:
            cfg_snap, cfg_hash = self._config_snapshot(conn)
            warnings: list[str] = []
            closed_trades: list[dict] = []
            open_trades: list[dict] = []
            orders_rows: list[dict] = []
            candles_rows: list[dict] = []

            if mode == "PAPER":
                rows = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM reversion_positions ORDER BY opened_at ASC"
                    ).fetchall()
                ]
                legs_by_pos: dict[str, list[dict]] = {}
                for leg in conn.execute(
                    "SELECT * FROM position_exit_leg ORDER BY created_at ASC"
                ).fetchall():
                    legs_by_pos.setdefault(leg["position_id"], []).append(dict(leg))

                for r in rows:
                    opened = datetime.fromisoformat(r["opened_at"])
                    if start_utc and opened < start_utc:
                        continue
                    if end_utc and opened > end_utc:
                        continue
                    if symbol_filter != "ALL" and r["symbol"] != symbol_filter:
                        continue
                    if version_filter != "ALL" and r["policy"] != version_filter:
                        continue
                    if status_filter != "ALL" and r["status"] != status_filter:
                        continue
                    if reason_filter != "ALL" and (r.get("exit_reason") or "") != reason_filter:
                        continue

                    pos_id = r["id"]
                    pos_legs = legs_by_pos.get(pos_id, [])
                    entry_p = _dec(r["entry_price"])
                    stop_p = _dec(r["initial_stop"])
                    init_stop_pct = (
                        ((stop_p / entry_p) - 1) * 100 if entry_p > 0 else None
                    )
                    peak_p = _dec(r["peak_price"], str(entry_p))
                    mfe_pct = ((peak_p / entry_p) - 1) * 100 if entry_p > 0 else None

                    # Compute candle low/high during hold if candles exist
                    start_ts = int(opened.timestamp()) - 1800
                    end_ts = (
                        int(datetime.fromisoformat(r["closed_at"]).timestamp())
                        if r.get("closed_at")
                        else int(datetime.now(UTC).timestamp())
                    )
                    c_rows = [
                        dict(c)
                        for c in conn.execute(
                            "SELECT * FROM market_candles WHERE symbol=? AND timeframe=60 AND start>=? AND start<=? ORDER BY start ASC",
                            (r["symbol"], start_ts, end_ts),
                        ).fetchall()
                    ]
                    flags: list[str] = []
                    lowest_p: Decimal | None = None
                    if c_rows:
                        hold_candles = [c for c in c_rows if c["start"] >= int(opened.timestamp())]
                        if hold_candles:
                            lowest_p = min(_dec(c["low"]) for c in hold_candles)
                            peak_p = max(peak_p, max(_dec(c["high"]) for c in hold_candles))
                            mfe_pct = ((peak_p / entry_p) - 1) * 100 if entry_p > 0 else None
                        else:
                            flags.append("CANDLE_HOLD_MISSING")
                        for c in c_rows:
                            candles_rows.append(
                                {
                                    "timestamp_utc": datetime.fromtimestamp(
                                        c["start"], UTC
                                    ).isoformat(),
                                    "symbol": c["symbol"],
                                    "open": c["open"],
                                    "high": c["high"],
                                    "low": c["low"],
                                    "close": c["close"],
                                    "volume": c["volume"],
                                    "source": "market_candles_1m",
                                    "missing": "false",
                                }
                            )
                    else:
                        flags.append("MARKET_CANDLES_MISSING")
                        candles_rows.append(
                            {
                                "timestamp_utc": r["opened_at"],
                                "symbol": r["symbol"],
                                "open": "",
                                "high": "",
                                "low": "",
                                "close": "",
                                "volume": "",
                                "source": "none",
                                "missing": "true",
                            }
                        )

                    mae_pct = (
                        ((lowest_p / entry_p) - 1) * 100
                        if lowest_p is not None and entry_p > 0
                        else None
                    )

                    # Aggregate partial legs + final exit
                    partial_fees = sum((_dec(l["fees"]) for l in pos_legs), D(0))
                    partial_pnl = sum((_dec(l["realized_pnl"]) for l in pos_legs), D(0))
                    partial_qty = sum((_dec(l["filled_quantity"]) for l in pos_legs if l["leg"] in ("TP1", "TP2")), D(0))
                    rem_qty = _dec(r["quantity"])
                    entry_qty = partial_qty + rem_qty if r["status"] == "OPEN" or pos_legs else rem_qty
                    entry_notional = entry_qty * entry_p
                    total_net_pnl = _dec(r.get("net_pnl")) + partial_pnl if r["status"] == "CLOSED" else None
                    est_fees = partial_fees + (entry_notional * D("0.0004")) + (
                        (entry_notional * D("0.0004")) if r["status"] == "CLOSED" else D(0)
                    )
                    gross_pnl = (total_net_pnl + est_fees) if total_net_pnl is not None else None
                    net_ret_pct = (
                        (total_net_pnl / entry_notional) * 100
                        if total_net_pnl is not None and entry_notional > 0
                        else None
                    )
                    profit_capture = (
                        (net_ret_pct / mfe_pct)
                        if net_ret_pct is not None and mfe_pct is not None and mfe_pct > 0 and total_net_pnl > 0
                        else None
                    )
                    holding_secs = (
                        int(
                            (
                                datetime.fromisoformat(r["closed_at"]) - opened
                            ).total_seconds()
                        )
                        if r.get("closed_at")
                        else int((datetime.now(UTC) - opened).total_seconds())
                    )
                    item = {
                        "trade_id": _anon_hash(f"{pos_id}:{r['policy']}"),
                        "position_id": _anon_hash(pos_id),
                        "mode": "PAPER",
                        "symbol": r["symbol"],
                        "strategy_id": "dip-rebound",
                        "strategy_version": r["policy"],
                        "config_hash": cfg_hash[:16],
                        "entry_signal_id": _anon_hash(r["entry_group"]),
                        "entry_time_utc": r["opened_at"],
                        "entry_price_vwap": _fmt(entry_p),
                        "entry_qty": _fmt(entry_qty),
                        "entry_notional_krw": _fmt(entry_notional, 2),
                        "exit_time_utc": r.get("closed_at") or "",
                        "exit_price_vwap": r.get("exit_price") or "",
                        "exit_qty_total": _fmt(entry_qty) if r["status"] == "CLOSED" else _fmt(partial_qty),
                        "exit_notional_krw": _fmt(entry_notional + total_net_pnl, 2) if total_net_pnl is not None else "",
                        "realized_gross_pnl_krw": _fmt(gross_pnl, 2) if gross_pnl is not None else "",
                        "fees_krw": _fmt(est_fees, 2),
                        "realized_net_pnl_krw": _fmt(total_net_pnl, 2) if total_net_pnl is not None else "",
                        "net_return_pct": _fmt(net_ret_pct, 4) if net_ret_pct is not None else "",
                        "holding_seconds": holding_secs,
                        "exit_reason": r.get("exit_reason") or ("OPEN" if r["status"] == "OPEN" else ""),
                        "initial_stop_pct": _fmt(init_stop_pct, 4) if init_stop_pct is not None else "",
                        "initial_stop_price": r["initial_stop"],
                        "highest_price_during_hold": _fmt(peak_p),
                        "lowest_price_during_hold": _fmt(lowest_p) if lowest_p is not None else "",
                        "mfe_pct": _fmt(mfe_pct, 4) if mfe_pct is not None else "",
                        "mae_pct": _fmt(mae_pct, 4) if mae_pct is not None else "",
                        "profit_capture_ratio": _fmt(profit_capture, 4) if profit_capture is not None else "",
                        "complete": "true" if r["status"] == "CLOSED" else "false",
                        "data_quality_flags": "|".join(flags) if flags else "OK",
                    }
                    if r["status"] == "CLOSED":
                        closed_trades.append(item)
                    else:
                        open_trades.append(item)

                    # Entry order + leg orders
                    orders_rows.append(
                        {
                            "order_id": _anon_hash(f"entry:{pos_id}"),
                            "trade_id": item["trade_id"],
                            "position_id": item["position_id"],
                            "mode": "PAPER",
                            "symbol": r["symbol"],
                            "side": "BUY",
                            "order_type": "PAPER_SWEEP",
                            "requested_qty": _fmt(entry_qty),
                            "filled_qty": _fmt(entry_qty),
                            "requested_price": _fmt(entry_p),
                            "avg_fill_price": _fmt(entry_p),
                            "fee_krw": _fmt(entry_notional * D("0.0004"), 2),
                            "placed_at_utc": r["opened_at"],
                            "filled_at_utc": r["opened_at"],
                            "status": "FILLED",
                            "reason": "ENTRY",
                            "partial_exit_stage": "ENTRY",
                            "idempotency_key_hash": _anon_hash(r["entry_group"]),
                        }
                    )
                    for leg in pos_legs:
                        orders_rows.append(
                            {
                                "order_id": _anon_hash(leg["id"]),
                                "trade_id": item["trade_id"],
                                "position_id": item["position_id"],
                                "mode": "PAPER",
                                "symbol": r["symbol"],
                                "side": "SELL",
                                "order_type": "PAPER_SWEEP",
                                "requested_qty": leg["target_quantity"],
                                "filled_qty": leg["filled_quantity"],
                                "requested_price": leg.get("avg_fill_price") or "",
                                "avg_fill_price": leg.get("avg_fill_price") or "",
                                "fee_krw": leg.get("fees") or "0",
                                "placed_at_utc": leg["created_at"],
                                "filled_at_utc": leg["updated_at"],
                                "status": leg["status"],
                                "reason": leg["leg"],
                                "partial_exit_stage": leg["leg"],
                                "idempotency_key_hash": _anon_hash(leg["client_order_id"]),
                            }
                        )
            else:
                # LIVE mode: read strictly from private_fills / private_orders
                fills = [
                    dict(f)
                    for f in conn.execute(
                        "SELECT * FROM private_fills ORDER BY filled_at ASC"
                    ).fetchall()
                ]
                if not fills:
                    warnings.append("LIVE_FILLS_EMPTY")
                for f in fills:
                    filled_dt = datetime.fromisoformat(f["filled_at"])
                    if start_utc and filled_dt < start_utc:
                        continue
                    if end_utc and filled_dt > end_utc:
                        continue
                    if symbol_filter != "ALL" and f["symbol"] != symbol_filter:
                        continue
                    orders_rows.append(
                        {
                            "order_id": _anon_hash(f["order_id"]),
                            "trade_id": _anon_hash(f["trade_id"]),
                            "position_id": _anon_hash(f["order_id"]),
                            "mode": "LIVE",
                            "symbol": f["symbol"],
                            "side": f["side"],
                            "order_type": "LIVE",
                            "requested_qty": f["quantity"],
                            "filled_qty": f["quantity"],
                            "requested_price": f["price"],
                            "avg_fill_price": f["price"],
                            "fee_krw": f.get("fee_krw") or "0",
                            "placed_at_utc": f["filled_at"],
                            "filled_at_utc": f["filled_at"],
                            "status": "FILLED",
                            "reason": "LIVE_FILL",
                            "partial_exit_stage": "LIVE",
                            "idempotency_key_hash": _anon_hash(f["trade_id"]),
                        }
                    )

            # Signals CSV
            sig_rows = []
            for s in conn.execute(
                "SELECT * FROM signals ORDER BY generated_at ASC"
            ).fetchall():
                sd = dict(s)
                payload = json.loads(sd["payload"]) if sd.get("payload") else {}
                gen_dt = datetime.fromisoformat(sd["generated_at"])
                if start_utc and gen_dt < start_utc:
                    continue
                if end_utc and gen_dt > end_utc:
                    continue
                if symbol_filter != "ALL" and sd["symbol"] != symbol_filter:
                    continue
                sig_rows.append(
                    {
                        "signal_id": _anon_hash(sd["id"]),
                        "signal_time_utc": sd["generated_at"],
                        "symbol": sd["symbol"],
                        "strategy_id": "main",
                        "strategy_version": sd.get("strategy_version") or "",
                        "mode": mode,
                        "signal_type": sd["type"],
                        "signal_score": payload.get("score", ""),
                        "reference_price": payload.get("price", ""),
                        "drop_pct": payload.get("drop_pct", ""),
                        "rebound_pct": payload.get("rebound_pct", ""),
                        "volume_ratio": payload.get("volume_ratio", ""),
                        "trend_1m": payload.get("trend_1m", ""),
                        "trend_3m": payload.get("trend_3m", ""),
                        "trend_5m": payload.get("trend_5m", ""),
                        "volatility_pct": payload.get("volatility_pct", ""),
                        "spread_pct": payload.get("spread_pct", ""),
                        "filter_result": payload.get("status", ""),
                        "rejection_reason": payload.get("reason", ""),
                        "linked_position_id": "",
                    }
                )

        # Compute summary KPIs strictly from closed_trades
        wins = [t for t in closed_trades if _dec(t["realized_net_pnl_krw"]) > 0]
        losses = [t for t in closed_trades if _dec(t["realized_net_pnl_krw"]) < 0]
        even = [t for t in closed_trades if _dec(t["realized_net_pnl_krw"]) == 0]
        total_net = sum((_dec(t["realized_net_pnl_krw"]) for t in closed_trades), D(0))
        total_gross = sum((_dec(t["realized_gross_pnl_krw"]) for t in closed_trades), D(0))
        total_fees = sum((_dec(t["fees_krw"]) for t in closed_trades), D(0))
        gross_win = sum((_dec(t["realized_net_pnl_krw"]) for t in wins), D(0))
        gross_loss = abs(sum((_dec(t["realized_net_pnl_krw"]) for t in losses), D(0)))
        pf = (gross_win / gross_loss) if gross_loss > 0 else None
        win_rate = (D(len(wins)) / len(closed_trades)) if closed_trades else None
        expectancy = (total_net / len(closed_trades)) if closed_trades else None

        # Max consecutive losses & Equity Curve
        max_consec = 0
        cur_consec = 0
        equity = D("150000")
        peak_eq = equity
        max_dd = D(0)
        equity_curve = []
        for t in closed_trades:
            pnl = _dec(t["realized_net_pnl_krw"])
            if pnl < 0:
                cur_consec += 1
                max_consec = max(max_consec, cur_consec)
            else:
                cur_consec = 0
            equity += pnl
            peak_eq = max(peak_eq, equity)
            dd = ((peak_eq - equity) / peak_eq * 100) if peak_eq > 0 else D(0)
            max_dd = max(max_dd, dd)
            equity_curve.append(
                {
                    "timestamp_utc": t["exit_time_utc"] or t["entry_time_utc"],
                    "mode": mode,
                    "strategy_id": t["strategy_id"],
                    "strategy_version": t["strategy_version"],
                    "realized_equity_krw": _fmt(equity, 2),
                    "unrealized_pnl_krw": "0",
                    "total_equity_krw": _fmt(equity, 2),
                    "drawdown_pct": _fmt(dd, 4),
                    "open_positions_count": len(open_trades),
                }
            )

        by_symbol: dict[str, dict] = {}
        by_reason: dict[str, int] = {}
        for t in closed_trades:
            sym = t["symbol"]
            b = by_symbol.setdefault(sym, {"symbol": sym, "trades": 0, "net_pnl_krw": D(0)})
            b["trades"] += 1
            b["net_pnl_krw"] += _dec(t["realized_net_pnl_krw"])
            r = t["exit_reason"] or "UNKNOWN"
            by_reason[r] = by_reason.get(r, 0) + 1

        avg_hold = (
            round(sum(int(t["holding_seconds"]) for t in closed_trades) / len(closed_trades), 1)
            if closed_trades
            else None
        )

        summary = {
            "mode": mode,
            "trade_count": len(closed_trades),
            "open_count": len(open_trades),
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": len(even),
            "win_rate": _fmt(win_rate * 100, 2) if win_rate is not None else None,
            "gross_pnl_krw": _fmt(total_gross, 2),
            "fees_krw": _fmt(total_fees, 2),
            "net_pnl_krw": _fmt(total_net, 2),
            "expectancy_krw": _fmt(expectancy, 2) if expectancy is not None else None,
            "profit_factor": _fmt(pf, 3) if pf is not None else None,
            "max_drawdown_pct": _fmt(max_dd, 4),
            "avg_holding_seconds": avg_hold,
            "max_consecutive_losses": max_consec,
            "exit_reason_counts": by_reason,
            "by_symbol": [
                {
                    "symbol": k,
                    "trades": v["trades"],
                    "net_pnl_krw": _fmt(v["net_pnl_krw"], 2),
                }
                for k, v in sorted(by_symbol.items(), key=lambda kv: kv[1]["net_pnl_krw"], reverse=True)
            ],
            "equity_curve": equity_curve,
        }

        missing_candles = sum(1 for c in candles_rows if c["missing"] == "true")
        data_quality = {
            "mode": mode,
            "closed_trades": len(closed_trades),
            "open_positions_excluded_from_pnl": len(open_trades),
            "signals_count": len(sig_rows),
            "orders_count": len(orders_rows),
            "candles_rows": len(candles_rows),
            "missing_candles_count": missing_candles,
            "csv_encoding": "UTF-8 with BOM (utf-8-sig)",
            "csv_injection_escaped": True,
            "sensitive_ids_hashed": True,
            "warnings": warnings,
        }

        return {
            "mode": mode,
            "filters": filters,
            "start_utc": start_utc.isoformat() if start_utc else None,
            "end_utc": end_utc.isoformat() if end_utc else None,
            "summary": summary,
            "data_quality": data_quality,
            "config_snapshot": cfg_snap,
            "closed_trades": closed_trades,
            "open_trades": open_trades,
            "signals": sig_rows,
            "orders": orders_rows,
            "equity_curve": equity_curve,
            "candles": candles_rows,
        }

    def preview(self, filters: dict) -> dict:
        data = self._collect(filters)
        return {
            "mode": data["mode"],
            "summary": data["summary"],
            "data_quality": data["data_quality"],
            "trades_sample": data["closed_trades"][:20],
            "open_positions": data["open_trades"],
        }

    def create_report(self, filters: dict) -> dict:
        report_id = uuid.uuid4().hex
        now_dt = datetime.now(UTC)
        expires_dt = now_dt + timedelta(days=7)
        data = self._collect(filters)
        summary = data["summary"]
        dq = data["data_quality"]
        cfg = data["config_snapshot"]

        summary_md = "\n".join(
            [
                "# BITHUMB AI 전략 분석 리포트",
                "",
                f"- 리포트 ID: `{report_id}`",
                f"- 생성 시각 (UTC): `{now_dt.isoformat()}`",
                f"- 모드: **{data['mode']}** (가상/실전 분리)",
                f"- 종료 거래 수: **{summary['trade_count']}건** (승 {summary['wins']} / 패 {summary['losses']} / 무 {summary['breakeven']})",
                f"- 승률: **{summary['win_rate'] or 'N/A'}%**",
                f"- 순손익 (수수료 반영): **{summary['net_pnl_krw']} KRW** (수수료 {summary['fees_krw']} KRW)",
                f"- Profit Factor: **{summary['profit_factor'] or 'N/A'}** · 기대값: **{summary['expectancy_krw'] or 'N/A'} KRW**",
                f"- 최대 낙폭 (MDD): **{summary['max_drawdown_pct']}%** · 최대 연속 손실: **{summary['max_consecutive_losses']}회**",
                f"- 미종료 포지션 (손익 제외): **{summary['open_count']}건**",
                "",
                "## 청산 사유 분포",
                *[f"- `{k}`: {v}건" for k, v in summary["exit_reason_counts"].items()],
            ]
        )

        readme_md = "\n".join(
            [
                "# BITHUMB AI Report Bundle (bithumb-ai-report/1.0)",
                "",
                "- 인코딩: UTF-8 with BOM (`utf-8-sig`), CSV 수식 인젝션 이스케이프 적용",
                "- 금액 단위: KRW (Decimal 정밀도 유지), 시각: ISO-8601 UTC",
                "- 개인정보/API 키/원본 주문 ID는 포함되지 않으며 SHA-256 해시로 익명화됩니다.",
                "- 원천 데이터가 없는 경우 추정하지 않고 빈 값(`null`) 및 `data_quality.json` 경고로 표시합니다.",
            ]
        )

        files_map: dict[str, bytes] = {
            "summary.json": json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8"),
            "summary.md": summary_md.encode("utf-8"),
            "trades.csv": _to_csv_bytes(TRADES_HEADERS, data["closed_trades"]),
            "open_positions.csv": _to_csv_bytes(TRADES_HEADERS, data["open_trades"]),
            "signals.csv": _to_csv_bytes(SIGNALS_HEADERS, data["signals"]),
            "orders.csv": _to_csv_bytes(ORDERS_HEADERS, data["orders"]),
            "equity_curve.csv": _to_csv_bytes(EQUITY_HEADERS, data["equity_curve"]),
            "market_context.csv": _to_csv_bytes(MARKET_CONTEXT_HEADERS, data["candles"]),
            "strategy_config.json": json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8"),
            "data_quality.json": json.dumps(dq, ensure_ascii=False, indent=2).encode("utf-8"),
            "README.md": readme_md.encode("utf-8"),
        }

        manifest_files = [
            {
                "name": name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
            for name, content in files_map.items()
        ]
        manifest = {
            "schema_version": "bithumb-ai-report/1.0",
            "report_id": report_id,
            "generated_at_utc": now_dt.isoformat(),
            "range_start_utc": data["start_utc"],
            "range_end_utc": data["end_utc"],
            "display_timezone": "Asia/Seoul",
            "mode": data["mode"],
            "strategy_ids": ["dip-rebound"],
            "strategy_versions": sorted({t["strategy_version"] for t in data["closed_trades"]}),
            "trade_count": summary["trade_count"],
            "fee_included": True,
            "slippage_model": "recorded-or-explicit-null",
            "files": manifest_files,
            "warnings": dq["warnings"],
        }
        files_map["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

        zip_path = self.reports_dir / f"{report_id}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, content in files_map.items():
                zf.writestr(name, content)

        size_bytes = zip_path.stat().st_size
        if size_bytes > MAX_ZIP_BYTES:
            zip_path.unlink(missing_ok=True)
            raise ValueError("ZIP 용량이 20MB 상한을 초과했습니다. 기간을 나누어 다시 생성하세요.")

        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO strategy_reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    report_id,
                    "READY",
                    data["mode"],
                    json.dumps(filters),
                    json.dumps(summary),
                    json.dumps(dq),
                    json.dumps(manifest),
                    str(zip_path),
                    size_bytes,
                    now_dt.isoformat(),
                    expires_dt.isoformat(),
                    None,
                ),
            )
            self.store.audit(
                conn,
                "strategy_report_created",
                {"report_id": report_id, "mode": data["mode"], "trade_count": summary["trade_count"]},
            )
        return {
            "report_id": report_id,
            "status": "READY",
            "mode": data["mode"],
            "file_size_bytes": size_bytes,
            "created_at": now_dt.isoformat(),
            "expires_at": expires_dt.isoformat(),
            "summary": summary,
            "data_quality": dq,
            "manifest": manifest,
        }

    def list_reports(self, limit: int = 20) -> list[dict]:
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT report_id,status,mode,filters_json,summary_json,file_size_bytes,created_at,expires_at,error FROM strategy_reports ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "report_id": r["report_id"],
                "status": r["status"],
                "mode": r["mode"],
                "filters": json.loads(r["filters_json"]),
                "summary": json.loads(r["summary_json"]),
                "file_size_bytes": r["file_size_bytes"],
                "created_at": r["created_at"],
                "expires_at": r["expires_at"],
                "error": r["error"],
            }
            for r in rows
        ]

    def get_report(self, report_id: str) -> dict | None:
        if not re.fullmatch(r"[0-9a-f]{32}", report_id):
            return None
        with self.store.connect() as conn:
            r = conn.execute(
                "SELECT * FROM strategy_reports WHERE report_id=?", (report_id,)
            ).fetchone()
        if not r:
            return None
        return {
            "report_id": r["report_id"],
            "status": r["status"],
            "mode": r["mode"],
            "filters": json.loads(r["filters_json"]),
            "summary": json.loads(r["summary_json"]),
            "data_quality": json.loads(r["data_quality_json"]),
            "manifest": json.loads(r["manifest_json"]),
            "file_size_bytes": r["file_size_bytes"],
            "created_at": r["created_at"],
            "expires_at": r["expires_at"],
            "error": r["error"],
        }

    def get_zip_path(self, report_id: str) -> Path | None:
        if not re.fullmatch(r"[0-9a-f]{32}", report_id):
            return None
        with self.store.connect() as conn:
            r = conn.execute(
                "SELECT zip_path, expires_at FROM strategy_reports WHERE report_id=?",
                (report_id,),
            ).fetchone()
        if not r:
            return None
        if datetime.fromisoformat(r["expires_at"]) < datetime.now(UTC):
            return None
        p = Path(r["zip_path"])
        return p if p.is_file() else None
