"""Actual-fill journal and receive-time-safe replay; never executes an order."""

import csv
import hashlib
import io
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from uuid import uuid4
from zoneinfo import ZoneInfo

from .ledger import decimal_math, decimal_text
from .review_events import utcnow

KST = ZoneInfo("Asia/Seoul")


def moment(value):
    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("시간대가 포함된 UTC/KST 시각이 필요합니다.")
    return value.astimezone(UTC)


def outcome(value):
    return (
        None
        if value is None
        else "PROFIT"
        if value > 0
        else "LOSS"
        if value < 0
        else "BREAKEVEN"
    )


class TradeReview:
    def __init__(self, store, market, account=None):
        self.store, self.market, self.account = store, market, account

    def evidence(self, symbol, cursor=None):
        at = moment(cursor or utcnow()).isoformat()
        with self.store.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM decision_events WHERE symbol=?
                   AND julianday(event_at)<=julianday(?) AND julianday(recorded_at)<=julianday(?)
                   ORDER BY julianday(event_at),id""",
                (symbol, at, at),
            ).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    @decimal_math
    def positions(self):
        with self.store.connect() as conn:
            fills = [dict(r) for r in conn.execute("SELECT * FROM manual_fills")]
            links = {
                r["fill_id"]: dict(r) for r in conn.execute("SELECT * FROM fill_links")
            }
            requests = {
                r["order_id"]: dict(r)
                for r in conn.execute(
                    "SELECT * FROM manual_orders WHERE order_id IS NOT NULL"
                )
            }
            claims = {
                r[0]
                for r in conn.execute("SELECT order_intent_id FROM auto_signal_claims")
                if r[0]
            }
            signals = {
                r["id"]: json.loads(r["payload"])
                for r in conn.execute("SELECT * FROM signals")
            }
            decisions = {
                r["entity_id"]: json.loads(r["payload"])
                for r in conn.execute(
                    "SELECT * FROM decision_events WHERE entity_type='SIGNAL'"
                )
            }
            follow_links = {
                r["entity_id"]: json.loads(r["payload"])
                for r in conn.execute(
                    "SELECT entity_id,payload FROM decision_events WHERE entity_type='PAPER_FOLLOW_LINK'"
                )
            }
            notes = {
                r["position_id"]: dict(r)
                for r in conn.execute("SELECT * FROM trading_journals")
            }
            private = (
                [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM private_fills WHERE account_id=?",
                        (self.account,),
                    )
                ]
                if self.account
                else []
            )
        imported = {r["id"] for r in fills}
        private_map = {"exchange-" + r["trade_id"]: r for r in private}
        for fill in fills:
            fill["scope"] = "STRATEGY"
            fill["actual_exchange_fill"] = fill["id"] in private_map
        for row in private:
            if "exchange-" + row["trade_id"] not in imported:
                fills.append(
                    {
                        **row,
                        "id": "external-" + row["trade_id"],
                        "recorded_at": row["received_at"],
                        "scope": "EXTERNAL",
                        "actual_exchange_fill": True,
                    }
                )
                private_map["external-" + row["trade_id"]] = row
        fills.sort(
            key=lambda f: (moment(f["filled_at"]), moment(f["recorded_at"]), f["id"])
        )
        open_positions, output = {}, []
        for fill in fills:
            symbol, scope = fill["symbol"], fill["scope"]
            linked = links.get(fill["id"], {})
            private_fill = private_map.get(fill["id"], {})
            order = requests.get(private_fill.get("order_id"), {})
            sid = order.get("signal_id") or linked.get("signal_id")
            signal = signals.get(sid, {})
            reason = decisions.get(sid, {}).get("exit_reason")
            mode = (
                "AUTO_APPROVAL"
                if order.get("id") in claims
                else "MANUAL_APPROVAL"
                if order
                else "EXTERNAL"
                if scope == "EXTERNAL"
                else "MANUAL_RECORDED"
            )
            followed = follow_links.get(fill["id"], {})
            version = (
                signal.get("strategy_version")
                or followed.get("version_id")
                or "UNATTRIBUTED"
            )
            key = (scope, symbol)
            p = open_positions.get(key)
            if p is None:
                pid = hashlib.sha256(f"{scope}:{fill['id']}".encode()).hexdigest()[:24]
                p = dict(
                    id=pid,
                    symbol=symbol,
                    scope=scope,
                    opened_at=fill["filled_at"],
                    closed_at=None,
                    quantity=D(0),
                    cost=D(0),
                    raw=D(0),
                    realized=D(0),
                    fees=D(0),
                    fills=[],
                    allocations=[],
                    versions=set(),
                    modes=set(),
                    fee_complete=True,
                    cost_complete=True,
                    lots=[],
                )
                open_positions[key] = p
                output.append(p)
            decision_version = version
            if fill["side"] == "SELL":
                entry_versions = {
                    lot["version"] for lot in p["lots"] if lot["remaining"] > 0
                }
                if entry_versions:
                    version = (
                        next(iter(entry_versions))
                        if len(entry_versions) == 1
                        else "MIXED_ENTRY_VERSIONS"
                    )
            qty, price = D(fill["quantity"]), D(fill["price"])
            fee = D(fill["fee_krw"]) if fill["fee_krw"] is not None else None
            p["fee_complete"] = p["fee_complete"] and fee is not None
            p["fees"] += fee or D(0)
            net = None
            allocated = D(0)
            if fill["side"] == "BUY":
                p["quantity"] += qty
                p["cost"] += qty * price + (fee or D(0))
                p["raw"] += qty * price
                p["lots"].append(
                    {
                        "fill_id": fill["id"],
                        "version": version,
                        "remaining": qty,
                        "cost": qty * price + (fee or D(0)),
                    }
                )
            elif qty <= p["quantity"] and p["quantity"] > 0:
                ratio = qty / p["quantity"]
                allocated = p["cost"] * ratio
                raw = p["raw"] * ratio
                for lot in p["lots"]:
                    aq, ac = lot["remaining"] * ratio, lot["cost"] * ratio
                    if aq:
                        p["allocations"].append(
                            {
                                "buy_fill_id": lot["fill_id"],
                                "sell_fill_id": fill["id"],
                                "quantity": decimal_text(aq),
                                "cost_including_buy_fee_krw": decimal_text(ac),
                            }
                        )
                    lot["remaining"] -= aq
                    lot["cost"] -= ac
                net = (
                    qty * price - (fee or D(0)) - allocated
                    if p["fee_complete"] and p["cost_complete"]
                    else None
                )
                if net is not None:
                    p["realized"] += net
                p["quantity"] -= qty
                p["cost"] -= allocated
                p["raw"] -= raw
            else:
                # Pre-existing exchange inventory is not assigned a fabricated zero cost.
                p["cost_complete"] = False
            execution = {
                **fill,
                "signal_id": sid,
                "paper_trade_id": followed.get("paper_trade_id"),
                "order_id": order.get("id"),
                "exchange_order_id": private_fill.get("order_id"),
                "mode": mode,
                "strategy_version": version,
                "decision_strategy_version": decision_version,
                "exit_reason": (reason or ("MANUAL_EXIT" if not sid else "SIGNAL_EXIT"))
                if fill["side"] == "SELL"
                else None,
                "realized_net_krw": decimal_text(net) if net is not None else None,
                "result": outcome(net),
                "allocated_cost_krw": decimal_text(allocated),
                "partial_fill": bool(
                    order
                    and (
                        order["status"] != "DONE"
                        or sum(
                            D(x["quantity"])
                            for x in private
                            if x["order_id"] == private_fill.get("order_id")
                        )
                        > qty
                    )
                ),
                "requested_reference_price": json.loads(order.get("details", "{}")).get(
                    "reference_price"
                ),
            }
            ref = execution["requested_reference_price"]
            execution["realized_slippage_krw"] = (
                decimal_text(
                    (price - D(ref)) * qty * (1 if fill["side"] == "BUY" else -1)
                )
                if ref
                else None
            )
            p["fills"].append(execution)
            p["versions"].add(version)
            p["modes"].add(mode)
            if fill["side"] == "SELL" and p["quantity"] == 0:
                p["closed_at"] = fill["filled_at"]
                del open_positions[key]
        policy = self.store.settings()
        result = []
        for p in output:
            quote = self.market.quote(p["symbol"])
            mark = D(quote["bids"][0][0]) if quote else None
            avg = p["raw"] / p["quantity"] if p["quantity"] else None
            net = p["realized"] if p["fee_complete"] and p["cost_complete"] else None
            note = notes.get(p["id"], {})
            result.append(
                {
                    k: v
                    for k, v in p.items()
                    if k
                    not in (
                        "lots",
                        "raw",
                        "cost",
                        "quantity",
                        "realized",
                        "fees",
                        "versions",
                        "modes",
                    )
                }
                | {
                    "quantity": decimal_text(p["quantity"]),
                    "cost_basis_krw": decimal_text(p["cost"]),
                    "average_entry_price": decimal_text(avg)
                    if avg is not None
                    else None,
                    "realized_net_krw": decimal_text(net) if net is not None else None,
                    "fees_krw": decimal_text(p["fees"]) if p["fee_complete"] else None,
                    "result": outcome(net) if p["closed_at"] else None,
                    "versions": sorted(p["versions"]),
                    "modes": sorted(p["modes"]),
                    "status": "CLOSED" if p["closed_at"] else "OPEN",
                    "mark_price": decimal_text(mark) if mark is not None else None,
                    "net_estimate_krw": decimal_text(
                        mark * p["quantity"] * (1 - D(policy["execution_fee_rate"]))
                        - p["cost"]
                    )
                    if mark is not None and p["fee_complete"] and p["cost_complete"]
                    else None,
                    "stop_reference": decimal_text(
                        avg * (1 - D(policy["stop_loss_rate"]))
                    )
                    if avg
                    else None,
                    "target_reference": decimal_text(
                        avg * (1 + D(policy["take_profit_rate"]))
                    )
                    if avg
                    else None,
                    "holding_seconds": max(
                        0,
                        int(
                            (
                                moment(p["closed_at"] or utcnow())
                                - moment(p["opened_at"])
                            ).total_seconds()
                        ),
                    ),
                    "data_fresh": quote is not None,
                    "note": note.get("note", ""),
                    "tags": json.loads(note.get("tags", "[]")),
                }
            )
        return result

    def position(self, pid):
        p = next((p for p in self.positions() if p["id"] == pid), None)
        if p is None:
            raise ValueError("포지션을 찾을 수 없습니다.")
        return p

    @decimal_math
    def journal(
        self,
        day,
        symbol=None,
        strategy=None,
        mode=None,
        scope="STRATEGY",
        page=1,
        page_size=10,
    ):
        date.fromisoformat(day)
        rows = []
        for p in self.positions():
            if (
                scope != "ALL"
                and p["scope"] != scope
                or symbol
                and p["symbol"] != symbol
                or strategy
                and strategy not in p["versions"]
                or mode
                and mode not in p["modes"]
            ):
                continue
            executions = [
                f
                for f in p["fills"]
                if moment(f["filled_at"]).astimezone(KST).date().isoformat() == day
                and (not strategy or f["strategy_version"] == strategy)
                and (not mode or f["mode"] == mode)
            ]
            if executions:
                rows.append({**p, "day_fills": executions})
        rows.sort(key=lambda p: moment(p["day_fills"][-1]["filled_at"]), reverse=True)
        sells = [f for p in rows for f in p["day_fills"] if f["side"] == "SELL"]
        known = [
            D(f["realized_net_krw"]) for f in sells if f["realized_net_krw"] is not None
        ]
        closed = [
            p
            for p in rows
            if p["closed_at"]
            and moment(p["closed_at"]).astimezone(KST).date().isoformat() == day
            and p["realized_net_krw"] is not None
        ]
        with self.store.connect() as conn:
            orders = [
                dict(r)
                for r in conn.execute(
                    """SELECT o.id,o.market,o.side,o.status,o.quantity,o.created_at,o.updated_at,o.signal_id,
                p.state AS exchange_state,p.executed_quantity,
                CASE WHEN a.order_intent_id IS NOT NULL THEN 'AUTO_APPROVAL' ELSE 'MANUAL_APPROVAL' END AS mode,
                s.strategy_version FROM manual_orders o LEFT JOIN private_orders p ON p.account_id=o.account_id AND p.order_id=o.order_id
                LEFT JOIN auto_signal_claims a ON a.order_intent_id=o.id LEFT JOIN signals s ON s.id=o.signal_id
                WHERE o.account_id=? AND date(datetime(o.created_at,'+9 hours'))=? AND o.status NOT IN ('PREVIEW','EXPIRED')
                ORDER BY o.created_at DESC,o.id DESC""",
                    (self.account, day),
                )
            ]
        orders = [
            o
            for o in orders
            if (not symbol or o["market"] == symbol)
            and (not mode or o["mode"] == mode)
            and (not strategy or o["strategy_version"] == strategy)
            and scope != "EXTERNAL"
        ]
        pages = max(1, (max(len(rows), len(orders)) + page_size - 1) // page_size)
        page = min(page, pages)
        return dict(
            items=rows[(page - 1) * page_size : page * page_size],
            total=len(rows),
            order_requests=orders[(page - 1) * page_size : page * page_size],
            order_request_count=len(orders),
            page=page,
            pages=pages,
            page_size=page_size,
            date=day,
            timezone="Asia/Seoul",
            summary={
                "realized_net_krw": decimal_text(sum(known, D(0))),
                "pnl_complete": len(known) == len(sells),
                "fill_count": sum(len(p["day_fills"]) for p in rows),
                "closed_positions": len(closed),
                "win_rate": decimal_text(
                    D(sum(D(p["realized_net_krw"]) > 0 for p in closed)) / len(closed)
                )
                if closed
                else None,
                "open_positions": sum(p["status"] == "OPEN" for p in rows),
                "fees_krw": decimal_text(
                    sum(
                        (
                            D(f["fee_krw"])
                            for p in rows
                            for f in p["day_fills"]
                            if f["fee_krw"] is not None
                        ),
                        D(0),
                    )
                ),
            },
        )

    def save_note(self, pid, note, tags):
        self.position(pid)
        if (
            not isinstance(note, str)
            or len(note) > 10000
            or not isinstance(tags, list)
            or len(tags) > 20
            or any(not isinstance(t, str) or len(t) > 40 for t in tags)
        ):
            raise ValueError("메모는 10,000자, 태그는 각 40자·20개 이하입니다.")
        with self.store.connect() as conn:
            previous = conn.execute(
                "SELECT * FROM trading_journals WHERE position_id=?", (pid,)
            ).fetchone()
            conn.execute(
                "INSERT INTO trading_journals VALUES(?,?,?,?) ON CONFLICT(position_id) DO UPDATE SET note=excluded.note,tags=excluded.tags,updated_at=excluded.updated_at",
                (pid, note, json.dumps(tags, ensure_ascii=False), utcnow()),
            )
            self.store.audit(
                conn,
                "JOURNAL_EDIT",
                {
                    "position_id": pid,
                    "before": dict(previous) if previous else None,
                    "after": {"note": note, "tags": tags},
                },
            )
        return {"saved": True}

    @decimal_math
    def chart(self, symbol, start, end, cursor, timeframe=60):
        begin, finish, cutoff = moment(start), moment(end), moment(cursor)
        cutoff = min(cutoff, finish, datetime.now(UTC))
        if (
            finish <= begin
            or (finish - begin).total_seconds() > 86400 * 3
            or cutoff < begin
        ):
            raise ValueError(
                "차트 조회 구간은 최대 3일이며 시작 이후 시각이 필요합니다."
            )
        if timeframe not in (60, 180, 300):
            raise ValueError("1/3/5분봉만 지원합니다.")
        lo, hi, at = (
            int(begin.timestamp()) // 60 * 60,
            int(cutoff.timestamp()),
            cutoff.isoformat(),
        )
        with self.store.connect() as conn:
            archived = conn.execute(
                """SELECT payload,known_at FROM replay_candles WHERE symbol=? AND start>=? AND start<=?
                AND julianday(known_at)<=julianday(?) ORDER BY julianday(known_at),id""",
                (symbol, lo, hi, at),
            ).fetchall()
            events = conn.execute(
                """SELECT payload FROM market_events WHERE symbol=? AND julianday(received_at)<=julianday(?)
                AND julianday(exchange_at)<=julianday(?) AND julianday(exchange_at)>=julianday(?) ORDER BY julianday(exchange_at),id""",
                (symbol, at, at, datetime.fromtimestamp(lo, UTC).isoformat()),
            ).fetchall()
        minutes = {}
        for row in archived:
            bar = json.loads(row["payload"])
            # Completed REST OHLC cannot reveal a not-yet-finished candle.
            if bar["closed"] and bar["end"] > hi:
                continue
            minutes[bar["start"]] = {**bar, "known_at": row["known_at"]}
        raw = {}
        for row in events:
            event = json.loads(row["payload"])
            if (
                event.get("stream_type") != "REALTIME"
                or "price" not in event
                or "quantity" not in event
            ):
                continue
            minute = int(moment(event["exchange_at"]).timestamp()) // 60 * 60
            price, qty = D(event["price"]), D(event["quantity"])
            if minute not in raw:
                raw[minute] = dict(
                    symbol=symbol,
                    start=minute,
                    end=minute + 60,
                    open=str(price),
                    high=str(price),
                    low=str(price),
                    close=str(price),
                    volume="0",
                    turnover="0",
                    complete=False,
                    closed=minute + 60 <= hi,
                    origin="WS_RETAINED_PARTIAL",
                    known_at=event["received_at"],
                )
            b = raw[minute]
            b.update(
                high=str(max(D(b["high"]), price)),
                low=str(min(D(b["low"]), price)),
                close=str(price),
                volume=str(D(b["volume"]) + qty),
                turnover=str(D(b["turnover"]) + qty * price),
                known_at=event["received_at"],
            )
        for minute, b in raw.items():
            if minute not in minutes:
                minutes[minute] = b
        grouped = {}
        for b in sorted(minutes.values(), key=lambda b: b["start"]):
            bucket = b["start"] // timeframe * timeframe
            grouped.setdefault(bucket, []).append(b)
        candles = []
        for bucket, parts in sorted(grouped.items()):
            candles.append(
                dict(
                    start=bucket,
                    end=bucket + timeframe,
                    open=parts[0]["open"],
                    high=str(max(D(b["high"]) for b in parts)),
                    low=str(min(D(b["low"]) for b in parts)),
                    close=parts[-1]["close"],
                    volume=str(sum((D(b["volume"]) for b in parts), D(0))),
                    complete=len(parts) == timeframe // 60
                    and all(b["complete"] and b["closed"] for b in parts)
                    and [b["start"] for b in parts]
                    == list(range(bucket, bucket + timeframe, 60)),
                    closed=bucket + timeframe <= hi,
                    sources=sorted(
                        {b.get("archive_source", b["origin"]) for b in parts}
                    ),
                    known_at=max(b["known_at"] for b in parts),
                )
            )
        evidence = self.evidence(symbol, at)
        markers = []
        fee_updates = {
            e["payload"]["fill_id"]: e["payload"].get("fee_krw")
            for e in evidence
            if e["entity_type"] == "FILL_FEE"
        }
        for e in evidence:
            if moment(e["event_at"]) < begin:
                continue
            p = e["payload"]
            if e["entity_type"] not in ("SIGNAL", "ORDER_REQUEST", "FILL"):
                continue
            marker = dict(
                id=e["id"],
                kind=e["entity_type"],
                at=e["event_at"],
                price=p.get("price")
                or p.get("signal", {}).get("reference_price")
                or p.get("details", {}).get("reference_price"),
                side=p.get("side") or p.get("signal", {}).get("type"),
                quantity=p.get("quantity"),
                fee_krw=p.get("fee_krw"),
                partial=False,
                source=p.get("source"),
            )
            marker["partial"] = p.get("partial", False)
            if e["entity_type"] == "FILL" and e["entity_id"] in fee_updates:
                marker["fee_krw"] = fee_updates[e["entity_id"]]
            markers.append(marker)
        reference_lines = None
        qty, raw = D(0), D(0)
        for event in evidence:
            if event["entity_type"] != "FILL":
                continue
            value = event["payload"]
            q, price = D(value["quantity"]), D(value["price"])
            if value["side"] == "BUY":
                qty += q
                raw += q * price
            elif qty >= q and qty > 0:
                raw *= 1 - q / qty
                qty -= q
            else:
                qty, raw = D(0), D(0)
        thresholds = next(
            (
                e["payload"].get("thresholds")
                for e in reversed(evidence)
                if e["entity_type"] == "SIGNAL" and e["payload"].get("thresholds")
            ),
            None,
        )
        if (
            qty > 0
            and thresholds
            and thresholds.get("stop_loss_rate")
            and thresholds.get("take_profit_rate")
        ):
            avg = raw / qty
            reference_lines = {
                "stop": decimal_text(avg * (1 - D(thresholds["stop_loss_rate"]))),
                "target": decimal_text(avg * (1 + D(thresholds["take_profit_rate"]))),
                "source": "AS_OF_CAPTURED_THRESHOLDS_AND_KNOWN_FILLS",
            }
        return dict(
            symbol=symbol,
            reference_lines=reference_lines,
            from_at=begin.isoformat(),
            to_at=finish.isoformat(),
            cursor=at,
            timeframe=timeframe,
            candles=candles,
            markers=markers,
            evidence=evidence,
            missing_minutes=max(0, (hi - lo) // 60 + 1 - len(minutes)),
            note="수신 시각 기준 재현. 사후 REST 복구는 수신 이후에만 공개하며, 과거 미수신/보존되지 않은 구간은 비워 둡니다.",
        )

    def replay(self, pid, cursor=None, timeframe=60, before=30, after=30):
        p = self.position(pid)
        signal_times = [
            moment(e["event_at"])
            for e in self.evidence(p["symbol"])
            if e["entity_type"] == "SIGNAL"
            and e["entity_id"] in {f["signal_id"] for f in p["fills"]}
        ]
        begin = min(signal_times + [moment(p["opened_at"])]) - timedelta(minutes=before)
        finish = min(
            moment(p["closed_at"] or utcnow()) + timedelta(minutes=after),
            datetime.now(UTC),
        )
        return self.chart(
            p["symbol"],
            begin.isoformat(),
            finish.isoformat(),
            cursor or begin.isoformat(),
            timeframe,
        )

    def reasoning(self, pid, cursor=None):
        p = self.position(pid)
        ids = {f["signal_id"] for f in p["fills"]} | {f["order_id"] for f in p["fills"]}
        evidence = [
            e for e in self.evidence(p["symbol"], cursor) if e["entity_id"] in ids
        ]
        return {
            "items": evidence,
            "missing_original_evidence": not any(
                e["entity_type"] == "SIGNAL" for e in evidence
            ),
            "note": "기존 과거 거래의 미기록 판단 근거는 사후 생성해 원래 근거로 표시하지 않습니다.",
        }

    def review(self, pid):
        p = self.position(pid)
        similar = [
            q
            for q in self.positions()
            if q["id"] != pid
            and q["status"] == "CLOSED"
            and set(q["versions"]) & set(p["versions"])
        ][:10]
        strengths, problems, hypotheses = [], [], []
        if p["fee_complete"] and p["cost_complete"]:
            strengths.append("실제 체결 수량과 수수료를 기준으로 원가를 배분했습니다.")
        else:
            problems.append(
                "수수료 또는 이전 매수 원가가 누락되어 순손익 평가가 불완전합니다."
            )
        if p["realized_net_krw"] is not None and D(p["realized_net_krw"]) < 0:
            problems.append(
                "현재까지 실제 실현 순손익이 음수입니다. 청산 사유와 손익 결과를 별도로 확인하세요."
            )
        if not self.reasoning(pid)["items"]:
            problems.append(
                "당시 판단 근거가 보존되지 않았습니다. 임의로 재구성하지 않습니다."
            )
        slips = [
            D(f["realized_slippage_krw"])
            for f in p["fills"]
            if f["realized_slippage_krw"] is not None
        ]
        if any(s > 0 for s in slips):
            problems.append("요청 당시 참고가보다 불리한 체결이 있습니다.")
            hypotheses.append(
                "동일 전략의 스프레드·주문 지연별 비용을 검증 구간에서 비교하세요."
            )
        hypotheses.append(
            "유사 거래 표본을 늘려 비용 포함 기대수익을 별도 검증하세요. 손실 만회를 위한 주문 증액은 하지 않습니다."
        )
        model_analysis = []
        buy_signals = {f["signal_id"] for f in p["fills"] if f["side"] == "BUY"}
        model = getattr(self.market, "model", None)
        for event in self.evidence(p["symbol"]):
            if (
                event["entity_type"] != "SIGNAL"
                or event["entity_id"] not in buy_signals
            ):
                continue
            original = event["payload"]
            captured = original.get("ai")
            if captured:
                model_analysis.append(
                    {
                        "model_id": captured.get("model_id"),
                        "probability": captured.get("probability"),
                        "label": "당시 보존된 실험 모델 참고 점수",
                    }
                )
            bars = original.get("signal", {}).get("basis") or []
            if model and model.model and len(bars) >= 10:
                from .scanner import feature_vector
                from .scanner_model import predict

                recent = bars[-10:]
                if all(
                    b["start"] - a["start"] == 60 for a, b in zip(recent, recent[1:])
                ):
                    model_analysis.append(
                        {
                            "model_id": model.id,
                            "probability": predict(
                                model.model, feature_vector(recent[-5:], recent[:5])
                            ),
                            "label": "현재 로컬 모델 사후 재추론 · 학습에 해당 거래가 포함될 수 있어 검증 성과 아님",
                        }
                    )
        result = dict(
            engine="LOCAL_BATCH_REVIEW_V1",
            ai_model_used=bool(model_analysis),
            model_analysis=model_analysis,
            created_at=utcnow(),
            post_hoc=True,
            affects_live_strategy=False,
            strengths=strengths,
            problems=problems,
            hypotheses=hypotheses,
            similar_trades=[
                {
                    "id": q["id"],
                    "symbol": q["symbol"],
                    "realized_net_krw": q["realized_net_krw"],
                    "result": q["result"],
                }
                for q in similar
            ],
        )
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO trade_reviews VALUES(?,?,?,?)",
                (str(uuid4()), pid, utcnow(), json.dumps(result, ensure_ascii=False)),
            )
        return result

    def reviews(self, pid):
        self.position(pid)
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_reviews WHERE position_id=? ORDER BY created_at DESC LIMIT 10",
                (pid,),
            ).fetchall()
        return [
            {"created_at": r["created_at"], **json.loads(r["payload"])} for r in rows
        ]

    @decimal_math
    def performance(self, first, last):
        begin, end = date.fromisoformat(first), date.fromisoformat(last)
        if end < begin or (end - begin).days > 366:
            raise ValueError("성과 조회 기간은 최대 366일입니다.")
        groups = {}
        for p in self.positions():
            if p["scope"] != "STRATEGY":
                continue
            for f in p["fills"]:
                if (
                    not begin <= moment(f["filled_at"]).astimezone(KST).date() <= end
                    or f["side"] != "SELL"
                ):
                    continue
                g = groups.setdefault(
                    f["strategy_version"],
                    {
                        "strategy": f["strategy_version"],
                        "realized_net_krw": D(0),
                        "sell_fills": 0,
                        "unknown_pnl_fills": 0,
                    },
                )
                g["sell_fills"] += 1
                if f["realized_net_krw"] is None:
                    g["unknown_pnl_fills"] += 1
                else:
                    g["realized_net_krw"] += D(f["realized_net_krw"])
        return {
            "items": [
                {**g, "realized_net_krw": decimal_text(g["realized_net_krw"])}
                for g in groups.values()
            ],
            "actual_fills_only": True,
        }

    def csv(self, day, **filters):
        result = self.journal(day, page_size=1000000, **filters)
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(
            [
                "position_id",
                "symbol",
                "scope",
                "filled_at_kst",
                "side",
                "quantity",
                "price",
                "fee_krw",
                "realized_net_krw",
                "exit_reason",
                "result",
                "mode",
                "strategy",
                "note",
                "tags",
            ]
        )
        for p in result["items"]:
            for f in p["day_fills"]:
                row = [
                    p["id"],
                    p["symbol"],
                    p["scope"],
                    moment(f["filled_at"]).astimezone(KST).isoformat(),
                    f["side"],
                    f["quantity"],
                    f["price"],
                    f["fee_krw"],
                    f["realized_net_krw"],
                    f["exit_reason"],
                    f["result"],
                    f["mode"],
                    f["strategy_version"],
                    p["note"],
                    ",".join(p["tags"]),
                ]
                writer.writerow(
                    [
                        "'" + str(v)
                        if isinstance(v, str)
                        and v[:1] in ("=", "+", "-", "@", "\t", "\r")
                        else v
                        for v in row
                    ]
                )
        return "\ufeff" + stream.getvalue()
