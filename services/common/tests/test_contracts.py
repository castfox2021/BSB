from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import ValidationError

from bsb_service.contracts import validate_trade

SCHEMA = Path(__file__).resolve().parents[3] / 'packages/contracts/market-trade.schema.json'
EVENT = dict(schema_version='1', event_id='mock-1', event_type='market.trade', source='mock',
             symbol='KRW-BTC', exchange_at='2026-11-08T00:00:00Z',
             received_at='2026-11-08T00:00:01Z', price='123.456789', quantity='0.000001', side='UNKNOWN')


def test_precision():
    assert validate_trade(EVENT, SCHEMA) == (Decimal('123.456789'), Decimal('0.000001'))


@pytest.mark.parametrize('change', [{'price': 1.1}, {'exchange_at': 'invalid'}, {'source': 'live'}, {'extra': 1}])
def test_reject_invalid(change):
    with pytest.raises(ValidationError):
        validate_trade({**EVENT, **change}, SCHEMA)
