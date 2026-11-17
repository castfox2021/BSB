import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path


class _PgCompatCursor:
    def __init__(self, rows=None, rowcount: int = 0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _PgCompatConn:
    """Minimal sqlite3-compatible wrapper around a psycopg connection."""

    def __init__(self, pg_conn):
        self._conn = pg_conn
        self._last_changes = 0

    def execute(self, sql: str, params=()):
        q = sql.strip()
        if q.upper() == "SELECT CHANGES()":
            return _PgCompatCursor([(self._last_changes,)], 1)
        pg_sql = q.replace("?", "%s")
        if re.match(r"(?i)^INSERT\s+OR\s+IGNORE\s+INTO\s+", pg_sql):
            pg_sql = re.sub(r"(?i)^INSERT\s+OR\s+IGNORE\s+INTO\s+", "INSERT INTO ", pg_sql, count=1)
            if "ON CONFLICT" not in pg_sql.upper():
                pg_sql = pg_sql.rstrip(";") + " ON CONFLICT DO NOTHING"
        with self._conn.cursor() as cur:
            cur.execute(pg_sql, params or ())
            self._last_changes = cur.rowcount or 0
            if cur.description is not None:
                rows = cur.fetchall()
                return _PgCompatCursor(rows, len(rows))
            return _PgCompatCursor([], self._last_changes)

    def executescript(self, script: str):
        with self._conn.cursor() as cur:
            cur.execute(script)


class Storage:
    def __init__(self, path: Path):
        self.path = path
        self.market_store = self
        self._pg_local = threading.local()
        self._pg_conns: list = []
        self._pg_lock = threading.Lock()

    @staticmethod
    def is_postgres_enabled() -> bool:
        if os.environ.get("USE_POSTGRES") != "1" or not (os.environ.get("DATABASE_URL") or "").strip():
            return False
        try:
            import psycopg  # type: ignore  # noqa: F401
            return True
        except ImportError:
            return False

    def _get_pg_conn(self):
        import psycopg  # type: ignore
        from psycopg.rows import dict_row  # type: ignore

        conn = getattr(self._pg_local, "conn", None)
        if conn is not None:
            if not getattr(conn, "closed", False):
                return conn
        dsn = (os.environ.get("DATABASE_URL") or "").strip()
        conn = psycopg.connect(dsn, row_factory=dict_row)
        self._pg_local.conn = conn
        with self._pg_lock:
            self._pg_conns.append(conn)
        return conn

    def close_pools(self):
        with self._pg_lock:
            for conn in self._pg_conns:
                try:
                    conn.close()
                except Exception:
                    pass
            self._pg_conns.clear()
        self._pg_local = threading.local()

    @contextmanager
    def identity_connect(self):
        if self.is_postgres_enabled():
            pg_conn = self._get_pg_conn()
            try:
                yield _PgCompatConn(pg_conn)
                pg_conn.commit()
            except Exception:
                try:
                    pg_conn.rollback()
                except Exception:
                    self._pg_local.conn = None
                raise
        else:
            with self.connect() as conn:
                yield conn

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA cache_size=-4000")
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

    def audit_event(self, kind: str, details: dict):
        """Record an audit event to PostgreSQL (when USE_POSTGRES=1) or SQLite."""
        if self.is_postgres_enabled():
            try:
                with self.identity_connect() as conn:
                    self.audit(conn, kind, details)
                return
            except Exception:
                pass
        with self.connect() as conn:
            self.audit(conn, kind, details)

    def prune_old_market_data(self, retention_days: int = 7) -> dict:
        """Delete market_events older than retention_days in bounded batches."""
        days = max(1, min(int(retention_days), 365))
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        events_deleted = 0
        with self.market_store.connect() as mconn:
            while True:
                mconn.execute(
                    "DELETE FROM market_events WHERE rowid IN (SELECT rowid FROM market_events WHERE received_at < ? LIMIT 5000)",
                    (cutoff,),
                )
                n = mconn.execute("SELECT changes()").fetchone()[0]
                events_deleted += n
                if n < 5000:
                    break
        return {
            "retention_days": days,
            "cutoff": cutoff,
            "replay_candles_deleted": 0,
            "market_events_deleted": events_deleted,
        }

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
