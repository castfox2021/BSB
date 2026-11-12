from decimal import Decimal

from .ledger import decimal_text

D = Decimal


def metrics(pnls, equity, fees="0", slippage=None):
    values = [D(p) for p in pnls]
    wins = [v for v in values if v > 0]
    losses = [-v for v in values if v < 0]
    peak = D("150000")
    drawdown = D(0)
    for value in equity:
        current = D(value)
        peak = max(peak, current)
        drawdown = max(drawdown, (peak - current) / peak)
    return {
        "observations": len(values),
        "net_realized_krw": decimal_text(sum(values, D(0))),
        "win_rate": decimal_text(D(len(wins)) / len(values)) if values else None,
        "payoff_ratio": decimal_text(
            (sum(wins) / len(wins)) / (sum(losses) / len(losses))
        )
        if wins and losses
        else None,
        "expectancy_krw": decimal_text(sum(values) / len(values)) if values else None,
        "sampled_equity_mdd": decimal_text(drawdown) if equity else None,
        "equity_samples": len(equity),
        "fees_krw": fees,
        "slippage_krw": slippage,
        "mdd_sampling": "1 minute; missing quotes omitted",
    }
