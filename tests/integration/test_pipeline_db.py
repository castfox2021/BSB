"""Real PostgreSQL + Redis tests. Only a dedicated bsb_test_* database is allowed."""
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
import pytest
from bsb_service.collector.config import Config as CollectorConfig
from bsb_service.collector.runner import RedisSink
from bsb_service.pipeline.app import json_values
from bsb_service.pipeline.config import Config
from bsb_service.pipeline.migrate import upgrade
from bsb_service.pipeline.runner import Pipeline
from bsb_service.pipeline.store import Store
from jsonschema import Draft202012Validator, FormatChecker
from psycopg.rows import dict_row
from redis.asyncio import Redis

URL = os.getenv('TEST_DATABASE_URL', '')
REDIS_URL = os.getenv('TEST_REDIS_URL', 'redis://redis:6379/15')
pytestmark = pytest.mark.skipif(not URL, reason='set a dedicated TEST_DATABASE_URL')
BASE = datetime(2026, 11, 8, tzinfo=UTC)
TABLES = 'stream_coverage,data_quality_events,pipeline_rejections,pipeline_offsets,candles,market_tickers,orderbook_snapshots,market_trades,market_events,markets'


def iso(at):
    return at.isoformat().replace('+00:00','Z')


def event(i, seconds=1, price='100', quantity='1', **changes):
    at = BASE + timedelta(seconds=seconds)
    return {'schema_version':'1','event_id':f'mock:{i}','event_type':'market.trade',
            'source':'mock','symbol':'KRW-BTC','exchange_at':iso(at),
            'received_at':iso(at + timedelta(milliseconds=100)), 'price':price,'quantity':quantity,
            'side':'UNKNOWN','connection_id':'c1','stream_type':'REALTIME', **changes}


def status(i, seconds, connected=True, connection='c1', invalid=0):
    at = iso(BASE + timedelta(seconds=seconds))
    return {'schema_version':'1','event_id':f'status:{i}','event_type':'collector.status',
            'source':'mock','exchange_at':at,'received_at':at,'invalid_messages':invalid,
            'shards':[{'symbols':['KRW-BTC'],'connection_id':connection,'connected':connected}]}


def rows(events, offset=1):
    return [(f'{offset+i}-0', {'payload':json.dumps(item)}) for i,item in enumerate(events)]


@pytest.fixture(scope='module', autouse=True)
def migrated():
    if not URL:
        return
    assert urlparse(URL).path.startswith('/bsb_test_'), 'refusing to migrate a non-test database'
    upgrade(URL)
    upgrade(URL)


@pytest.fixture(autouse=True)
def empty_db():
    if URL:
        assert urlparse(URL).path.startswith('/bsb_test_')
        with psycopg.connect(URL, autocommit=True) as connection:
            connection.execute(f'TRUNCATE {TABLES}')


async def connection():
    return await psycopg.AsyncConnection.connect(URL, autocommit=True, row_factory=dict_row)


async def all_candles(conn):
    result = await conn.execute('SELECT * FROM candles ORDER BY timeframe,bucket_start')
    return await result.fetchall()


def test_exact_ohlcv_duplicate_and_restart_recovery():
    async def check():
        inputs = [event(1,1,'100.123456789123456789','0.00000001'), event(2,30,'103','2'),
                  event(3,10,'99','3'), event(4,60,'105','4')]
        async with await connection() as conn:
            store = Store(conn, Config(database_url=URL, lateness_seconds=5))
            assert (await store.apply(rows(inputs[:2]), BASE+timedelta(minutes=6)))['accepted'] == 2
        async with await connection() as conn:
            store = Store(conn, Config(database_url=URL, lateness_seconds=5))
            assert await store.offset() == '2-0'
            result = await store.apply(rows(inputs[2:],3), BASE+timedelta(minutes=6))
            assert result['last_stream_id'] == '4-0'
            before = await all_candles(conn)
            schema = json.loads((Path(__file__).resolve().parents[2] / 'packages/contracts/candle.schema.json').read_text())
            for candle in before:
                Draft202012Validator(schema, format_checker=FormatChecker()).validate(json_values(candle))
            replay = await store.apply(rows(inputs,5), BASE+timedelta(minutes=6))
            assert replay['duplicates'] == 4
            assert await all_candles(conn) == before
            one, two, five = before
            assert (one['open'],one['high'],one['low'],one['close']) == (Decimal('100.123456789123456789'),Decimal('103'),Decimal('99'),Decimal('103'))
            assert one['volume'] == Decimal('5.00000001')
            assert one['turnover'] == Decimal('503.00000100123456789123456789')
            assert two['bucket_start'] == BASE+timedelta(minutes=1)
            assert five['volume'] == Decimal('9.00000001')
            cursor = await conn.execute("SELECT count(*) AS n FROM data_quality_events WHERE code='out_of_order'")
            assert (await cursor.fetchone())['n'] == 1
    asyncio.run(check())


