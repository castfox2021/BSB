import logging
import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger('bsb')


def settings() -> dict[str, str]:
    if os.getenv('DATA_SOURCE', 'mock') != 'mock':
        raise ValueError('Task 01 supports DATA_SOURCE=mock only')
    return {key: os.environ[key] for key in ('DATABASE_URL', 'REDIS_URL')}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings()
    logger.info('service started mode=paper source=mock')
    yield
    logger.info('service stopped')


app = FastAPI(lifespan=lifespan)


async def check_dependencies() -> None:
    config = settings()
    async with await psycopg.AsyncConnection.connect(config['DATABASE_URL'], connect_timeout=2) as conn:
        await conn.execute('SELECT 1')
    redis = Redis.from_url(config['REDIS_URL'], socket_connect_timeout=2, socket_timeout=2)
    try:
        await redis.ping()
    finally:
        await redis.aclose()


@app.get('/health')
async def health():
    result = {'service': os.getenv('SERVICE_NAME', 'template'), 'mode': 'paper',
              'dataSource': 'mock', 'tradingEnabled': False}
    try:
        await check_dependencies()
        return {**result, 'status': 'ok', 'dependencies': {'postgres': 'ok', 'redis': 'ok'}}
    except Exception:
        logger.exception('dependency health check failed')
        return JSONResponse({**result, 'status': 'degraded'}, status_code=503)
