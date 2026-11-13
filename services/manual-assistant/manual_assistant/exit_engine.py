"""Pure exit decision engine for Task 17 partial take-profit and dynamic trailing."""

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

D = Decimal
ROUND_TRIP_COST = D("0.0028")
QTY_STEP = D("0.00000001")


def quantize_qty(qty: Decimal) -> Decimal:
    if qty <= 0:
        return D("0")
    return qty.quantize(QTY_STEP, rounding=ROUND_DOWN)


@dataclass(frozen=True)
class ExitIntent:
    leg: str  # TP1 | TP2 | RUNNER | STOP | CANCEL_PENDING
    target_quantity: Decimal
    reason: str
    client_order_id: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExitDecision:
    state_after: str
    peak_price: Decimal
    protective_stop_price: Decimal
    intents: tuple[ExitIntent, ...] = ()
    events: tuple[tuple[str, str, dict], ...] = ()  # (event_type, reason, metadata)


def evaluate_exit_step(
    *,
    position_id: str,
    state: str,
    entry_avg_price: Decimal,
    initial_quantity: Decimal,
    remaining_quantity: Decimal,
    peak_price: Decimal,
    protective_stop_price: Decimal,
    task16_stop_price: Decimal,
    latest_price: Decimal | None,
    tp1_pct: Decimal,
    tp1_fraction: Decimal,
    tp2_pct: Decimal,
    tp2_fraction: Decimal,
    trailing_drawdown_pct: Decimal,
    break_even_after_tp1: bool = False,
    min_order_krw: Decimal = D("5000"),
    legs: dict[str, dict] | None = None,
    rebound_failure_triggered: bool = False,
    is_stale: bool = False,
    attempt_generation: int = 1,
) -> ExitDecision:
    """Pure decision function: returns next state, monotonic stop, and order intents."""
    legs = legs or {}
    if state in ("CLOSED", "RECONCILING"):
        return ExitDecision(
            state_after=state,
            peak_price=peak_price,
            protective_stop_price=protective_stop_price,
        )
    if latest_price is None or latest_price <= 0 or is_stale:
        return ExitDecision(
            state_after=state,
            peak_price=peak_price,
            protective_stop_price=protective_stop_price,
            events=(("SAFETY_HOLD", "INVALID_OR_STALE_PRICE", {}),),
        )
    if remaining_quantity <= 0:
        return ExitDecision(
            state_after="CLOSED",
            peak_price=max(peak_price, latest_price),
            protective_stop_price=protective_stop_price,
        )

    new_peak = max(peak_price, latest_price)
    tp1_leg = legs.get("TP1") or {}
    tp2_leg = legs.get("TP2") or {}
    tp1_status = tp1_leg.get("status", "NONE")
    tp2_status = tp2_leg.get("status", "NONE")
    tp1_done = tp1_status in ("FILLED", "DEFERRED")
    tp2_done = tp2_status in ("FILLED", "DEFERRED")

    # Monotonic protective stop update
    candidate_stop = max(protective_stop_price, task16_stop_price)
    if tp1_done and break_even_after_tp1:
        candidate_stop = max(candidate_stop, entry_avg_price * (1 + ROUND_TRIP_COST))
    if state == "RUNNER_ACTIVE" or tp2_done:
        candidate_stop = max(candidate_stop, new_peak * (1 - trailing_drawdown_pct))

    # Priority 1: Pending TP cancellation if stop triggers
    pending_legs = [
        leg_name
        for leg_name, leg_data in legs.items()
        if leg_data.get("status") == "PENDING"
    ]

    # Priority 2: Stop-loss / Trailing stop / Rebound failure
    stop_hit = latest_price <= candidate_stop
    if stop_hit or rebound_failure_triggered:
        if pending_legs:
            return ExitDecision(
                state_after="RECONCILING",
                peak_price=new_peak,
                protective_stop_price=candidate_stop,
                intents=(
                    ExitIntent(
                        leg="CANCEL_PENDING",
                        target_quantity=D("0"),
                        reason="STOP_PREEMPTS_PENDING_TP",
                        client_order_id=f"{position_id}:CANCEL:{attempt_generation}",
                        metadata={"pending_legs": pending_legs},
                    ),
                ),
                events=(
                    (
                        "CANCEL_REQUESTED",
                        "STOP_PREEMPTS_PENDING_TP",
                        {"pending_legs": pending_legs},
                    ),
                ),
            )
        reason = (
            "TRAILING_STOP"
            if (state == "RUNNER_ACTIVE" or tp2_done) and stop_hit
            else "STRUCTURE_STOP"
            if stop_hit
            else "REBOUND_FAILURE"
        )
        if remaining_quantity * latest_price < min_order_krw:
            return ExitDecision(
                state_after="CLOSED",
                peak_price=new_peak,
                protective_stop_price=candidate_stop,
                events=(
                    (
                        "DUST_REMAINDER",
                        reason,
                        {
                            "remaining_qty": str(remaining_quantity),
                            "notional_krw": str(remaining_quantity * latest_price),
                        },
                    ),
                ),
            )
        exit_leg = "RUNNER" if reason == "TRAILING_STOP" else "STOP"
        return ExitDecision(
            state_after="STOP_PENDING",
            peak_price=new_peak,
            protective_stop_price=candidate_stop,
            intents=(
                ExitIntent(
                    leg=exit_leg,
                    target_quantity=remaining_quantity,
                    reason=reason,
                    client_order_id=f"{position_id}:{exit_leg}:{attempt_generation}",
                ),
            ),
            events=(("STOP_TRIGGERED", reason, {"price": str(latest_price)}),),
        )

    # Priority 3: Pending orders still open
    if pending_legs:
        return ExitDecision(
            state_after=state,
            peak_price=new_peak,
            protective_stop_price=candidate_stop,
        )

    intents: list[ExitIntent] = []
    events: list[tuple[str, str, dict]] = []
    sim_remaining = remaining_quantity
    next_state = state

    tp1_target = entry_avg_price * (1 + tp1_pct)
    tp2_target = entry_avg_price * (1 + tp2_pct)

    # Priority 4: TP1 (+1.2%, 25% of initial)
    if tp1_status == "NONE" and latest_price >= tp1_target and sim_remaining > 0:
        tp1_qty = min(sim_remaining, quantize_qty(initial_quantity * tp1_fraction))
        if tp1_qty <= 0 or tp1_qty * latest_price < min_order_krw:
            events.append(
                (
                    "DUST_DEFERRED",
                    "TP1_BELOW_MIN_ORDER",
                    {
                        "target_qty": str(tp1_qty),
                        "notional_krw": str(tp1_qty * latest_price),
                    },
                )
            )
            tp1_status = "DEFERRED"
            next_state = "TP1_DONE"
        else:
            intents.append(
                ExitIntent(
                    leg="TP1",
                    target_quantity=tp1_qty,
                    reason="TP1_TARGET_HIT",
                    client_order_id=f"{position_id}:TP1:{attempt_generation}",
                    metadata={"target_price": str(tp1_target)},
                )
            )
            sim_remaining -= tp1_qty
            next_state = "TP1_PENDING"
            if break_even_after_tp1:
                candidate_stop = max(
                    candidate_stop, entry_avg_price * (1 + ROUND_TRIP_COST)
                )

    # Priority 5: TP2 (+2.5%, 25% of initial, plus any deferred TP1)
    if tp2_status == "NONE" and latest_price >= tp2_target and sim_remaining > 0:
        fraction = tp2_fraction + (tp1_fraction if tp1_status == "DEFERRED" else D("0"))
        tp2_qty = min(sim_remaining, quantize_qty(initial_quantity * fraction))
        if tp2_qty <= 0 or tp2_qty * latest_price < min_order_krw:
            events.append(
                (
                    "DUST_DEFERRED",
                    "TP2_BELOW_MIN_ORDER",
                    {
                        "target_qty": str(tp2_qty),
                        "notional_krw": str(tp2_qty * latest_price),
                    },
                )
            )
            next_state = "RUNNER_ACTIVE"
            candidate_stop = max(candidate_stop, new_peak * (1 - trailing_drawdown_pct))
        else:
            intents.append(
                ExitIntent(
                    leg="TP2",
                    target_quantity=tp2_qty,
                    reason="TP2_TARGET_HIT",
                    client_order_id=f"{position_id}:TP2:{attempt_generation}",
                    metadata={"target_price": str(tp2_target)},
                )
            )
            sim_remaining -= tp2_qty
            next_state = "TP2_PENDING"

    return ExitDecision(
        state_after=next_state,
        peak_price=new_peak,
        protective_stop_price=candidate_stop,
        intents=tuple(intents),
        events=tuple(events),
    )
