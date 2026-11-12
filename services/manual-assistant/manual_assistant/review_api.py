import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Query, Request
from fastapi.responses import Response

from .review_events import utcnow
from .trade_review import TradeReview, moment


def register_review(app, store, read_body):
    def service():
        return TradeReview(store, app.state.market, app.state.private.account)

    @app.get("/api/trading/candidates")
    def candidates(symbol: str = "KRW-BTC"):
        if not re.fullmatch(r"KRW-[A-Z0-9]+", symbol):
            raise ValueError("invalid market")
        market = app.state.market
        market.request_detail(symbol)
        return {
            "items": market.candidates(),
            "selected": symbol,
            "snapshot": {
                k: v
                for k, v in market.latest.get(symbol, {}).items()
                if k in ("trade", "ticker", "orderbook")
            },
            "detail": {
                k: v
                for k, v in market.detail_cache.get(symbol, {}).items()
                if k != "at"
            },
            "received_at": utcnow(),
            "risk": app.state.engine.risk(),
        }

    @app.get("/api/trading/positions")
    def positions():
        return {"items": [p for p in service().positions() if p["status"] == "OPEN"]}

    @app.get("/api/trading/journal")
    def journal(
        date: str | None = None,
        timezone: str = "Asia/Seoul",
        symbol: str | None = None,
        strategy: str | None = None,
        mode: str | None = None,
        scope: str = "STRATEGY",
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=50),
    ):
        if timezone != "Asia/Seoul" or scope not in ("STRATEGY", "EXTERNAL", "ALL"):
            raise ValueError("한국시간 및 STRATEGY/EXTERNAL/ALL 범위를 사용하세요.")
        day = date or datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        return service().journal(day, symbol, strategy, mode, scope, page, page_size)

    @app.get("/api/trading/journal.csv")
    def export_journal(
        date: str,
        symbol: str | None = None,
        strategy: str | None = None,
        mode: str | None = None,
        scope: str = "STRATEGY",
    ):
        return Response(
            service().csv(
                date, symbol=symbol, strategy=strategy, mode=mode, scope=scope
            ),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="bsb-journal-{date}.csv"'
            },
        )

    @app.get("/api/trading/chart")
    def chart(
        symbol: str,
        timeframe: int = 60,
        cursor: str | None = None,
        from_at: str | None = None,
        to_at: str | None = None,
    ):
        if not re.fullmatch(r"KRW-[A-Z0-9]+", symbol):
            raise ValueError("invalid market")
        end = moment(to_at or utcnow())
        begin = moment(from_at) if from_at else end - timedelta(minutes=90)
        return service().chart(
            symbol,
            begin.isoformat(),
            end.isoformat(),
            cursor or end.isoformat(),
            timeframe,
        )

    @app.get("/api/trading/positions/{pid}/replay")
    def replay(
        pid: str,
        cursor: str | None = None,
        timeframe: int = 60,
        before: int = Query(30, ge=0, le=720),
        after: int = Query(30, ge=0, le=720),
    ):
        return service().replay(pid, cursor, timeframe, before, after)

    @app.get("/api/trading/positions/{pid}/reasoning")
    def reasoning(pid: str, cursor: str | None = None):
        return service().reasoning(pid, cursor)

    @app.patch("/api/trading/positions/{pid}/journal")
    async def edit_journal(pid: str, request: Request):
        data = await read_body(request)
        if set(data) - {"note", "tags"}:
            raise ValueError("알 수 없는 메모 필드")
        return service().save_note(pid, data.get("note", ""), data.get("tags", []))

    @app.get("/api/trading/positions/{pid}/reviews")
    def reviews(pid: str):
        return {"items": service().reviews(pid)}

    @app.post("/api/trading/positions/{pid}/review")
    async def create_review(pid: str, request: Request):
        data = await read_body(request)
        if data:
            raise HTTPException(422, "복기 요청은 빈 JSON을 사용하세요.")
        import asyncio

        return await asyncio.to_thread(service().review, pid)

    @app.get("/api/trading/performance")
    def performance(from_date: str, to_date: str):
        return service().performance(from_date, to_date)
