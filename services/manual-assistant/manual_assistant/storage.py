import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


class Storage:
    def __init__(self, path: Path):
        self.path = path
        self.market_store = self

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self, defaults: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > 10:
                raise RuntimeError(
                    "database is newer than this application; refusing to downgrade"
                )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            if version == 0:
                conn.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS signals(id TEXT PRIMARY KEY,symbol TEXT NOT NULL,type TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,payload TEXT NOT NULL,generated_at TEXT NOT NULL,expires_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS manual_fills(id TEXT PRIMARY KEY,symbol TEXT NOT NULL,side TEXT NOT NULL,
                    quantity TEXT NOT NULL,price TEXT NOT NULL,fee_krw TEXT NOT NULL,filled_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,notes TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS positions(symbol TEXT PRIMARY KEY,quantity TEXT NOT NULL,
                    average_entry_price TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS risk_events(id INTEGER PRIMARY KEY,severity TEXT NOT NULL,
                    rule TEXT NOT NULL,details_json TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS system_events(id INTEGER PRIMARY KEY,type TEXT NOT NULL,
                    details_json TEXT NOT NULL,created_at TEXT NOT NULL);
                PRAGMA user_version=1;
                COMMIT;
            """)
            from .schema import migrate

            migrate(conn)
            at = datetime.now(UTC).isoformat()
            for key, value in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings VALUES (?,?,?)",
                    (key, json.dumps(value), at),
                )
            self.audit(
                conn,
                "startup",
                {"schema_version": 10, "mode": "MANUAL LIVE", "market_data": "OFFLINE"},
            )
        self.path.chmod(0o600)

    @staticmethod
    def audit(conn, kind: str, details: dict):
        conn.execute(
            "INSERT INTO system_events(type,details_json,created_at) VALUES (?,?,?)",
            (
                kind,
                json.dumps(details, ensure_ascii=False),
                datetime.now(UTC).isoformat(),
            ),
        )

    def settings(self):
        with self.connect() as conn:
            return {
                row["key"]: json.loads(row["value"])
                for row in conn.execute("SELECT key,value FROM settings")
            }

    def initialize_market(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript("""
              CREATE TABLE IF NOT EXISTS market_candles(
                symbol TEXT, timeframe INTEGER, start INTEGER, end INTEGER,
                open TEXT, high TEXT, low TEXT, close TEXT, volume TEXT, turnover TEXT,
                trade_count INTEGER, closed INTEGER, complete INTEGER, origin TEXT,
                updated_at TEXT, quality_notes TEXT,
                PRIMARY KEY(symbol, timeframe, start)
              );
              CREATE INDEX IF NOT EXISTS market_candle_pending ON market_candles(end) WHERE timeframe=60 AND closed=0;
              CREATE INDEX IF NOT EXISTS scanner_training_candles ON market_candles(start DESC,symbol) WHERE timeframe=60 AND closed=1 AND complete=1;
              CREATE TABLE IF NOT EXISTS market_events(
                id TEXT PRIMARY KEY, symbol TEXT, type TEXT, exchange_at TEXT, received_at TEXT, payload TEXT
              );
              CREATE INDEX IF NOT EXISTS events_time ON market_events(received_at);
              CREATE INDEX IF NOT EXISTS replay_events_symbol ON market_events(symbol,exchange_at,received_at);
              CREATE TABLE IF NOT EXISTS market_issues(
                id INTEGER PRIMARY KEY, symbol TEXT, kind TEXT, details TEXT, created_at TEXT
              );
              CREATE TABLE IF NOT EXISTS replay_candles(
                id INTEGER PRIMARY KEY, symbol TEXT, start INTEGER, known_at TEXT, payload TEXT,
                UNIQUE(symbol,start,known_at)
              );
            """)
