def migrate(conn):
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 6:
        return
    conn.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE scanner_models(id TEXT PRIMARY KEY,model TEXT NOT NULL,metrics TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL);
        CREATE TABLE scanner_observations(id INTEGER PRIMARY KEY,symbol TEXT NOT NULL,created_at TEXT NOT NULL,features TEXT NOT NULL,analysis TEXT NOT NULL);
        CREATE INDEX scanner_observation_time ON scanner_observations(created_at,symbol);
        CREATE INDEX market_candle_pending ON market_candles(end) WHERE timeframe=60 AND closed=0;
        CREATE INDEX scanner_training_candles ON market_candles(start DESC,symbol) WHERE timeframe=60 AND closed=1 AND complete=1;
        PRAGMA user_version=6;
        COMMIT;
    """)
