import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from bsb_service.pipeline.app import create_app, json_values
from bsb_service.pipeline.config import Config
from bsb_service.pipeline.validation import (
    InvalidEvent,
    bucket_start,
    stream_id,
    validate,
)

AT = datetime(2026, 11, 8, 0, 0, tzinfo=UTC)


def trade(**changes):
    return {'schema_version':'1', 'event_id':'mock:t1', 'event_type':'market.trade',
            'source':'mock', 'symbol':'KRW-BTC', 'exchange_at':'2026-11-08T00:00:00Z',
            'received_at':'2026-11-08T00:00:00.100000Z', 'price':'123.123456789123456789',
            'quantity':'0.00000001', 'side':'UNKNOWN', 'stream_type':'REALTIME', 'connection_id':'one',
            **changes}


@pytest.mark.parametrize('tf,minute,expected', [(60,4,4),(300,4,0),(300,5,5),(300,59,55)])
def test_utc_buckets(tf, minute, expected):
    moment = AT.replace(minute=minute, second=59, microsecond=999999)
    assert bucket_start(moment, tf) == AT.replace(minute=expected)
    assert bucket_start(moment + timedelta(microseconds=1), 60).second == 0


def test_decimal_and_serialization_do_not_use_float():
    event = validate(json.dumps(trade()), Config(), AT + timedelta(seconds=1))
    assert event.flags == []
    assert json_values({'price':Decimal(event.payload['price']), 'at':AT}) == {
        'price':'123.123456789123456789', 'at':'2026-11-08T00:00:00Z'}


@pytest.mark.parametrize('changes', [{'price':0.1}, {'price':'NaN'}, {'quantity':'0'},
                                   {'price':'1' * 65}, {'source':'bithumb_public'},
                                   {'exchange_at':'2026-11-08T09:00:00+09:00'},
                                   {'received_at':'2026-11-08T00:10:00Z'},
                                   {'event_type':'paper.order'}])
def test_invalid_events_are_rejected(changes):
    with pytest.raises(InvalidEvent):
        validate(json.dumps(trade(**changes)), Config(), AT + timedelta(seconds=1))


def test_quality_flags_and_stable_business_hash():
    first = validate(json.dumps(trade()), Config(), AT + timedelta(minutes=1))
    replay = validate(json.dumps(trade(connection_id='second', received_at='2026-11-08T00:00:20Z')), Config(), AT + timedelta(minutes=1))
    assert first.digest == replay.digest
    assert replay.flags == ['delayed']
    skew = validate(json.dumps(trade(exchange_at='2026-11-08T00:00:03Z')), Config(), AT + timedelta(seconds=4))
    assert skew.flags == ['clock_skew']


def test_stream_ids_are_numeric_and_validated():
    assert stream_id('9-2') < stream_id('10-0')
    for item in ('$', '1', '-1-2', '1-x'):
        with pytest.raises(InvalidEvent):
            stream_id(item)


@pytest.mark.parametrize('kwargs', [{'source':'live'}, {'batch_size':0}, {'lateness_seconds':-1}, {'heartbeat_timeout':2}])
def test_configuration_bounds(kwargs):
    with pytest.raises(ValueError):
        Config(**kwargs)


def test_control_metadata_is_validated_before_sql():
    event = trade(event_type='collector.status', shards=[{'symbols':['KRW-BTC'],'connected':True,'connection_id':'c'}], invalid_messages=None)
    with pytest.raises(InvalidEvent):
        validate(json.dumps(event), Config(), AT + timedelta(seconds=1))


def test_candle_http_query_coercion_and_bounds(monkeypatch):
    monkeypatch.setenv('DATABASE_URL','postgresql://unused')
    monkeypatch.setenv('REDIS_URL','redis://unused')
    class Connection:
        async def __aenter__(self):
            return self
        async def __aexit__(self,*args):
            pass
        async def execute(self,sql,params):
            assert params[2] in (60,300)
            return self
        async def fetchall(self):
            return []
    with patch('bsb_service.pipeline.app.Pipeline.run',AsyncMock()), patch(
        'bsb_service.pipeline.app.psycopg.AsyncConnection.connect',AsyncMock(return_value=Connection())):
        with TestClient(create_app(Config())) as client:
            for tf in (60,300):
                result = client.get(f'/pipeline/candles/KRW-BTC?timeframe={tf}')
                assert result.status_code == 200
                assert result.json()['source'] == 'mock'
            assert client.get('/pipeline/candles/KRW-BTC?timeframe=120').status_code == 422
            assert client.get('/pipeline/candles/KRW-BTC?limit=1001').status_code == 422
            assert client.get('/pipeline/candles/INVALID').status_code == 400
