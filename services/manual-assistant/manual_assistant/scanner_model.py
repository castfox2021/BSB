"""Small local logistic model. Advisory only; never approves live orders."""

import hashlib
import json
import math
from datetime import UTC, datetime

from .scanner import feature_vector


def sigmoid(x):
    return 1 / (1 + math.exp(-max(-40.0, min(40.0, x))))


def fit(samples):
    if len(samples) < 200:
        raise ValueError("학습 표본 최소 200개 필요")
    samples = sorted(samples, key=lambda s: (s["at"], s["symbol"]))
    boundary = samples[int(len(samples) * 0.7)]["at"]
    train = [s for s in samples if s["label_end"] < boundary]
    validation = [s for s in samples if s["at"] >= boundary]
    if len(train) < 100 or len(validation) < 40 or len({s["y"] for s in train}) < 2:
        raise ValueError("시간 분리 학습·검증 표본 또는 클래스 부족")
    means = [sum(s["x"][j] for s in train) / len(train) for j in range(4)]
    scales = [
        max(
            1e-6,
            math.sqrt(sum((s["x"][j] - means[j]) ** 2 for s in train) / len(train)),
        )
        for j in range(4)
    ]
    normalized = [
        (
            [
                max(-10.0, min(10.0, (s["x"][j] - means[j]) / scales[j]))
                for j in range(4)
            ],
            s["y"],
        )
        for s in train
    ]
    weights = [0.0] * 4
    bias = 0.0
    for _ in range(60):
        gradient = [0.0] * 4
        bgrad = 0.0
        for x, y in normalized:
            delta = sigmoid(bias + sum(w * v for w, v in zip(weights, x))) - y
            bgrad += delta
            for j in range(4):
                gradient[j] += delta * x[j]
        for j in range(4):
            weights[j] -= 0.05 * (gradient[j] / len(train) + 0.01 * weights[j])
        bias -= 0.05 * bgrad / len(train)
    model = dict(
        kind="local-logistic-v1", means=means, scales=scales, weights=weights, bias=bias
    )
    probabilities = [predict(model, s["x"]) for s in validation]
    accuracy = sum(
        (p >= 0.5) == s["y"] for p, s in zip(probabilities, validation)
    ) / len(validation)
    positive = sum(s["y"] for s in validation) / len(validation)
    metrics = dict(
        train_samples=len(train),
        validation_samples=len(validation),
        train_label_end=max(s["label_end"] for s in train),
        validation_start=min(s["at"] for s in validation),
        validation_accuracy=accuracy,
        majority_baseline=max(positive, 1 - positive),
        brier_score=sum((p - s["y"]) ** 2 for p, s in zip(probabilities, validation))
        / len(validation),
        target="다음 완료 3분 수익률이 비용 가정(왕복 수수료+슬리피지)을 초과",
        advisory_only=True,
    )
    return model, metrics


def predict(model, x):
    normalized = [
        max(-10.0, min(10.0, (x[j] - model["means"][j]) / model["scales"][j]))
        for j in range(4)
    ]
    return sigmoid(
        model["bias"] + sum(w * v for w, v in zip(model["weights"], normalized))
    )


class ScannerModel:
    def __init__(self, store):
        self.store = store
        self.model = None
        self.id = None
        self.metrics = None
        self.last_error = None
        self.training = False
        self.inferences = 0
        with store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM scanner_models ORDER BY created_at DESC,id DESC LIMIT 1"
            ).fetchone()
        if row:
            self.model = json.loads(row["model"])
            self.id = row["id"]
            self.metrics = json.loads(row["metrics"])

    def train_local(self):
        # Same timestamp for every market; purge labels crossing the temporal split.
        with self.store.connect() as conn:
            rows = conn.execute("""SELECT symbol,start,open,high,low,close,volume,turnover FROM market_candles
                WHERE timeframe=60 AND closed=1 AND complete=1 ORDER BY start DESC,symbol LIMIT 16000""").fetchall()
        grouped = {}
        for row in reversed(rows):
            grouped.setdefault(row["symbol"], []).append(dict(row))
        costs = self.store.settings()
        threshold = 2 * (
            float(costs["execution_fee_rate"]) + float(costs["execution_slippage_rate"])
        )
        samples = []
        for symbol, bars in grouped.items():
            for i in range(9, len(bars) - 3):
                block = bars[i - 9 : i + 4]
                if any(b["start"] - a["start"] != 60 for a, b in zip(block, block[1:])):
                    continue
                samples.append(
                    dict(
                        symbol=symbol,
                        at=bars[i]["start"] + 60,
                        label_end=bars[i + 3]["start"] + 60,
                        x=feature_vector(bars[i - 4 : i + 1], bars[i - 9 : i - 4]),
                        y=int(
                            float(bars[i + 3]["close"]) / float(bars[i]["close"]) - 1
                            > threshold
                        ),
                    )
                )
        # Bounded CPU: preserve time ordering with deterministic sampling across markets.
        samples = sorted(samples, key=lambda s: (s["at"], s["symbol"]))
        if len(samples) > 5000:
            step = len(samples) / 5000
            samples = [samples[int(i * step)] for i in range(5000)]
        model, metrics = fit(samples)
        metrics["cost_threshold"] = threshold
        created = datetime.now(UTC).isoformat()
        version = hashlib.sha256(
            json.dumps([model, metrics], sort_keys=True).encode()
        ).hexdigest()[:20]
        with self.store.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO scanner_models VALUES(?,?,?,?,?)",
                (
                    version,
                    json.dumps(model),
                    json.dumps(metrics),
                    created,
                    "EXPERIMENTAL_ADVISORY",
                ),
            )
        return version, model, metrics

    def inference(self, symbol, bars, is_candidate):
        if not is_candidate:
            raise ValueError("후보 외 모델 추론 금지")
        if self.model is None or len(bars) < 10:
            return None
        recent = bars[-10:]
        if any(not b.get("complete", True) for b in recent) or any(
            b["start"] - a["start"] != 60 for a, b in zip(recent, recent[1:])
        ):
            return None
        self.inferences += 1
        return dict(
            model_id=self.id,
            probability=predict(self.model, feature_vector(recent[-5:], recent[:5])),
            advisory_only=True,
            trained_status="EXPERIMENTAL_ADVISORY",
        )

    def status(self):
        return dict(
            state="TRAINING"
            if self.training
            else "EXPERIMENTAL_ADVISORY"
            if self.model
            else "UNTRAINED",
            model_id=self.id,
            metrics=self.metrics,
            inference_count=self.inferences,
            error=self.last_error,
            affects_live_strategy=False,
        )
