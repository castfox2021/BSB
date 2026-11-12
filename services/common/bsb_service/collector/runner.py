import asyncio
import contextlib
import json
import logging
import random
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
from redis.asyncio import Redis
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from .config import STREAM_TYPES, WS_URL, Config
from .normalize import InvalidMessage, SubscriptionError, normalize, utc_iso
from .rest import fetch_markets, retry_delay
from .state import Shard, State

logger = logging.getLogger('bsb.collector')

LATEST_CAS = '''
local previous = redis.call('GET', KEYS[1])
if previous then
  local ok, value = pcall(cjson.decode, previous)
  if ok and type(value) == 'table' and type(value.exchange_at) == 'string' and value.exchange_at > ARGV[2] then return 0 end
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
return 1
'''


class ConnectionGate:
    """Serialize connection attempts to <= 1/sec across every shard in this process."""
    def __init__(self):
        self.lock = asyncio.Lock()
        self.next_at = 0.

    async def wait(self):
        async with self.lock:
            await asyncio.sleep(max(0, self.next_at - time.monotonic()))
            self.next_at = time.monotonic() + 1

    async def block(self, seconds: float):
        async with self.lock:
            self.next_at = max(self.next_at, time.monotonic() + seconds)


def subscription(symbols: list[str], ticket: str) -> list[dict]:
    return [{'ticket': ticket}, *[{'type': kind, 'codes': symbols} for kind in STREAM_TYPES],
            {'format': 'DEFAULT'}]


class RedisSink:
    def __init__(self, redis: Redis, config: Config):
        self.redis = redis
        self.config = config

    async def publish(self, event: dict):
        payload = json.dumps(event, separators=(',', ':'))
        prefix = f'bsb:{event["source"]}'
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.xadd(f'{prefix}:events', {'payload': payload}, maxlen=self.config.stream_maxlen,
                      approximate=False)
            pipe.eval(LATEST_CAS, 1, f'{prefix}:latest:{event["symbol"]}:{event["event_type"]}',
                      payload, event['exchange_at'], str(self.config.stale_seconds))
            await pipe.execute()

    async def control(self, kind: str, **fields):
        at = utc_iso(datetime.now(UTC))
        event = {'schema_version':'1', 'event_id':f'{kind}:{uuid4()}', 'event_type':kind,
                 'source':'bithumb_public', 'exchange_at':at, 'received_at':at, **fields}
        await self.redis.xadd('bsb:bithumb_public:events', {'payload':json.dumps(event)},
                              maxlen=self.config.stream_maxlen, approximate=False)

    async def markets(self, markets: list[dict]):
        at = utc_iso(datetime.now(UTC))
        event = {'schema_version':'1', 'event_id':f'collector.universe:{uuid4()}',
                 'event_type':'collector.universe', 'source':'bithumb_public',
                 'exchange_at':at, 'received_at':at, 'markets':markets}
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.set('bsb:bithumb_public:markets', json.dumps(markets, ensure_ascii=False),
                     ex=self.config.refresh_seconds * 2)
            pipe.xadd('bsb:bithumb_public:events', {'payload':json.dumps(event)},
                      maxlen=self.config.stream_maxlen, approximate=False)
            await pipe.execute()


