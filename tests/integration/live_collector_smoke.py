"""Opt-in public-market network smoke. No account access and no order requests."""
import argparse
import json
import time
from urllib.request import urlopen

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:8000')
parser.add_argument('--timeout', type=int, default=60)
args = parser.parse_args()
deadline = time.monotonic() + args.timeout
last = None
while time.monotonic() < deadline:
    with urlopen(f'{args.url}/collector/status', timeout=10) as response:
        last = json.load(response)
    assert last['source'] == 'bithumb_public', 'start with COLLECTOR_SOURCE=bithumb_public'
    assert last['entries_allowed'] is False and last['history_complete'] is False
    if last['transport_ready'] and all(last['counts'].get(f'market.{kind}.REALTIME', 0) > 0
                                       for kind in ('ticker', 'trade', 'orderbook')):
        assert sum(s['symbols_count'] for s in last['shards']) == last['markets_count']
        with urlopen(f'{args.url}/collector/latest/KRW-BTC', timeout=10) as response:
            latest = json.load(response)
        assert latest['source'] == 'bithumb_public' and latest['entries_allowed'] is False
        assert latest['events']
        assert all(e['source'] == 'bithumb_public' and e['exchange_at'].endswith('Z')
                   and e['received_at'].endswith('Z') for e in latest['events'])
        print(json.dumps({'result':'passed', 'markets':last['markets_count'],
                          'shards':len(last['shards']), 'counts':last['counts'],
                          'btc_event_types':[e['event_type'] for e in latest['events']]}, ensure_ascii=False))
        break
    time.sleep(1)
else:
    raise SystemExit(f'Live collector not ready within timeout: {last}')
