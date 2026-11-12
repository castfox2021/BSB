import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from .config import STREAM_TYPES


class InvalidMessage(ValueError):
    pass


class SubscriptionError(InvalidMessage):
    pass


def decimal_string(value: Any, *, zero: bool = False) -> str:
    if isinstance(value, (bool, float)) or value is None:
        raise InvalidMessage('numeric fields must be decoded without floats')
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise InvalidMessage('invalid decimal') from error
    if not number.is_finite() or number < 0 or (not zero and number == 0):
        raise InvalidMessage('non-positive or non-finite decimal')
    return format(number, 'f')


def integer(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InvalidMessage('invalid integer identifier/timestamp')
    return value


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise InvalidMessage('timezone-aware receipt time required')
    return value.astimezone(UTC).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def exchange_time(value: Any, stream: str, received: datetime) -> tuple[str, str]:
    stamp = integer(value)
    # Official orderbook spec uses microseconds; deployed feeds may use milliseconds.
    unit = 'microseconds' if stream == 'orderbook' and stamp >= 100_000_000_000_000 else 'milliseconds'
    micros = stamp if unit == 'microseconds' else stamp * 1000
    try:
        moment = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=micros)
    except OverflowError as error:
        raise InvalidMessage('timestamp out of range') from error
    if moment.year < 2020 or moment > received + timedelta(seconds=60):
        raise InvalidMessage('invalid or future exchange timestamp')
    return utc_iso(moment), unit


def reject_constant(value: str):
    raise InvalidMessage(f'non-finite JSON constant: {value}')


def normalize(raw: str | bytes, received: datetime, connection_id: str) -> dict | None:
    try:
        data = json.loads(raw, parse_float=Decimal, parse_constant=reject_constant)
        if not isinstance(data, dict):
            raise InvalidMessage('expected an object')
        if 'error' in data:
            raise SubscriptionError('exchange rejected subscription')
        if data == {'status': 'UP'}:
            return None
        stream = data.get('type')
        symbol = data.get('code', '')
        if stream not in STREAM_TYPES or not isinstance(symbol, str) or not re.fullmatch(r'KRW-[A-Z0-9]+', symbol):
            raise InvalidMessage('unsupported stream or symbol')
        stamp = data['trade_timestamp'] if stream == 'trade' else data['timestamp']
        at, unit = exchange_time(stamp, stream, received)
        stream_type = data.get('stream_type', 'UNKNOWN')
        if stream_type not in ('SNAPSHOT', 'REALTIME', 'UNKNOWN'):
            raise InvalidMessage('invalid stream type')
        event: dict[str, Any] = {'schema_version': '1', 'event_type': f'market.{stream}',
                 'source': 'bithumb_public', 'symbol': symbol, 'exchange_at': at,
                 'received_at': utc_iso(received), 'connection_id': connection_id,
                 'stream_type': stream_type, 'exchange_timestamp_unit': unit,
                 'raw_exchange_timestamp': str(stamp)}
        if stream == 'trade':
            sequence = str(integer(data['sequential_id']))
            event.update(price=decimal_string(data['trade_price']),
                         quantity=decimal_string(data['trade_volume']),
                         side={'BID': 'BUY', 'ASK': 'SELL'}.get(str(data.get('ask_bid')), 'UNKNOWN'))
            identity = sequence  # IDs are unique, not a contiguous or ordered sequence.
        elif stream == 'ticker':
            suspended = data.get('is_trading_suspended')
            if suspended is not None and not isinstance(suspended, bool):
                raise InvalidMessage('invalid suspension flag')
            event.update(price=decimal_string(data['trade_price']),
                         turnover_24h=decimal_string(data['acc_trade_price_24h'], zero=True),
                         volume_24h=decimal_string(data['acc_trade_volume_24h'], zero=True),
                         market_state=data.get('market_state', 'UNKNOWN'),
                         is_trading_suspended=suspended,
                         market_warning=data.get('market_warning', 'UNKNOWN'))
            identity = str(integer(stamp))
        else:
            units = data['orderbook_units']
            if not isinstance(units, list) or not 1 <= len(units) <= 15:
                raise InvalidMessage('orderbook must have 1–15 levels')
            asks = [[decimal_string(x['ask_price']), decimal_string(x['ask_size'], zero=True)] for x in units]
            bids = [[decimal_string(x['bid_price']), decimal_string(x['bid_size'], zero=True)] for x in units]
            asks.sort(key=lambda x: Decimal(x[0]))
            bids.sort(key=lambda x: Decimal(x[0]), reverse=True)
            if (len({Decimal(x[0]) for x in asks}) != len(asks) or len({Decimal(x[0]) for x in bids}) != len(bids)
                    or Decimal(bids[0][0]) >= Decimal(asks[0][0])):
                raise InvalidMessage('crossed or duplicate orderbook levels')
            event.update(asks=asks, bids=bids)
            identity = str(integer(stamp))
        # Different snapshots with an identical ticker/book timestamp remain distinguishable.
        payload_hash = hashlib.sha256(json.dumps(data, default=str, sort_keys=True).encode()).hexdigest()[:16]
        event['event_id'] = f'bithumb:{stream}:{symbol}:{identity}' + (f':{payload_hash}' if stream != 'trade' else '')
        return event
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise InvalidMessage('malformed exchange message') from error