def test_transaction_failure_rolls_back_event_candle_and_cursor():
    async def check():
        async with await connection() as conn:
            store = Store(conn, Config(database_url=URL, lateness_seconds=5))
            original = store.project
            async def fail(item, now):
                if item.payload['event_id'] == 'mock:2':
                    raise RuntimeError('injected crash after second event insert')
                await original(item, now)
            with patch.object(store, 'project', fail):
                with pytest.raises(RuntimeError):
                    await store.apply(rows([event(1), event(2)]), BASE+timedelta(minutes=1))
            cursor = await conn.execute('SELECT count(*) AS n FROM market_events')
            assert (await cursor.fetchone())['n'] == 0
            assert await store.offset() == '0-0'
            assert (await store.apply(rows([event(1), event(2)]), BASE+timedelta(minutes=1)))['accepted'] == 2
    asyncio.run(check())


def test_closed_and_complete_require_continuous_coverage():
    async def check():
        async with await connection() as conn:
            store = Store(conn, Config(database_url=URL, lateness_seconds=5))
            inputs = [status('before',-1), event(1,1)] + [status(i,i) for i in range(9,70,10)]
            await store.apply(rows(inputs), BASE+timedelta(seconds=70))
            assert (await all_candles(conn))[0]['closed'] is False
            await store.finalize(BASE+timedelta(seconds=70))
            one,five = await all_candles(conn)
            assert one['closed'] and one['complete']
            assert five['closed'] is False
            await store.apply(rows([event(2,2,'101')],100), BASE+timedelta(seconds=75))
            one = (await all_candles(conn))[0]
            assert one['closed'] and not one['complete'] and one['revised_after_close']
            assert 'late_correction' in one['quality_flags']
    asyncio.run(check())


def test_coverage_gap_and_empty_minutes_are_not_fabricated():
    async def check():
        async with await connection() as conn:
            store = Store(conn, Config(database_url=URL, lateness_seconds=5))
            inputs = [status(0,-1), event(1,1), status(1,10,False), status(2,30,connection='c2'),event(2,121)]
            await store.apply(rows(inputs), BASE+timedelta(minutes=6))
            await store.finalize(BASE+timedelta(minutes=6))
            candles = await all_candles(conn)
            assert len([c for c in candles if c['timeframe']==60]) == 2
            assert not any(c['complete'] for c in candles)
            cursor = await conn.execute("SELECT count(*) AS n FROM data_quality_events WHERE code='connection_gap'")
            assert (await cursor.fetchone())['n'] >= 1
    asyncio.run(check())


def test_invalid_poison_conflict_snapshot_and_source_isolation():
    async def check():
        async with await connection() as conn:
            store = Store(conn, Config(database_url=URL, lateness_seconds=5))
            inputs = rows([event(1,stream_type='SNAPSHOT'), event(2), event(2,price='1000'), event(3,source='bithumb_public')])
            inputs.append(('5-0', {'payload':'{invalid'}))
            result = await store.apply(inputs, BASE+timedelta(minutes=1))
            assert result == {'accepted':2,'duplicates':1,'rejected':2,'last_stream_id':'5-0'}
            assert (await all_candles(conn))[0]['volume'] == Decimal('1')
            cursor = await conn.execute('SELECT code FROM data_quality_events')
            assert 'event_id_conflict' in [row['code'] for row in await cursor.fetchall()]
            cursor = await conn.execute('SELECT count(*) AS n FROM pipeline_rejections')
            assert (await cursor.fetchone())['n'] == 3
    asyncio.run(check())


def test_same_timestamp_has_deterministic_open_close():
    async def replay(order):
        async with await connection() as conn:
            store = Store(conn, Config(database_url=URL, lateness_seconds=5))
            await store.apply(rows(order), BASE+timedelta(minutes=1))
            value = (await all_candles(conn))[0]
            await conn.execute(f'TRUNCATE {TABLES}')
            return {k:value[k] for k in ('open','high','low','close','volume','turnover','first_event_id','last_event_id')}
    async def check():
        inputs = [event(1,1,'105'),event(2,1,'99'),event(3,1,'101')]
        assert await replay(inputs) == await replay(list(reversed(inputs)))
    asyncio.run(check())


