"""Replay retained public events into a dedicated bsb_test_* DB; never modify production DB."""
import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from bsb_service.pipeline.config import Config
from bsb_service.pipeline.migrate import upgrade
from bsb_service.pipeline.store import Store
from psycopg.rows import dict_row
from redis.asyncio import Redis

parser = argparse.ArgumentParser()
parser.add_argument('--limit', type=int, default=500, choices=range(1,2001))
parser.add_argument('--report')
args = parser.parse_args()
url = os.environ['TEST_DATABASE_URL']
if not urlparse(url).path.startswith('/bsb_test_'):
    raise SystemExit('a dedicated bsb_test_* database is required')
upgrade(url)


async def main():
    redis = Redis.from_url(os.getenv('PUBLIC_SAMPLE_REDIS_URL','redis://redis:6379/0'), decode_responses=True)
    try:
        sample = await redis.xrange('bsb:bithumb_public:events', count=args.limit)
        if not sample:
            raise SystemExit('no retained public events; no live-data claim can be made')
        async with await psycopg.AsyncConnection.connect(url,autocommit=True,row_factory=dict_row) as conn:
            store = Store(conn,Config(source='bithumb_public',database_url=url))
            result = await store.apply(sample,datetime.now(UTC))
            await store.finalize(datetime.now(UTC))
            cursor = await conn.execute("SELECT event_type,count(*) AS count FROM market_events WHERE source='bithumb_public' GROUP BY event_type")
            counts = await cursor.fetchall()
            assert sum(row['count'] for row in counts) > 0
            cursor = await conn.execute("SELECT count(*) AS count FROM candles WHERE source='bithumb_public'")
            candles = (await cursor.fetchone())['count']
            output = {'verified_at':datetime.now(UTC).isoformat(), 'kind':'retained-public-data-replay',
                      'source':'bithumb_public','sample_size':len(sample),'result':result,
                      'stored_event_types':counts,'stored_candles':candles,'entries_allowed':False}
            if args.report:
                Path(args.report).write_text(json.dumps(output,indent=2)+'\n')
            print(json.dumps(output))
    finally:
        await redis.aclose()


asyncio.run(main())
