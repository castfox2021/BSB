import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .config import ROOT, Config

VALIDATORS = {
    f'market.{kind}': Draft202012Validator(
        json.loads((ROOT / f'packages/contracts/market-{kind}.schema.json').read_text()),
        format_checker=FormatChecker()) for kind in ('trade', 'ticker', 'orderbook')
}


CONTROL_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / 'packages/contracts/collector-control.schema.json').read_text()),
    format_checker=FormatChecker())


class InvalidEvent(ValueError):
    pass


def parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith('Z'):
        raise InvalidEvent('UTC Z timestamp required')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise InvalidEvent('invalid UTC timestamp') from error
    if result.year < 2020:
        raise InvalidEvent('timestamp before supported history')
    return result


def stream_id(value: str) -> tuple[int, int]:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+-[0-9]+', value):
        raise InvalidEvent('invalid Redis stream ID')
    a, b = value.split('-')
    return int(a), int(b)


def bucket_start(at: datetime, timeframe: int) -> datetime:
    if timeframe not in (60, 300) or at.tzinfo is None:
        raise ValueError('supported timeframe and aware timestamp required')
    utc = at.astimezone(UTC)
    return utc.replace(minute=utc.minute - utc.minute % (timeframe // 60), second=0, microsecond=0)


def exact_decimal(value: str, zero: bool = False) -> Decimal:
    number = Decimal(value)
    if not number.is_finite() or number < 0 or (number == 0 and not zero):
        raise InvalidEvent('non-positive numeric field')
    exponent = number.as_tuple().exponent
    if not isinstance(exponent, int) or len(number.as_tuple().digits) > 64 or abs(exponent) > 32:
        raise InvalidEvent('numeric precision exceeds supported input bounds')
    return number


@dataclass
class Event:
    payload: dict
    at: datetime
    received: datetime
    flags: list[str]
    digest: str


def validate(raw: str, config: Config, now: datetime) -> Event:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('source') != config.source:
            raise InvalidEvent('source mismatch or non-object event')
        if data.get('schema_version') != '1' or not isinstance(data.get('event_id'), str) or not 1 <= len(data['event_id']) <= 512:
            raise InvalidEvent('invalid event envelope')
        kind = data.get('event_type')
        at, received = parse_time(data.get('exchange_at')), parse_time(data.get('received_at'))
        if received > now + timedelta(seconds=60) or at > now + timedelta(seconds=60):
            raise InvalidEvent('event is in the future relative to processor clock')
        if kind in VALIDATORS:
            VALIDATORS[kind].validate(data)
            if kind == 'market.trade':
                exact_decimal(data['price'])
                exact_decimal(data['quantity'])
            elif kind == 'market.ticker':
                exact_decimal(data['price'])
                exact_decimal(data['volume_24h'], zero=True)
                exact_decimal(data['turnover_24h'], zero=True)
            else:
                for side in ('bids', 'asks'):
                    levels = data[side]
                    prices = [exact_decimal(p) for p, q in levels]
                    for p, q in levels:
                        exact_decimal(q, zero=True)
                    if len(set(prices)) != len(prices):
                        raise InvalidEvent('duplicate book levels')
                if max(Decimal(x[0]) for x in data['bids']) >= min(Decimal(x[0]) for x in data['asks']):
                    raise InvalidEvent('crossed book')
        elif kind == 'collector.status':
            CONTROL_VALIDATOR.validate(data)
            count = data.get('invalid_messages', 0)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise InvalidEvent('invalid frame counter')
            if not isinstance(data.get('shards'), list) or len(data['shards']) > 1000:
                raise InvalidEvent('invalid collector status')
            symbols = set()
            for shard in data['shards']:
                if (not isinstance(shard.get('connected'), bool)
                        or not isinstance(shard.get('connection_id'), str)
                        or not isinstance(shard.get('symbols'), list)):
                    raise InvalidEvent('invalid shard status')
                if shard.get('last_data_at') is not None:
                    parse_time(shard['last_data_at'])
                for symbol in shard['symbols']:
                    if not isinstance(symbol, str) or not re.fullmatch(r'KRW-[A-Z0-9]+', symbol) or symbol in symbols:
                        raise InvalidEvent('invalid or duplicate coverage symbol')
                    symbols.add(symbol)
        elif kind == 'collector.universe':
            CONTROL_VALIDATOR.validate(data)
            if not isinstance(data.get('markets'), list) or not 1 <= len(data['markets']) <= 10000:
                raise InvalidEvent('invalid universe')
            seen_markets = set()
            for market in data['markets']:
                if not isinstance(market.get('symbol'), str) or not re.fullmatch(r'KRW-[A-Z0-9]+', market['symbol']):
                    raise InvalidEvent('invalid universe symbol')
                if market['symbol'] in seen_markets or any(not isinstance(market.get(k, ''), str) for k in ('korean_name','english_name','market_warning')):
                    raise InvalidEvent('duplicate or invalid universe metadata')
                seen_markets.add(market['symbol'])
        else:
            raise InvalidEvent('unsupported event type')
        lag = (received - at).total_seconds()
        flags = []
        if kind.startswith('market.'):
            if lag < -1:
                flags.append('clock_skew')
            if lag > config.max_delay_seconds:
                flags.append('delayed')
            if not isinstance(data.get('connection_id'), str) or not data['connection_id']:
                flags.append('untracked_connection')
            if data.get('stream_type', 'UNKNOWN') != 'REALTIME':
                flags.append('non_realtime')
        business = {k: v for k, v in data.items() if k not in ('received_at', 'connection_id', 'stream_type')}
        digest = hashlib.sha256(json.dumps(business, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return Event(data, at, received, flags, digest)
    except InvalidEvent:
        raise
    except Exception as error:
        raise InvalidEvent(type(error).__name__) from error
