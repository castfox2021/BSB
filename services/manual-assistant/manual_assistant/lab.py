import hashlib
import itertools
import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from .backtest import run, validate_bars
from .ledger import decimal_text, rebuild
from .performance import metrics
from .schema import version_id
from .strategy import parameters

D = Decimal


class Lab:
    def __init__(self, store):
        self.store = store

    def versions(self):
        with self.store.connect() as conn:
            return [
                {**dict(r), "params": json.loads(r["params"])}
                for r in conn.execute(
                    "SELECT * FROM strategy_versions ORDER BY created_at,id"
                )
            ]

    def active(self):
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT value FROM strategy_state WHERE key='active_version'"
            ).fetchone()
            return row[0] if row else None

    def add_version(self, kind, params):
        parameters(kind, params)
        identifier = version_id(kind, params)
        with self.store.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO strategy_versions VALUES (?,?,?,?)",
                (identifier, kind, json.dumps(params), datetime.now(UTC).isoformat()),
            )
            self.store.audit(conn, "strategy_created", {"version": identifier})
        return identifier

    def dataset(self, symbol, bars):
        validate_bars(bars)
        body = json.dumps(bars, sort_keys=True)
        identifier = hashlib.sha256((symbol + body).encode()).hexdigest()
        with self.store.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO lab_datasets VALUES (?,?,?,?,?)",
                (
                    identifier,
                    symbol,
                    body,
                    datetime.now(UTC).isoformat(),
                    "bithumb_public",
                ),
            )
        return identifier

    def evaluate(self, version, dataset, fee, slippage, grid=None):
        with self.store.connect() as conn:
            version_row = conn.execute(
                "SELECT * FROM strategy_versions WHERE id=?", (version,)
            ).fetchone()
            dataset_row = conn.execute(
                "SELECT * FROM lab_datasets WHERE id=?", (dataset,)
            ).fetchone()
        if not version_row or not dataset_row:
            raise ValueError("version/dataset not found")
        bars = json.loads(dataset_row["candles"])
        validate_bars(bars)
        split = int(len(bars) * 0.7)
        if split < 40 or len(bars) - split < 40:
            raise ValueError("학습·검증 각각 40봉 이상 필요합니다.")
        train, validation = bars[:split], bars[split:]
        kind = version_row["kind"]
        base = json.loads(version_row["params"])
        candidates = [base]
        if grid:
            if set(grid) - {
                "lookback",
                "volume_ratio",
                "stop_loss",
                "take_profit",
                "max_bars",
            }:
                raise ValueError("invalid grid")
            if any(
                not isinstance(v, list) or not 1 <= len(v) <= 5 for v in grid.values()
            ):
                raise ValueError("grid values must be bounded lists")
            candidates = [
                {**base, **dict(zip(grid, values, strict=True))}
                for values in itertools.product(*grid.values())
            ]
            if len(candidates) > 20:
                raise ValueError("최대 20개 조합")
        scored = []
        for params in candidates:
            parameters(kind, params)
            result = run(kind, params, train, fee, slippage)
            scored.append((D(result["net_profit"]), params, result))
        _, chosen, training = max(scored, key=lambda x: x[0])
        chosen_version = self.add_version(kind, chosen)
        # The holdout is evaluated once, AFTER parameter selection on training only.
        holdout = run(kind, chosen, validation, fee, slippage, warmup=train[-100:])
        stress = run(
            kind,
            chosen,
            validation,
            decimal_text(min(D("0.01"), D(fee) * 2)),
            decimal_text(min(D("0.05"), D(slippage) * 2)),
            warmup=train[-100:],
        )
        gate = (
            holdout["trades_count"] >= 5
            and D(holdout["net_profit"]) > 0
            and D(holdout["mdd"]) <= D("0.10")
            and D(fee) > 0
            and D(slippage) > 0
        )
        identifier = decimal_text(uuid4())
        report = {
            "id": identifier,
            "version_id": chosen_version,
            "dataset_id": dataset,
            "symbol": dataset_row["symbol"],
            "training": training,
            "cost_stress_validation": {
                k: v for k, v in stress.items() if k not in ("trades", "equity_curve")
            },
            "validation": holdout,
            "train_start": train[0]["start"],
            "train_end": train[-1]["start"] + 60,
            "validation_start": validation[0]["start"],
            "validation_end": validation[-1]["start"] + 60,
            "selected_on": "training_only",
            "combinations": len(candidates),
            "promotion_eligible": gate,
            "gate_reason": "검증 5회 이상, 순손익 양수, MDD 10% 이하, 양수 비용 가정 필요. 수익성 보장이 아닙니다.",
            "created_at": datetime.now(UTC).isoformat(),
            "auto_promoted": False,
        }
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO lab_runs VALUES (?,?,?,?,?)",
                (
                    identifier,
                    chosen_version,
                    dataset,
                    json.dumps(report),
                    report["created_at"],
                ),
            )
        return report

    def promote(self, version, report_id, approval):
        if approval != "실전 신호 적용 승인":
            raise ValueError("사용자 승인 문구가 필요합니다.")
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM lab_runs WHERE id=? AND version_id=?",
                (report_id, version),
            ).fetchone()
            if not row:
                raise ValueError("matching validation report required")
            report = json.loads(row["report"])
            if (
                not report["promotion_eligible"]
                or (
                    datetime.now(UTC) - datetime.fromisoformat(row["created_at"])
                ).total_seconds()
                > 72 * 3600
            ):
                raise ValueError("검증 미통과 또는 72시간 초과. 재검증 필요")
            previous = conn.execute(
                "SELECT value FROM strategy_state WHERE key='active_version'"
            ).fetchone()
            conn.execute(
                "INSERT OR REPLACE INTO strategy_state VALUES ('active_version',?)",
                (version,),
            )
            conn.execute(
                "INSERT INTO promotion_history(version_id,previous_version,report_id,created_at) VALUES (?,?,?,?)",
                (
                    version,
                    previous[0] if previous else None,
                    report_id,
                    datetime.now(UTC).isoformat(),
                ),
            )
            self.store.audit(
                conn,
                "user_strategy_promotion",
                {
                    "version": version,
                    "previous": previous[0] if previous else None,
                    "report": report_id,
                },
            )
        return version

    def reports(self):
        with self.store.connect() as conn:
            return [
                json.loads(r[0])
                for r in conn.execute(
                    "SELECT report FROM lab_runs ORDER BY created_at DESC LIMIT 20"
                )
            ]

    def compare(self):
        with self.store.connect() as conn:
            manual = [
                dict(r)
                for r in conn.execute(
                    "SELECT m.*,l.signal_id FROM manual_fills m LEFT JOIN fill_links l ON m.id=l.fill_id ORDER BY m.filled_at,m.recorded_at,m.id"
                )
            ]
            paper = [
                json.loads(r[0])
                for r in conn.execute(
                    "SELECT payload FROM paper_trades ORDER BY created_at DESC LIMIT 1000"
                )
            ]
            signals = conn.execute(
                "SELECT count(*) FROM signals WHERE type='BUY_CANDIDATE'"
            ).fetchone()[0]
        ledger = rebuild(manual, self.store.settings()["initial_capital_krw"])
        with self.store.connect() as conn:
            samples = [
                dict(r)
                for r in conn.execute("SELECT * FROM performance_samples ORDER BY at")
            ]
        grouped = {}
        for trade in paper:
            grouped.setdefault(trade["version_id"], []).append(trade)
        summaries = {}
        for version, trades in grouped.items():
            summaries[version] = metrics(
                [t["net_pnl"] for t in trades if t["side"] == "SELL"],
                [
                    r["equity"]
                    for r in samples
                    if r["channel"] == "PAPER" and r["version_id"] == version
                ],
                decimal_text(sum((D(t.get("fee_krw", "0")) for t in trades), D(0))),
                decimal_text(
                    sum((D(t.get("slippage_krw", "0")) for t in trades), D(0))
                ),
            )
        manual_metrics = metrics(
            ledger["closed_campaign_pnls"],
            [r["equity"] for r in samples if r["channel"] == "MANUAL"],
            decimal_text(sum((D(f["fee_krw"]) for f in manual), D(0))),
        )
        manual_metrics["net_realized_krw"] = ledger["realized_net_krw"]
        manual_metrics["basis"] = (
            "closed position campaigns; partial sells included only in net realized"
        )
        return {
            "manual_metrics": manual_metrics,
            "paper_metrics": summaries,
            "manual_fills": manual,
            "paper_trades": paper,
            "live_buy_signals": signals,
            "manual_attribution_coverage": sum(bool(f["signal_id"]) for f in manual),
            "note": "체결 누락·수동 실행 지연·귀속 차이가 있어 동일 조건의 인과 비교가 아닙니다.",
        }
