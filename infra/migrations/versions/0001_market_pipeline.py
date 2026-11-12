"""Durable event log, exact numeric projections, cursor and coverage.

Revision ID: 0001_market_pipeline
"""
from alembic import op

revision = '0001_market_pipeline'
down_revision = None
branch_labels = None
depends_on = None

DDL = '''
CREATE TABLE markets (
 source TEXT NOT NULL, symbol TEXT NOT NULL, korean_name TEXT NOT NULL DEFAULT '',
 english_name TEXT NOT NULL DEFAULT '', market_warning TEXT NOT NULL DEFAULT 'UNKNOWN',
 status TEXT NOT NULL DEFAULT 'UNKNOWN', updated_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(source,symbol)
);
CREATE TABLE market_events (
 source TEXT NOT NULL, event_id TEXT NOT NULL, event_type TEXT NOT NULL, symbol TEXT,
 exchange_at TIMESTAMPTZ NOT NULL, received_at TIMESTAMPTZ NOT NULL,
 connection_id TEXT, stream_type TEXT NOT NULL, payload JSONB NOT NULL,
 content_hash TEXT NOT NULL, quality_flags TEXT[] NOT NULL DEFAULT '{}',
 PRIMARY KEY(source,event_id)
);
CREATE INDEX market_events_time ON market_events(source,symbol,exchange_at);
CREATE TABLE market_trades (
 source TEXT NOT NULL, event_id TEXT NOT NULL, symbol TEXT NOT NULL,
 price NUMERIC NOT NULL CHECK(price>0), quantity NUMERIC NOT NULL CHECK(quantity>0),
 side TEXT NOT NULL CHECK(side IN ('BUY','SELL','UNKNOWN')),
 exchange_at TIMESTAMPTZ NOT NULL, received_at TIMESTAMPTZ NOT NULL,
 included_in_candles BOOLEAN NOT NULL,
 PRIMARY KEY(source,event_id), FOREIGN KEY(source,event_id) REFERENCES market_events(source,event_id)
);
CREATE INDEX market_trades_time ON market_trades(source,symbol,exchange_at,event_id);
CREATE TABLE orderbook_snapshots (
 source TEXT NOT NULL, event_id TEXT NOT NULL, symbol TEXT NOT NULL,
 bids JSONB NOT NULL, asks JSONB NOT NULL, exchange_at TIMESTAMPTZ NOT NULL,
 received_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(source,event_id),
 FOREIGN KEY(source,event_id) REFERENCES market_events(source,event_id)
);
CREATE TABLE market_tickers (
 source TEXT NOT NULL, event_id TEXT NOT NULL, symbol TEXT NOT NULL,
 price NUMERIC NOT NULL CHECK(price>0), volume_24h NUMERIC NOT NULL,
 turnover_24h NUMERIC NOT NULL, exchange_at TIMESTAMPTZ NOT NULL, received_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(source,event_id), FOREIGN KEY(source,event_id) REFERENCES market_events(source,event_id)
);
CREATE TABLE candles (
 source TEXT NOT NULL, symbol TEXT NOT NULL, timeframe INTEGER NOT NULL CHECK(timeframe IN (60,300)),
 bucket_start TIMESTAMPTZ NOT NULL, bucket_end TIMESTAMPTZ NOT NULL,
 open NUMERIC NOT NULL, high NUMERIC NOT NULL, low NUMERIC NOT NULL, close NUMERIC NOT NULL,
 volume NUMERIC NOT NULL, turnover NUMERIC NOT NULL, trade_count BIGINT NOT NULL,
 first_at TIMESTAMPTZ NOT NULL, last_at TIMESTAMPTZ NOT NULL,
 first_event_id TEXT NOT NULL, last_event_id TEXT NOT NULL,
 closed BOOLEAN NOT NULL DEFAULT FALSE, complete BOOLEAN NOT NULL DEFAULT FALSE,
 revised_after_close BOOLEAN NOT NULL DEFAULT FALSE, revision BIGINT NOT NULL DEFAULT 1,
 quality_flags TEXT[] NOT NULL DEFAULT '{}', updated_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(source,symbol,timeframe,bucket_start)
);
CREATE INDEX candles_recent ON candles(source,symbol,timeframe,bucket_start DESC);
CREATE TABLE pipeline_offsets (
 source TEXT PRIMARY KEY, last_stream_id TEXT NOT NULL DEFAULT '0-0',
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE pipeline_rejections (
 source TEXT NOT NULL, stream_id TEXT NOT NULL, raw TEXT NOT NULL, reason TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(source,stream_id)
);
CREATE TABLE data_quality_events (
 id BIGSERIAL PRIMARY KEY, source TEXT NOT NULL, symbol TEXT, code TEXT NOT NULL,
 start_at TIMESTAMPTZ NOT NULL, end_at TIMESTAMPTZ NOT NULL,
 blocking BOOLEAN NOT NULL DEFAULT TRUE, details JSONB NOT NULL DEFAULT '{}', dedup_key TEXT UNIQUE
);
CREATE INDEX quality_window ON data_quality_events(source,symbol,start_at,end_at);
CREATE TABLE stream_coverage (
 source TEXT NOT NULL, symbol TEXT NOT NULL, connection_id TEXT NOT NULL,
 start_at TIMESTAMPTZ NOT NULL, last_seen_at TIMESTAMPTZ NOT NULL,
 last_data_at TIMESTAMPTZ, connected BOOLEAN NOT NULL, invalid_count BIGINT NOT NULL DEFAULT 0, PRIMARY KEY(source,symbol)
)
'''


def upgrade():
    for statement in DDL.split(';'):
        if statement.strip():
            op.execute(statement)


def downgrade():
    for table in ['stream_coverage', 'data_quality_events', 'pipeline_rejections', 'pipeline_offsets',
                  'candles', 'market_tickers', 'orderbook_snapshots', 'market_trades', 'market_events', 'markets']:
        op.execute(f'DROP TABLE {table}')
