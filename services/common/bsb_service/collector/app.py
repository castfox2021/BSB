import json
import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from bsb_service.app import check_dependencies, settings

from .config import Config
from .runner import Collector, RedisSink

logger = logging.getLogger('bsb.collector.api')


def create_app(config: Config | None = None):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings()  # Shared infrastructure stays in mock/paper mode; collector source is independent.
        current = config or Config.from_env()
        redis = Redis.from_url(current.redis_url, decode_responses=True,
                               socket_connect_timeout=2, socket_timeout=2)
        collector = Collector(current, RedisSink(redis, current))
        app.state.collector = collector
        app.state.redis = redis
        try:
            async with collector.start():
                yield
        finally:
            await redis.aclose()

    app = FastAPI(title='BSB Public Market Collector', lifespan=lifespan)

    @app.get('/health')
    async def health():
        collector = app.state.collector
        status = collector.state.status()
        result = {'service': 'market-collector', 'mode': 'paper',
                  'dataSource': collector.config.source, 'tradingEnabled': False,
                  'collector': status}
        try:
            await check_dependencies()
            if collector.config.source == 'bithumb_public' and not status['transport_ready']:
                return JSONResponse({**result, 'status': 'degraded'}, status_code=503)
            return {**result, 'status': 'ok', 'dependencies': {'postgres': 'ok', 'redis': 'ok'}}
        except Exception:
            logger.exception('collector health dependency failure')
            return JSONResponse({**result, 'status': 'degraded'}, status_code=503)

    @app.get('/collector/status')
    async def status():
        return app.state.collector.state.status()

    @app.get('/collector/markets')
    async def markets():
        state = app.state.collector.state
        return {'source': state.config.source, 'refreshed_at': state.refreshed_at,
                'metadata_fresh': state.status()['metadata_fresh'], 'markets': state.markets}

    @app.get('/collector/latest/{symbol}')
    async def latest(symbol: str):
        if not re.fullmatch(r'KRW-[A-Z0-9]+', symbol):
            raise HTTPException(400, 'invalid KRW symbol')
        state = app.state.collector.state
        if symbol not in {market['symbol'] for market in state.markets}:
            raise HTTPException(404, 'market not subscribed')
        keys = [f'bsb:{state.config.source}:latest:{symbol}:market.{kind}'
                for kind in ('ticker', 'trade', 'orderbook')]
        try:
            values = await app.state.redis.mget(keys)
        except Exception as error:
            raise HTTPException(503, 'market cache unavailable') from error
        status = state.status()
        return {'symbol': symbol, 'source': state.config.source, 'entries_allowed': False,
                'fresh': status['metadata_fresh'] and symbol in status['fresh_symbols'],
                'events': [json.loads(value) for value in values if value]}

    return app


app = create_app()
