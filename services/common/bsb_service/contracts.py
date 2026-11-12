import json
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


def validate_trade(event: dict, schema_path: Path) -> tuple[Decimal, Decimal]:
    schema = json.loads(schema_path.read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(event)
    price, quantity = Decimal(event['price']), Decimal(event['quantity'])
    if price <= 0 or quantity <= 0:
        raise ValueError('price and quantity must be positive')
    return price, quantity
