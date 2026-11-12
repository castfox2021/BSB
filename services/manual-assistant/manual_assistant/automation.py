"""Explicitly enabled, version-pinned execution of approved signals only."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from .ledger import amount
from .manual_orders import OrderBlocked
from .private_sync import now


class Automation:
    def __init__(self, orders):
        self.orders = orders
        self.store = orders.store
        self.engine = orders.engine
        self.active = False
        self.version = None
        self.budget = "10000"
        self.started_at = None
        self.reason = "자동매매 정지"
        self.lock = asyncio.Lock()
        self.attempt_task = None
        orders.automation = self

    def event(self, kind, message, signal_id=None):
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO automation_events(account_id,kind,message,signal_id,created_at) VALUES(?,?,?,?,?)",
                (self.orders.private.account, kind, message, signal_id, now()),
            )

    def state(self):
        return dict(
            active=self.active and self.orders.active,
            version=self.version,
            budget_krw=self.budget,
            started_at=self.started_at,
            reason=self.reason,
            configured=self.orders.private.credentials is not None,
            approved_version=self.engine.lab.active(),
            auto_order_enabled=self.active and self.orders.active,
            buy_block_reasons=self.engine.risk()["reasons"],
            restart_policy="STOPPED",
        )

    def account_guard(self):
        reasons = self.orders.private.guard()
        if self.active and self.orders.active:
            try:
                self.orders.ready()
            except OrderBlocked:
                return reasons or ["자동매매 계좌 동기화 확인 필요"]
            # The initial baseline was approved. Only our newly imported fills may
            # reconcile automatically; ready() rejects any new external fill.
            reasons = [
                r
                for r in reasons
                if r != "자동 체결 매매노트와 전략 수동 원장 대조 필요"
            ]
        return reasons

    def activate(self, approval, version, budget, permissions_confirmed):
        if self.active:
            raise OrderBlocked("자동매매를 정지한 후 설정을 변경하세요.")
        if (
            approval != "신호에 따라 실제 자동매매를 시작합니다"
            or permissions_confirmed is not True
        ):
            raise OrderBlocked("자동 주문 및 API 주문하기 권한 확인이 필요합니다.")
        value = amount(budget)
        if value != value.to_integral_value() or not Decimal("5000") <= value <= min(
            Decimal("50000"), Decimal(self.store.settings()["max_entry_krw"])
        ):
            raise OrderBlocked("자동 매수 예산은 정수 원 단위 5,000~50,000원입니다.")
        if not version or version != self.engine.lab.active():
            raise OrderBlocked(
                "Strategy Lab에서 검증·승인한 실전 전략을 먼저 선택하세요."
            )
        self.orders.ready()
        self.orders.require_no_open()
        previous_budget = getattr(self.orders.market, "analysis_budget", None)
        if hasattr(self.orders.market, "set_analysis_budget"):
            self.orders.market.set_analysis_budget(budget)
        try:
            risk = self.engine.risk()
            if risk["reasons"]:
                raise OrderBlocked("시작 조건 미충족: " + " · ".join(risk["reasons"]))
            self.orders.activate("실제 매수·매도를 직접 확인합니다")
        except Exception:
            if hasattr(self.orders.market, "set_analysis_budget"):
                self.orders.market.set_analysis_budget(previous_budget)
            raise
        self.version = version
        self.budget = budget
        self.started_at = now()
        self.reason = "신호 대기"
        self.active = True
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE trading_control SET mode='AUTO_APPROVAL',state='RUNNING',updated_at=? WHERE id=1",
                (now(),),
            )
            self.store.audit(
                conn,
                "AUTOMATION_ENABLED",
                {
                    "version": version,
                    "budget_krw": budget,
                    "permissions": "USER_CONFIRMED",
                    "auto_orders": True,
                },
            )
        self.event("STARTED", "자동매매 활성화 · 승인 전략과 고정 예산 사용")
        return self.state()

    def stop(self, reason="사용자 정지", stop_orders=True):
        was_active = self.active
        self.active = False
        self.reason = reason
        if (
            self.attempt_task
            and not self.attempt_task.done()
            and self.attempt_task is not asyncio.current_task()
        ):
            self.attempt_task.cancel()
        if stop_orders:
            self.orders.stop()
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE trading_control SET state=CASE WHEN state='EMERGENCY_STOP' THEN state ELSE 'STOPPED' END,updated_at=? WHERE id=1",
                (now(),),
            )
        if was_active:
            self.event("STOPPED", reason)
        return self.state()

    def authorize(self, signal_id, side):
        if not self.active or not self.orders.active:
            raise OrderBlocked("자동매매 정지")
        with self.store.connect() as conn:
            signal = conn.execute(
                "SELECT * FROM signals WHERE id=?", (signal_id,)
            ).fetchone()
            claim = conn.execute(
                "SELECT status FROM auto_signal_claims WHERE account_id=? AND signal_id=?",
                (self.orders.private.account, signal_id),
            ).fetchone()
        if not signal or not claim or claim["status"] != "CLAIMED":
            raise OrderBlocked("신호 실행 권한 없음")
        if signal["generated_at"] < self.started_at or datetime.fromisoformat(
            signal["expires_at"]
        ) <= datetime.now(UTC):
            raise OrderBlocked("시작 전 또는 만료 신호")
        if side == "BUY":
            if (
                signal["type"] != "BUY_CANDIDATE"
                or signal["strategy_version"] != self.version
                or self.engine.lab.active() != self.version
            ):
                raise OrderBlocked("승인된 전략의 매수 신호만 자동 실행합니다.")
            if (
                not self.engine.risk()["buy_signals_enabled"]
                or not self.orders.market.safe(signal["symbol"])[0]
            ):
                raise OrderBlocked("신규 매수 위험관리·시세 조건 미충족")
            if any(
                p["symbol"] == signal["symbol"]
                for p in self.orders.ledger()["positions"]
            ):
                raise OrderBlocked("자동 추가 매수는 하지 않습니다.")
            with self.store.connect() as conn:
                recent = conn.execute(
                    """SELECT 1 FROM manual_orders o JOIN auto_signal_claims c ON c.order_intent_id=o.id
                    WHERE o.account_id=? AND o.market=? AND o.created_at>? AND o.signal_id!=? LIMIT 1""",
                    (
                        self.orders.private.account,
                        signal["symbol"],
                        (datetime.now(UTC) - timedelta(seconds=60)).isoformat(),
                        signal_id,
                    ),
                ).fetchone()
            if recent:
                raise OrderBlocked("동일 종목 재진입 대기 중")
        elif side == "SELL":
            if (
                signal["type"] not in ("SELL_CANDIDATE", "RISK_EXIT")
                or signal["strategy_version"] != "manual-risk"
            ):
                raise OrderBlocked("전략 원장의 매도·손절 신호만 자동 실행합니다.")
            if not any(
                p["symbol"] == signal["symbol"]
                for p in self.orders.ledger()["positions"]
            ):
                raise OrderBlocked("전략 보유분 없음")
        else:
            raise OrderBlocked("신호 방향 오류")

    async def tick(self):
        if not self.active:
            return
        if not self.orders.active:
            self.stop("주문 실행 세션이 중지되었습니다.", False)
            return
        try:
            self.orders.ready()
            self.orders.require_no_open()
        except OrderBlocked as error:
            self.reason = str(error)
            return
        with self.store.connect() as conn:
            signals = [
                dict(r)
                for r in conn.execute(
                    """SELECT * FROM signals WHERE generated_at>=? AND expires_at>?
                AND type IN ('BUY_CANDIDATE','SELL_CANDIDATE','RISK_EXIT')
                ORDER BY CASE type WHEN 'RISK_EXIT' THEN 0 WHEN 'SELL_CANDIDATE' THEN 1 ELSE 2 END,generated_at DESC,id DESC LIMIT 100""",
                    (self.started_at, now()),
                )
            ]
        for signal in signals:
            side = "BUY" if signal["type"] == "BUY_CANDIDATE" else "SELL"
            # Never claim an unapproved strategy, so it cannot be replayed later.
            if side == "BUY" and signal["strategy_version"] != self.version:
                continue
            with self.store.connect() as conn:
                claimed = conn.execute(
                    "INSERT OR IGNORE INTO auto_signal_claims VALUES(?,?,NULL,?,?,?)",
                    (self.orders.private.account, signal["id"], "CLAIMED", now(), None),
                ).rowcount
            if not claimed:
                continue
            intent = None
            try:
                self.authorize(signal["id"], side)
                value = self.budget
                if side == "SELL":
                    value = next(
                        p["quantity"]
                        for p in self.orders.ledger()["positions"]
                        if p["symbol"] == signal["symbol"]
                    )
                preview = await self.orders.preview(
                    signal["symbol"], side, value, signal["id"], _automation=self
                )
                intent = preview["id"]
                result = await self.orders.submit(
                    intent, preview["confirmation"], _automation=self
                )
                status = result["status"]
                self.reason = result.get("message", "주문 상태 " + status)
                self.event(
                    status, f"{signal['symbol']} · {side} · {self.reason}", signal["id"]
                )
                if status in ("UNKNOWN", "REJECTED"):
                    self.stop(
                        "주문 결과 불명 또는 거절: 조회·설정 확인 후 다시 시작하세요."
                    )
            except (
                OrderBlocked,
                ValueError,
                ArithmeticError,
                KeyError,
                StopIteration,
            ) as error:
                status = "SKIPPED"
                # Only our fixed validation messages are persisted. No transport payloads.
                self.reason = (
                    str(error)
                    if isinstance(error, OrderBlocked)
                    else "주문 조건 검증 실패"
                )
                self.event("SKIPPED", self.reason, signal["id"])
            except Exception:
                status = "ERROR"
                self.stop("자동매매 오류: 주문 이력을 확인하세요.")
            with self.store.connect() as conn:
                conn.execute(
                    "UPDATE auto_signal_claims SET order_intent_id=COALESCE(order_intent_id,?),status=?,message=? WHERE account_id=? AND signal_id=?",
                    (
                        intent,
                        status,
                        self.reason,
                        self.orders.private.account,
                        signal["id"],
                    ),
                )
            # One order attempt per tick; exits always win over fresh entries.
            return
        self.reason = (
            "신호 대기"
            if self.engine.lab.active() == self.version
            else "전략 변경: 신규 매수 중지 · 기존 보유분 청산 신호 감시"
        )

    async def run(self):
        while True:
            try:
                async with self.lock:
                    self.attempt_task = asyncio.create_task(self.tick())
                    await self.attempt_task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            except Exception:
                self.stop("자동매매 오류: 사용자 확인 필요")
            await asyncio.sleep(1)
