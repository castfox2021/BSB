import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


class Storage:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
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
            conn.execute("PRAGMA synchronous=FULL")
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
