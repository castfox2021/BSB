import asyncio
import contextlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from bsb_service.collector.app import create_app
from bsb_service.collector.config import STREAM_TYPES, Config
from bsb_service.collector.normalize import InvalidMessage, normalize
from bsb_service.collector.rest import fetch_markets, retry_delay
from bsb_service.collector.runner import Collector, RedisSink, subscription
from bsb_service.collector.state import Shard, State

ROOT = Path(__file__).resolve().parents[3]
RAW = json.loads((Path(__file__).parent / 'fixtures/public-ws.json').read_text(), parse_float=Decimal)
RECEIVED = datetime(2026, 11, 8, 0, 0, 1, tzinfo=UTC)


def frame(kind, **changes):
    # Preserve exact decimal tokens in JSON fixtures (no float round-trip).
    data = {**RAW[kind], **changes}
    return json.dumps(data, default=str)


@pytest.mark.parametrize('kind', STREAM_TYPES)
def test_normalized_contract(kind):
    event = normalize(frame(kind), RECEIVED, 'connection-1')
    schema = json.loads((ROOT / f'packages/contracts/market-{kind}.schema.json').read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(event)
    assert event['source'] == 'bithumb_public'
    assert event['received_at'] == '2026-11-08T00:00:01.000000Z'
    if kind == 'trade':
        assert event['price'] == '123456.12345678'
        assert event['quantity'] == '0.00000001'
        assert event['side'] == 'BUY'
    elif kind == 'orderbook':
        assert event['exchange_at'] == '2026-11-08T00:00:00.123456Z'
        assert event['exchange_timestamp_unit'] == 'microseconds'


def test_raw_json_decimal_tokens_and_unknown_side():
    raw = '{"type":"trade","code":"KRW-BTC","trade_price":0.123456789123456789,"trade_volume":0.00000001,"trade_timestamp":1794096000123,"sequential_id":1}'
    event = normalize(raw, RECEIVED, 'c')
    assert event['price'] == '0.123456789123456789'
    assert event['side'] == 'UNKNOWN'
    assert normalize(frame('trade', ask_bid='ASK'), RECEIVED, 'c')['side'] == 'SELL'


@pytest.mark.parametrize('raw', ['{}', '[]', '{', '{"error":{"name":"INVALID_CODE"}}',
                                '{"type":"trade","trade_price":NaN}',
                                frame('trade', trade_price='-1'),
                                frame('trade', sequential_id=None),
                                frame('trade', trade_timestamp=1794096000123000),
                                frame('trade', code='BTC-ETH'),
                                frame('orderbook', orderbook_units=[]),
                                frame('orderbook', orderbook_units=[{'ask_price':1,'bid_price':2,'ask_size':1,'bid_size':1}])])
def test_reject_bad_frames(raw):
    with pytest.raises(InvalidMessage):
        normalize(raw, RECEIVED, 'c')


def test_book_millisecond_compatibility_and_heartbeat():
    event = normalize(frame('orderbook', timestamp=1794096000123), RECEIVED, 'c')
    assert event['exchange_timestamp_unit'] == 'milliseconds'
    assert normalize('{"status":"UP"}', RECEIVED, 'c') is None


@pytest.mark.parametrize('kwargs', [{'source':'live'}, {'batch_size':101}, {'refresh_seconds':1}, {'stale_seconds':0}])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        Config(**kwargs)


def test_rest_krw_filter_and_rate_limit():
    async def check():
        def handler(request):
            assert request.url.path == '/v1/market/all'
            assert request.url.params['isDetails'] == 'true'
            assert 'authorization' not in request.headers
            return httpx.Response(200, json=[{'market':'BTC-ETH'}, {'market':'KRW-ETH'}, {'market':'KRW-BTC'}])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert [m['symbol'] for m in await fetch_markets(client)] == ['KRW-BTC', 'KRW-ETH']
        response = httpx.Response(429, request=httpx.Request('GET', 'https://api.bithumb.com/v1/market/all'), headers={'Retry-After':'120'})
        error = httpx.HTTPStatusError('limited', request=response.request, response=response)
        assert retry_delay(error, 1) == 120
        assert retry_delay(RuntimeError(), 50) == 60
    asyncio.run(check())


@pytest.mark.parametrize('payload', [[], {}, [{'market':'INVALID'}], [{'market':'KRW-'}]])
def test_invalid_universe_is_not_accepted(payload):
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
            with pytest.raises(ValueError):
                await fetch_markets(client)
    asyncio.run(check())


def test_stale_snapshot_reconnect_guards():
    clock = [100.]
    state = State(Config(source='bithumb_public'), clock=lambda: clock[0])
    state.refreshed_mono = 100
    shard = Shard(['KRW-BTC'])
    state.shards = [shard]
    state.reset(shard, 'first')
    for kind in STREAM_TYPES:
        state.acknowledge(shard, normalize(frame(kind), RECEIVED, 'first'))
    assert state.status()['fresh_symbols'] == ['KRW-BTC']
    assert state.status()['entries_allowed'] is False
    clock[0] += 16
    assert state.status()['fresh_symbols'] == []
    state.reset(shard, 'second')
    assert shard.seen == {}
    for kind in STREAM_TYPES:
        state.acknowledge(shard, normalize(frame(kind, stream_type='SNAPSHOT'), RECEIVED, 'second'))
    assert state.status()['fresh_symbols'] == []
    state.disconnect(shard)
    assert state.status()['transport_ready'] is False


def test_redis_delivery_is_transactional_and_bounded():
    async def check():
        class Pipeline:
            def __init__(self):
                self.commands = []
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def xadd(self, *args, **kwargs):
                self.commands.append(('xadd', args, kwargs))
            def eval(self, *args, **kwargs):
                self.commands.append(('eval', args, kwargs))
            async def execute(self):
                assert len(self.commands) == 2
        class Redis:
            def pipeline(self, transaction):
                assert transaction is True
                return pipe
        pipe = Pipeline()
        await RedisSink(Redis(), Config()).publish(normalize(frame('trade'), RECEIVED, 'c'))
        assert pipe.commands[0][2] == {'maxlen':100000,'approximate':False}
        assert pipe.commands[1][0] == 'eval'
        assert pipe.commands[1][1][-1] == '15'
    asyncio.run(check())


def test_local_websocket_reconnect_resubscribe_deduplicate_and_cancel():
    async def check():
        reconnected, resume, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        sessions = []
        output = []
        class Gate:
            async def wait(self):
                pass
        class Sink:
            async def publish(self, event):
                output.append(event)
                if len(output) == 4:
                    delivered.set()
        async def handler(ws):
            request = json.loads(await ws.recv())
            sessions.append(request)
            assert [r['type'] for r in request if 'type' in r] == list(STREAM_TYPES)
            assert all(r['codes'] == ['KRW-BTC'] for r in request if 'codes' in r)
            if len(sessions) == 1:
                await ws.send(frame('trade'))
                await ws.close()
            else:
                reconnected.set()
                await resume.wait()
                for kind in ['trade', 'ticker', 'orderbook']:
                    await ws.send(frame(kind))
                await ws.send(frame('trade', sequential_id=2))
                await ws.wait_closed()
        async with serve(handler, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            def connector(url, **kwargs):
                assert url.startswith('wss://ws-api.bithumb.com/')
                return connect(f'ws://127.0.0.1:{port}', **kwargs)
            collector = Collector(Config(source='bithumb_public'), Sink(), connector=connector, gate=Gate())
            shard = Shard(['KRW-BTC'])
            collector.state.shards = [shard]
            task = asyncio.create_task(collector.run_shard(shard))
            try:
                await asyncio.wait_for(reconnected.wait(), 8)
                assert shard.seen == {}
                assert collector.state.status()['entries_allowed'] is False
                resume.set()
                await asyncio.wait_for(delivered.wait(), 5)
                assert len(output) == 4
                assert collector.state.counts['duplicates'] == 1
                assert shard.reconnects == 1
                assert sessions[0][0]['ticket'] != sessions[1][0]['ticket']
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            assert not shard.connected
    asyncio.run(check())


def test_subscription_and_mock_api(monkeypatch):
    assert subscription(['KRW-BTC'], 't')[-1] == {'format':'DEFAULT'}
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused')
    monkeypatch.setenv('REDIS_URL', 'redis://unused')
    with patch('bsb_service.collector.app.check_dependencies', AsyncMock()):
        with TestClient(create_app(Config())) as client:
            assert client.get('/health').json()['dataSource'] == 'mock'
            status = client.get('/collector/status').json()
            assert status['running'] is False
            assert status['entries_allowed'] is False
            assert client.get('/collector/markets').json()['markets'] == []
            assert client.get('/collector/latest/KRW-BTC').status_code == 404


def test_sink_failure_does_not_acknowledge_or_deduplicate():
    async def check():
        failed = asyncio.Event()
        class Sink:
            async def publish(self, event):
                failed.set()
                raise RuntimeError('redis unavailable')
        class Gate:
            async def wait(self):
                pass
        class Socket:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def send(self, data):
                pass
            async def recv(self):
                return frame('trade')
        collector = Collector(Config(source='bithumb_public'), Sink(), connector=lambda *a, **k: Socket(), gate=Gate())
        shard = Shard(['KRW-BTC'])
        task = asyncio.create_task(collector.run_shard(shard))
        try:
            await asyncio.wait_for(failed.wait(), 2)
            assert not collector.state.recent
            assert collector.state.counts['market.trade'] == 0
            assert not shard.connected
            assert shard.reconnects == 1
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    asyncio.run(check())


def test_live_transport_is_required_for_health(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused')
    monkeypatch.setenv('REDIS_URL', 'redis://unused')
    with patch('bsb_service.collector.app.check_dependencies', AsyncMock()):
        with TestClient(create_app(Config())) as client:
            collector = client.app.state.collector
            collector.config = Config(source='bithumb_public')
            collector.state.config = collector.config
            response = client.get('/health')
            assert response.status_code == 503
            assert response.json()['dataSource'] == 'bithumb_public'
            assert response.json()['collector']['transport_ready'] is False


def test_clock_skew_and_delay_are_distinct_and_never_fresh():
    state = State(Config(source='bithumb_public'))
    shard = Shard(['KRW-BTC'])
    state.shards = [shard]
    state.reset(shard, 'c')
    state.acknowledge(shard, normalize(frame('trade'), datetime(2026, 11, 7, 23, 59, 58, tzinfo=UTC), 'c'))
    assert state.status()['clock_skew_detected'] is True
    assert state.counts['clock_skew_events'] == 1
    assert state.status()['fresh_symbols'] == []
    state.acknowledge(shard, normalize(frame('trade'), datetime(2026, 11, 8, 0, 1, tzinfo=UTC), 'c'))
    assert state.counts['stale_events'] == 1
    assert state.status()['clock_skew_detected'] is False