def test_real_redis_durable_cursor_trim_and_stream_reset():
    async def check():
        redis = Redis.from_url(REDIS_URL, decode_responses=True)
        config = Config(database_url=URL,redis_url=REDIS_URL)
        key = f'bsb:mock:test:{uuid4()}'
        # Override only the stream property using a test-specific subclass; never touch production keys.
        class TestConfig(Config):
            @property
            def stream(self):
                return key
        config = TestConfig(database_url=URL,redis_url=REDIS_URL)
        worker = Pipeline(config)
        try:
            await redis.xadd(key, {'payload':json.dumps(event(1))}, id='1000-0')
            async with await connection() as conn:
                store = Store(conn, config)
                await worker.step(redis, store, block=1)
                assert await store.offset() == '1000-0'
            await redis.xadd(key, {'payload':json.dumps(event(2))}, id='2000-0')
            await redis.xadd(key, {'payload':json.dumps(event(3))}, id='3000-0')
            await redis.xtrim(key, maxlen=1, approximate=False)
            async with await connection() as conn:
                store = Store(conn, config)
                await Pipeline(config).step(redis,store,block=1)
                assert await store.offset() == '3000-0'
                codes = await conn.execute('SELECT code FROM data_quality_events')
                assert 'stream_retention_gap' in [r['code'] for r in await codes.fetchall()]
            await redis.delete(key)
            await redis.xadd(key, {'payload':json.dumps(event(4))}, id='500-0')
            async with await connection() as conn:
                store = Store(conn,config)
                await Pipeline(config).step(redis,store,block=1)
                assert await store.offset() == '500-0'
                codes = await conn.execute('SELECT code FROM data_quality_events')
                assert 'stream_reset' in [r['code'] for r in await codes.fetchall()]
        finally:
            await redis.delete(key)
            await redis.aclose()
    asyncio.run(check())


def test_observed_gap_invalidates_closed_candle_revision():
    async def check():
        async with await connection() as conn:
            store = Store(conn, Config(database_url=URL,lateness_seconds=5))
            initial = [status('begin',-1),event(1)] + [status(i,i) for i in range(9,70,10)]
            await store.apply(rows(initial), BASE+timedelta(seconds=70))
            await store.finalize(BASE+timedelta(seconds=70))
            before = (await all_candles(conn))[0]
            assert before['complete']
            disconnected = status('lost',90,False)
            disconnected['shards'][0]['last_data_at'] = iso(BASE+timedelta(seconds=50))
            await store.apply(rows([disconnected],100), BASE+timedelta(seconds=90))
            after = (await all_candles(conn))[0]
            assert after['closed'] and not after['complete']
            assert after['revision'] > before['revision']
            assert 'connection_gap' in after['quality_flags']
    asyncio.run(check())


def test_grace_period_and_incomplete_start_window():
    async def check():
        async with await connection() as conn:
            store = Store(conn,Config(database_url=URL))
            inputs = [status('begin',2),event(1,3)] + [status(i,i) for i in range(12,103,10)]
            await store.apply(rows(inputs),BASE+timedelta(seconds=105))
            assert await store.finalize(BASE+timedelta(seconds=99)) == 0
            await store.finalize(BASE+timedelta(seconds=105))
            candle = (await all_candles(conn))[0]
            assert candle['closed'] and not candle['complete']
    asyncio.run(check())


def test_latest_cache_does_not_regress_on_out_of_order_delivery():
    async def check():
        redis = Redis.from_url(REDIS_URL, decode_responses=True)
        source = f'test-{uuid4()}'
        latest_key = f'bsb:{source}:latest:KRW-BTC:market.trade'
        stream_key = f'bsb:{source}:events'
        try:
            sink = RedisSink(redis,CollectorConfig())
            newest = event(2,20,source=source)
            oldest = event(1,10,source=source)
            # Collector timestamps have fixed microsecond precision for lexical CAS.
            for item in (newest,oldest):
                item['exchange_at'] = datetime.fromisoformat(item['exchange_at']).isoformat(timespec='microseconds').replace('+00:00','Z')
            await sink.publish(newest)
            await sink.publish(oldest)
            assert json.loads(await redis.get(latest_key))['event_id'] == newest['event_id']
            assert await redis.xlen(stream_key) == 2
            await redis.set(latest_key,'42')
            await sink.publish(newest)
            assert json.loads(await redis.get(latest_key))['event_id'] == newest['event_id']
        finally:
            await redis.delete(latest_key,stream_key)
            await redis.aclose()
    asyncio.run(check())


def test_client_heartbeats_without_fresh_exchange_delivery_are_not_complete():
    async def check():
        async with await connection() as conn:
            store = Store(conn,Config(database_url=URL))
            controls = [status(i,i) for i in range(-1,110,10)]
            for item in controls:
                item['shards'][0]['last_data_at'] = iso(BASE+timedelta(seconds=-1))
            await store.apply(rows([controls[0],event(1),*controls[1:]]),BASE+timedelta(seconds=110))
            await store.finalize(BASE+timedelta(seconds=110))
            candle = (await all_candles(conn))[0]
            assert candle['closed'] and not candle['complete']
    asyncio.run(check())
