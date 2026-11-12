def migrate(conn):
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 7:
        return
    conn.executescript("""
      BEGIN IMMEDIATE;
      CREATE TABLE decision_events(id TEXT PRIMARY KEY,entity_type TEXT,entity_id TEXT,symbol TEXT,event_at TEXT,recorded_at TEXT,payload TEXT);
      CREATE INDEX decision_symbol_time ON decision_events(symbol,event_at);
      CREATE TABLE replay_candles(id INTEGER PRIMARY KEY,symbol TEXT,start INTEGER,known_at TEXT,payload TEXT,UNIQUE(symbol,start,known_at));
      CREATE INDEX replay_known ON replay_candles(symbol,start,known_at);
      CREATE TABLE trading_journals(position_id TEXT PRIMARY KEY,note TEXT,tags TEXT,updated_at TEXT);
      CREATE TABLE trade_reviews(id TEXT PRIMARY KEY,position_id TEXT,created_at TEXT,payload TEXT);
      CREATE TRIGGER decision_no_update BEFORE UPDATE ON decision_events BEGIN SELECT RAISE(ABORT,'immutable decision'); END;
      CREATE TRIGGER decision_no_delete BEFORE DELETE ON decision_events BEGIN SELECT RAISE(ABORT,'immutable decision'); END;
      CREATE TRIGGER replay_no_update BEFORE UPDATE ON replay_candles BEGIN SELECT RAISE(ABORT,'immutable candle'); END;
      CREATE TRIGGER replay_no_delete BEFORE DELETE ON replay_candles BEGIN SELECT RAISE(ABORT,'immutable candle'); END;
      CREATE INDEX replay_events_symbol ON market_events(symbol,exchange_at,received_at);
    """)

    # Old rows have no recoverable original receipt snapshots. Import them now, never backdate knowledge.
    from .review_events import archive_candles, capture_fill

    for row in conn.execute("SELECT * FROM manual_fills").fetchall():
        capture_fill(
            conn,
            row["id"],
            row["symbol"],
            row["filled_at"],
            row["side"],
            row["quantity"],
            row["price"],
            row["fee_krw"],
            source="LEGACY_IMPORTED_NOW",
        )
    for row in conn.execute("SELECT * FROM private_fills").fetchall():
        capture_fill(
            conn,
            "exchange-" + row["trade_id"],
            row["symbol"],
            row["filled_at"],
            row["side"],
            row["quantity"],
            row["price"],
            row["fee_krw"],
            row["order_id"],
            source="LEGACY_IMPORTED_NOW",
        )
    archive_candles(
        conn,
        conn.execute("SELECT * FROM market_candles WHERE timeframe=60").fetchall(),
        source="LEGACY_IMPORTED_NOW",
    )

    conn.execute("PRAGMA user_version=7")
