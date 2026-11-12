import re

import httpx

from .config import REST_URL


async def fetch_markets(client: httpx.AsyncClient) -> list[dict]:
    response = await client.get(f'{REST_URL}/v1/market/all', params={'isDetails': 'true'})
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError('market list must be an array')
    result = {}
    for row in data:
        if not isinstance(row, dict) or not isinstance(row.get('market'), str):
            raise ValueError('invalid market metadata')
        symbol = row['market']
        if not symbol.startswith('KRW-'):
            continue
        if not re.fullmatch(r'KRW-[A-Z0-9]+', symbol):
            raise ValueError('invalid KRW symbol')
        result[symbol] = {'symbol': symbol, 'korean_name': row.get('korean_name', ''),
                          'english_name': row.get('english_name', ''),
                          'market_warning': row.get('market_warning', 'UNKNOWN'),
                          'source': 'bithumb_public'}
    if not result:
        raise ValueError('empty KRW market universe; retain previous subscription')
    return [result[key] for key in sorted(result)]


def retry_delay(error: Exception, attempt: int) -> float:
    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in (429, 418):
        try:
            return max(60., min(3600., float(error.response.headers.get('Retry-After', '60'))))
        except ValueError:
            return 60.
    return float(min(60, 2 ** min(attempt, 6)))
