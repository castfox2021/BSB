import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from .config import Config
from .validation import (
    Event,
    InvalidEvent,
    bucket_start,
    parse_time,
    stream_id,
    validate,
)

CANDLE_UPSERT = '''
INSERT INTO candles(source,symbol,timeframe,bucket_start,bucket_end,
 open,high,low,close,volume,turnover,trade_count,first_at,last_at,
 first_event_id,last_event_id,quality_flags,updated_at)
VALUES (%(source)s,%(symbol)s,%(tf)s,%(start)s,%(end)s,%(price)s,%(price)s,%(price)s,%(price)s,
 %(quantity)s,%(price)s::numeric * %(quantity)s::numeric,1,%(at)s,%(at)s,%(id)s,%(id)s,%(flags)s,%(now)s)
ON CONFLICT(source,symbol,timeframe,bucket_start) DO UPDATE SET
 open=CASE WHEN (EXCLUDED.first_at,EXCLUDED.first_event_id)<(candles.first_at,candles.first_event_id)
           THEN EXCLUDED.open ELSE candles.open END,
 close=CASE WHEN (EXCLUDED.last_at,EXCLUDED.last_event_id)>(candles.last_at,candles.last_event_id)
           THEN EXCLUDED.close ELSE candles.close END,
 high=GREATEST(candles.high,EXCLUDED.high), low=LEAST(candles.low,EXCLUDED.low),
 volume=candles.volume+EXCLUDED.volume, turnover=candles.turnover+EXCLUDED.turnover,
 trade_count=candles.trade_count+1,
 first_at=LEAST(candles.first_at,EXCLUDED.first_at), last_at=GREATEST(candles.last_at,EXCLUDED.last_at),
 first_event_id=CASE WHEN (EXCLUDED.first_at,EXCLUDED.first_event_id)<(candles.first_at,candles.first_event_id)
                    THEN EXCLUDED.first_event_id ELSE candles.first_event_id END,
 last_event_id=CASE WHEN (EXCLUDED.last_at,EXCLUDED.last_event_id)>(candles.last_at,candles.last_event_id)
                   THEN EXCLUDED.last_event_id ELSE candles.last_event_id END,
 quality_flags=ARRAY(SELECT DISTINCT flag FROM unnest(candles.quality_flags || EXCLUDED.quality_flags ||
               CASE WHEN candles.closed THEN ARRAY['late_correction'] ELSE ARRAY[]::text[] END) flag ORDER BY flag),
 complete=FALSE, revised_after_close=candles.revised_after_close OR candles.closed,
 revision=candles.revision+1, updated_at=EXCLUDED.updated_at
'''


