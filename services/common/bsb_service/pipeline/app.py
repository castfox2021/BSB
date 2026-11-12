import asyncio
import contextlib
import re
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row

from bsb_service.app import check_dependencies, settings

from .config import Config
from .runner import Pipeline


def json_values(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, datetime):
        return value.isoformat().replace('+00:00', 'Z')
    if isinstance(value, dict):
        return {key:json_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_values(item) for item in value]
    return value


def create_app(config: Config | None = None):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings()
        current = config or Config.from_env()
        pipeline = Pipeline(current)
        app.state.pipeline = pipeline
        task = asyncio.create_task(pipeline.run())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title='BSB Market Data Pipeline', lifespan=lifespan)

    @app.get('/health')
    async def health():
        status = app.state.pipeline.status()
        result = {'service':'candle-builder', 'mode':'paper', 'dataSource':status['source'],
                  'tradingEnabled':False, 'pipeline':status}
        try:
            await check_dependencies()
            if status['ready']:
                return {**result, 'status':'ok'}
        except Exception:
            pass
        return JSONResponse({**result, 'status':'degraded'}, status_code=503)

    @app.get('/pipeline/status')
    async def status():
        return app.state.pipeline.status()

    async def query(statement, parameters):
        try:
            async with await psycopg.AsyncConnection.connect(
                app.state.pipeline.config.database_url, row_factory=dict_row,
                connect_timeout=3, options='-c statement_timeout=5000') as connection:
                cursor = await connection.execute(statement, parameters)
                return json_values(await cursor.fetchall())
        except Exception as error:
            raise HTTPException(503, 'pipeline database unavailable') from error

    @app.get('/pipeline/candles/{symbol}')
    async def candles(symbol: str, timeframe: int = Query(60),
                      closed_only: bool = False, complete_only: bool = False,
                      limit: int = Query(100, ge=1, le=1000)):
        if timeframe not in (60, 300):
            raise HTTPException(422, 'timeframe must be 60 or 300 seconds')
        if not re.fullmatch(r'KRW-[A-Z0-9]+', symbol):
            raise HTTPException(400, 'invalid KRW symbol')
        source = app.state.pipeline.config.source
        rows = await query('''SELECT * FROM candles WHERE source=%s AND symbol=%s AND timeframe=%s
            AND (NOT %s OR closed) AND (NOT %s OR complete) ORDER BY bucket_start DESC LIMIT %s''',
                           (source, symbol, timeframe, closed_only, complete_only, limit))
        return {'source':source, 'entries_allowed':False, 'candles':list(reversed(rows))}

    @app.get('/pipeline/issues')
    async def issues(limit: int = Query(100, ge=1, le=1000)):
        source = app.state.pipeline.config.source
        rows = await query('SELECT * FROM data_quality_events WHERE source=%s ORDER BY id DESC LIMIT %s',
                           (source, limit))
        return {'source':source, 'issues':rows}

    return app


app = create_app()