class Collector:
    def __init__(self, config: Config, sink: Any, *, client: Any = None,
                 connector: Any = connect, gate: Any = None):
        self.config = config
        self.state = State(config)
        self.sink = sink
        self.client = client
        self.connector = connector
        self.gate = gate or ConnectionGate()
        self.workers: list[asyncio.Task] = []

    async def run_shard(self, shard: Shard):
        attempt = 0
        try:
            while True:
                await self.gate.wait()
                started = self.state.clock()
                try:
                    async with self.connector(WS_URL, open_timeout=10, close_timeout=5,
                                              ping_interval=20, ping_timeout=20,
                                              max_queue=64, max_size=2**20) as ws:
                        connection_id = str(uuid4())
                        self.state.reset(shard, connection_id)
                        await ws.send(json.dumps(subscription(shard.symbols, connection_id)))
                        while True:
                            raw = await asyncio.wait_for(ws.recv(), timeout=self.config.receive_timeout)
                            received = datetime.now(UTC)
                            try:
                                # Exchange subscription errors require reconnect/backoff, not a silent skip.
                                event = normalize(raw, received, connection_id)
                            except SubscriptionError:
                                raise
                            except InvalidMessage:
                                self.state.counts['invalid_messages'] += 1
                                shard.seen.clear()
                                continue
                            if event is None:
                                continue
                            if event['symbol'] not in shard.symbols:
                                raise InvalidMessage('received an unsubscribed symbol')
                            if event['event_id'] in self.state.recent:
                                self.state.counts['duplicates'] += 1
                                continue
                            # A failed sink write must never acknowledge a message as successfully delivered.
                            await self.sink.publish(event)
                            self.state.acknowledge(shard, event)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    shard.last_error = type(error).__name__
                    shard.reconnects += 1
                    self.state.disconnect(shard)
                    self.state.counts['disconnects'] += 1
                    if isinstance(error, InvalidStatus) and error.response.status_code in (429, 418):
                        await self.gate.block(600)
                    if self.state.clock() - started > 60:
                        attempt = 0
                    attempt += 1
                    delay = min(60, 2 ** min(attempt - 1, 6)) + random.uniform(0, 1)
                    logger.warning('stream disconnected reason=%s retry_in=%.1fs', type(error).__name__, delay)
                    await asyncio.sleep(delay)
        finally:
            self.state.disconnect(shard)

    async def replace_workers(self, symbols: list[str]):
        await self.stop_workers()
        self.state.shards = [Shard(symbols[i:i + self.config.batch_size])
                             for i in range(0, len(symbols), self.config.batch_size)]
        self.workers = [asyncio.create_task(self.run_shard(shard)) for shard in self.state.shards]

    async def stop_workers(self):
        for task in self.workers:
            task.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers = []

    async def status_once(self):
        if hasattr(self.sink, 'control'):
            await self.sink.control('collector.status',
                invalid_messages=self.state.counts['invalid_messages'],
                shards=[{'symbols':s.symbols, 'connection_id':s.connection_id,
                         'connected':s.connected and bool(s.last_message) and
                             self.state.clock() - s.last_message <= self.config.stale_seconds,
                         'last_data_at':utc_iso(datetime.now(UTC) - timedelta(
                             seconds=max(0, self.state.clock() - s.last_message))) if s.last_message else None}
                        for s in self.state.shards])

    async def status_loop(self):
        while True:
            try:
                await self.status_once()
            except Exception:
                self.state.counts['control_errors'] += 1
                logger.exception('collector coverage heartbeat failed')
            await asyncio.sleep(5)

    async def run(self):
        self.state.running = True
        monitor = asyncio.create_task(self.status_loop())
        self.client = self.client or httpx.AsyncClient(timeout=10, follow_redirects=False)
        attempt = 0
        try:
            while True:
                try:
                    markets = await fetch_markets(self.client)
                    await self.sink.markets(markets)
                    previous = [m['symbol'] for m in self.state.markets]
                    symbols = [m['symbol'] for m in markets]
                    self.state.markets = markets
                    self.state.refreshed_at = utc_iso(datetime.now(UTC))
                    self.state.refreshed_mono = self.state.clock()
                    self.state.rest_error = None
                    if symbols != previous or not self.workers or any(t.done() for t in self.workers):
                        await self.replace_workers(symbols)
                    attempt = 0
                    await asyncio.sleep(self.config.refresh_seconds)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    attempt += 1
                    self.state.rest_error = type(error).__name__
                    logger.warning('market refresh failed reason=%s', type(error).__name__)
                    await asyncio.sleep(retry_delay(error, attempt))
        finally:
            self.state.running = False
            await self.stop_workers()
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor
            try:
                await self.status_once()
            except Exception:
                logger.warning('could not publish final disconnected status')
            await self.client.aclose()

    @contextlib.asynccontextmanager
    async def start(self):
        task = asyncio.create_task(self.run()) if self.config.source == 'bithumb_public' else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