class Store:
    def __init__(self, connection: AsyncConnection[dict[str, Any]], config: Config):
        self.conn = connection
        self.config = config

    async def offset(self) -> str:
        await self.conn.execute('INSERT INTO pipeline_offsets(source) VALUES (%s) ON CONFLICT DO NOTHING',
                                (self.config.source,))
        cursor = await self.conn.execute('SELECT last_stream_id FROM pipeline_offsets WHERE source=%s',
                                         (self.config.source,))
        row = await cursor.fetchone()
        assert row is not None
        return row['last_stream_id']

    async def issue(self, code: str, start: datetime, end: datetime, *, symbol=None,
                    details=None, key=None, blocking=True):
        await self.conn.execute('''INSERT INTO data_quality_events(source,symbol,code,start_at,end_at,details,dedup_key,blocking)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(dedup_key) DO NOTHING''',
            (self.config.source, symbol, code, min(start, end), max(start, end),
             Jsonb(details or {}), key, blocking))
        if blocking:
            await self.conn.execute('''UPDATE candles SET complete=FALSE,revision=revision+1,updated_at=now(),
                quality_flags=ARRAY(SELECT DISTINCT x FROM unnest(quality_flags || ARRAY[%s]::text[]) x ORDER BY x)
                WHERE source=%s AND (%s::text IS NULL OR symbol=%s)
                  AND bucket_start<=%s AND bucket_end>%s AND (complete OR NOT %s=ANY(quality_flags))''',
                (code, self.config.source, symbol, symbol, max(start, end), min(start, end), code))

    async def coverage(self, event: Event):
        data = event.payload
        for shard in data['shards']:
            for symbol in shard['symbols']:
                cursor = await self.conn.execute('SELECT * FROM stream_coverage WHERE source=%s AND symbol=%s',
                                                 (self.config.source, symbol))
                old = await cursor.fetchone()
                if old and event.received <= old['last_seen_at']:
                    continue
                continuous = (old and old['connected'] and shard['connected']
                              and old['connection_id'] == shard['connection_id']
                              and (event.received - old['last_seen_at']).total_seconds() <= self.config.heartbeat_timeout)
                start = old['start_at'] if continuous and old else event.received
                if old and not continuous:
                    gap_start = min(old['last_seen_at'], old['last_data_at'] or old['last_seen_at'],
                                    parse_time(shard['last_data_at']) if shard.get('last_data_at') else event.received)
                    await self.issue('connection_gap', gap_start, event.received, symbol=symbol,
                                     key=f'{self.config.source}:{data["event_id"]}:{symbol}:gap')
                invalid_count = data.get('invalid_messages', 0)
                if old and invalid_count > old['invalid_count']:
                    await self.issue('invalid_frame', old['last_seen_at'], event.received, symbol=symbol,
                                     key=f'{self.config.source}:{data["event_id"]}:{symbol}:invalid')
                    start = event.received
                await self.conn.execute('''INSERT INTO stream_coverage
                    (source,symbol,connection_id,start_at,last_seen_at,last_data_at,connected,invalid_count)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(source,symbol) DO UPDATE SET
                    connection_id=EXCLUDED.connection_id,start_at=EXCLUDED.start_at,
                    last_seen_at=EXCLUDED.last_seen_at,last_data_at=EXCLUDED.last_data_at,connected=EXCLUDED.connected,invalid_count=EXCLUDED.invalid_count''',
                    (self.config.source, symbol, shard['connection_id'], start, event.received,
                     parse_time(shard['last_data_at']) if shard.get('last_data_at') else event.received,
                     shard['connected'], invalid_count))

    async def project(self, event: Event, now: datetime):
        data = event.payload
        kind = data['event_type']
        source = self.config.source
        if kind == 'collector.status':
            await self.coverage(event)
            return
        if kind == 'collector.universe':
            symbols = [m['symbol'] for m in data['markets']]
            await self.conn.execute("UPDATE markets SET status='REMOVED',updated_at=%s WHERE source=%s AND NOT(symbol=ANY(%s))",
                                    (event.received, source, symbols))
            for row in data['markets']:
                await self.conn.execute('''INSERT INTO markets(source,symbol,korean_name,english_name,market_warning,status,updated_at)
                    VALUES (%s,%s,%s,%s,%s,'LISTED',%s) ON CONFLICT(source,symbol) DO UPDATE SET
                    korean_name=EXCLUDED.korean_name,english_name=EXCLUDED.english_name,
                    market_warning=EXCLUDED.market_warning,status='LISTED',updated_at=EXCLUDED.updated_at''',
                    (source, row['symbol'], row.get('korean_name',''), row.get('english_name',''),
                     row.get('market_warning','UNKNOWN'), event.received))
            return
        symbol, identifier = data['symbol'], data['event_id']
        await self.conn.execute('''INSERT INTO markets(source,symbol,updated_at) VALUES (%s,%s,%s)
                                  ON CONFLICT DO NOTHING''', (source, symbol, event.received))
        for flag in event.flags:
            if flag != 'non_realtime':
                await self.issue(flag, event.at, event.at, symbol=symbol,
                                 key=f'{source}:{identifier}:{flag}')
        if kind == 'market.trade':
            included = data.get('stream_type') == 'REALTIME'
            cursor = await self.conn.execute('''SELECT exchange_at FROM market_trades WHERE source=%s AND symbol=%s
                ORDER BY exchange_at DESC LIMIT 1''', (source, symbol))
            previous = await cursor.fetchone()
            if previous and event.at < previous['exchange_at']:
                await self.issue('out_of_order', event.at, event.at, symbol=symbol, blocking=False,
                                 key=f'{source}:{identifier}:order')
            await self.conn.execute('''INSERT INTO market_trades
                (source,event_id,symbol,price,quantity,side,exchange_at,received_at,included_in_candles)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (source, identifier, symbol, Decimal(data['price']), Decimal(data['quantity']),
                 data['side'], event.at, event.received, included))
            if not included:
                return
            for tf in (60, 300):
                start = bucket_start(event.at, tf)
                await self.conn.execute(CANDLE_UPSERT,
                    {'source':source, 'symbol':symbol, 'tf':tf, 'start':start,
                     'end':start + timedelta(seconds=tf), 'price':Decimal(data['price']),
                     'quantity':Decimal(data['quantity']), 'at':event.at, 'id':identifier,
                     'flags':event.flags, 'now':now})
        elif kind == 'market.orderbook':
            await self.conn.execute('''INSERT INTO orderbook_snapshots
                (source,event_id,symbol,bids,asks,exchange_at,received_at) VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                (source, identifier, symbol, Jsonb(data['bids']), Jsonb(data['asks']), event.at, event.received))
        else:
            await self.conn.execute('''INSERT INTO market_tickers
                (source,event_id,symbol,price,volume_24h,turnover_24h,exchange_at,received_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                (source, identifier, symbol, Decimal(data['price']), Decimal(data['volume_24h']),
                 Decimal(data['turnover_24h']), event.at, event.received))

    async def apply(self, rows: list, now: datetime) -> dict:
        stats = {'accepted':0, 'duplicates':0, 'rejected':0}
        async with self.conn.transaction():
            await self.offset()
            cursor = await self.conn.execute('SELECT last_stream_id FROM pipeline_offsets WHERE source=%s FOR UPDATE',
                                             (self.config.source,))
            offset_row = await cursor.fetchone()
            assert offset_row is not None
            current = offset_row['last_stream_id']
            for identifier, fields in rows:
                stream_id(identifier)
                if stream_id(identifier) <= stream_id(current):
                    continue
                raw = fields.get('payload', '')
                try:
                    if not isinstance(raw, str) or len(raw) > 2**20:
                        raise InvalidEvent('missing or oversized payload')
                    event = validate(raw, self.config, now)
                except InvalidEvent as error:
                    await self.conn.execute('''INSERT INTO pipeline_rejections(source,stream_id,raw,reason)
                        VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                        (self.config.source, identifier, str(raw)[:2**20], str(error)))
                    try:
                        invalid_at = parse_time(json.loads(raw).get('exchange_at'))
                        invalid_at = min(invalid_at, now)
                    except Exception:
                        invalid_at = now
                    await self.conn.execute('UPDATE stream_coverage SET connected=FALSE WHERE source=%s', (self.config.source,))
                    await self.issue('invalid_event', invalid_at, now, details={'stream_id':identifier},
                                     key=f'{self.config.source}:{identifier}:invalid')
                    stats['rejected'] += 1
                else:
                    data = event.payload
                    cursor = await self.conn.execute('''INSERT INTO market_events
                        (source,event_id,event_type,symbol,exchange_at,received_at,connection_id,stream_type,payload,content_hash,quality_flags)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(source,event_id) DO NOTHING RETURNING event_id''',
                        (self.config.source, data['event_id'], data['event_type'], data.get('symbol'), event.at,
                         event.received, data.get('connection_id'), data.get('stream_type','UNKNOWN'),
                         Jsonb(data), event.digest, event.flags))
                    if await cursor.fetchone():
                        await self.project(event, now)
                        stats['accepted'] += 1
                    else:
                        cursor = await self.conn.execute('SELECT content_hash FROM market_events WHERE source=%s AND event_id=%s',
                                                         (self.config.source, data['event_id']))
                        stored = await cursor.fetchone()
                        assert stored is not None
                        if stored['content_hash'] != event.digest:
                            await self.conn.execute('''INSERT INTO pipeline_rejections(source,stream_id,raw,reason)
                                VALUES (%s,%s,%s,'event_id_conflict') ON CONFLICT DO NOTHING''',
                                (self.config.source, identifier, raw))
                            await self.issue('event_id_conflict', event.at, event.at, symbol=data.get('symbol'),
                                             key=f'{self.config.source}:{identifier}:conflict')
                        stats['duplicates'] += 1
                current = identifier
            await self.conn.execute('UPDATE pipeline_offsets SET last_stream_id=%s,updated_at=%s WHERE source=%s',
                                    (current, now, self.config.source))
        return {**stats, 'last_stream_id':current}

    async def retention_gap(self, first_id: str | None, last_id: str | None, now: datetime):
        current = await self.offset()
        if current == '0-0':
            return current
        reset = last_id is not None and stream_id(last_id) < stream_id(current)
        trimmed = first_id is not None and stream_id(current) < stream_id(first_id)
        missing = last_id is None
        empty_trim = first_id is None and last_id is not None and stream_id(last_id) > stream_id(current)
        if reset or trimmed or missing or empty_trim:
            code = 'stream_reset' if reset or missing else 'stream_retention_gap'
            start = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=stream_id(current)[0])
            async with self.conn.transaction():
                await self.issue(code, start, now, details={'cursor':current, 'first_id':first_id, 'last_id':last_id},
                                 key=f'{self.config.source}:{code}:{current}:{first_id}:{last_id}')
                await self.conn.execute('UPDATE stream_coverage SET connected=FALSE WHERE source=%s', (self.config.source,))
                if reset or missing:
                    current = '0-0'
                    await self.conn.execute('UPDATE pipeline_offsets SET last_stream_id=%s,updated_at=%s WHERE source=%s',
                                            (current, now, self.config.source))
        return current

    async def finalize(self, now: datetime):
        async with self.conn.transaction():
            cursor = await self.conn.execute('''UPDATE candles c SET closed=TRUE,revision=revision+1,updated_at=%s,
                complete=(c.quality_flags='{}'::text[] AND NOT c.revised_after_close AND EXISTS(
                    SELECT 1 FROM stream_coverage v WHERE v.source=c.source AND v.symbol=c.symbol
                      AND v.connected AND v.start_at<=c.bucket_start AND v.last_seen_at>=c.bucket_end
                      AND v.last_seen_at>=%s - %s * interval '1 second'
                      AND v.last_data_at>=%s - %s * interval '1 second')
                  AND NOT EXISTS(SELECT 1 FROM data_quality_events q WHERE q.source=c.source AND q.blocking
                    AND (q.symbol IS NULL OR q.symbol=c.symbol)
                    AND q.start_at<c.bucket_end AND q.end_at>=c.bucket_start))
                WHERE c.source=%s AND NOT c.closed AND c.bucket_end<=%s - %s * interval '1 second'
                RETURNING symbol''',
                (now, now, self.config.heartbeat_timeout, now, self.config.max_delay_seconds,
                 self.config.source, now, self.config.lateness_seconds))
            return len(await cursor.fetchall())
