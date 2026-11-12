import asyncio
import logging
import time
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from .config import Config
from .migrate import upgrade
from .store import Store
from .validation import stream_id

logger = logging.getLogger('bsb.pipeline')


async def stream_info(redis, key):
    try:
        return await redis.xinfo_stream(key)
    except ResponseError as error:
        if 'no such key' not in str(error).lower():
            raise
        return None


class Pipeline:
    def __init__(self, config: Config):
        self.config = config
        self.ready = False
        self.last_error: str | None = None
        self.last_poll = 0.
        self.last_stream_id = '0-0'
        self.caught_up = False
        self.counts = {'accepted':0, 'duplicates':0, 'rejected':0, 'closed':0}

    def status(self):
        return {'source':self.config.source, 'mode':'paper', 'entries_allowed':False,
                'ready':self.ready and time.monotonic() - self.last_poll < 15,
                'last_stream_id':self.last_stream_id, 'caught_up':self.caught_up,
                'last_error':self.last_error, 'session_counts':dict(self.counts)}

    async def step(self, redis, store: Store, *, block=1000):
        info = await stream_info(redis, self.config.stream)
        first = info['first-entry'][0] if info and info['first-entry'] else None
        last = info['last-generated-id'] if info else None
        cursor = await store.retention_gap(first, last, datetime.now(UTC))
        result = await redis.xread({self.config.stream:cursor}, count=self.config.batch_size, block=block)
        if result:
            stats = await store.apply(result[0][1], datetime.now(UTC))
            cursor = stats['last_stream_id']
            for key in ('accepted','duplicates','rejected'):
                self.counts[key] += stats[key]
        self.last_stream_id = cursor
        info = await stream_info(redis, self.config.stream)
        self.caught_up = not info or stream_id(cursor) >= stream_id(info['last-generated-id'])
        if self.caught_up:
            self.counts['closed'] += await store.finalize(datetime.now(UTC))
        self.last_poll = time.monotonic()
        self.ready = True
        self.last_error = None

    async def run(self):
        migrated = False
        while True:
            try:
                if not migrated:
                    await asyncio.to_thread(upgrade, self.config.database_url)
                    migrated = True
                redis = Redis.from_url(self.config.redis_url, decode_responses=True,
                                       socket_connect_timeout=3, socket_timeout=5)
                try:
                    async with await psycopg.AsyncConnection.connect(
                        self.config.database_url, autocommit=True, row_factory=dict_row,
                        connect_timeout=5) as connection:
                        cursor = await connection.execute('SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired',
                                                          (f'bsb.pipeline.{self.config.source}',))
                        if not (await cursor.fetchone())['acquired']:
                            raise RuntimeError('another pipeline already owns this source')
                        store = Store(connection, self.config)
                        while True:
                            await self.step(redis, store)
                finally:
                    await redis.aclose()
            except asyncio.CancelledError:
                self.ready = False
                raise
            except Exception as error:
                self.ready = False
                self.caught_up = False
                self.last_error = type(error).__name__
                logger.exception('pipeline failed; retrying from durable cursor')
                await asyncio.sleep(3)
