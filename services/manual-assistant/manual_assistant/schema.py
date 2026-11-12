import hashlib
import json
from datetime import UTC, datetime


def version_id(kind, params):
    return hashlib.sha256(
        json.dumps({"kind": kind, "params": params}, sort_keys=True).encode()
    ).hexdigest()[:20]


def _migrate_v2(conn):
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 2:
        return
    conn.executescript("""
      BEGIN IMMEDIATE;
      CREATE TABLE market_candles(symbol TEXT,timeframe INTEGER,start INTEGER,end INTEGER,
        open TEXT,high TEXT,low TEXT,close TEXT,volume TEXT,turnover TEXT,
        first_key TEXT,last_key TEXT,closed INTEGER,complete INTEGER,origin TEXT,updated_at TEXT,
        PRIMARY KEY(symbol,timeframe,start));
      CREATE TABLE market_events(id TEXT PRIMARY KEY,symbol TEXT,type TEXT,exchange_at TEXT,received_at TEXT,payload TEXT);
      CREATE INDEX events_time ON market_events(received_at);
      CREATE TABLE market_issues(id INTEGER PRIMARY KEY,symbol TEXT,kind TEXT,details TEXT,created_at TEXT);
      CREATE TABLE fill_links(fill_id TEXT PRIMARY KEY,signal_id TEXT,channel TEXT NOT NULL DEFAULT 'MANUAL');
      CREATE TABLE strategy_versions(id TEXT PRIMARY KEY,kind TEXT,params TEXT,created_at TEXT);
      CREATE TABLE strategy_state(key TEXT PRIMARY KEY,value TEXT);
      CREATE TABLE lab_datasets(id TEXT PRIMARY KEY,symbol TEXT,candles TEXT,created_at TEXT,source TEXT);
      CREATE TABLE lab_runs(id TEXT PRIMARY KEY,version_id TEXT,dataset_id TEXT,report TEXT,created_at TEXT);
      CREATE TABLE paper_accounts(version_id TEXT PRIMARY KEY,cash TEXT,position TEXT,realized TEXT,last_bar INTEGER);
      CREATE TABLE paper_trades(id TEXT PRIMARY KEY,version_id TEXT,payload TEXT,created_at TEXT);
      CREATE TABLE performance_samples(channel TEXT,version_id TEXT,at INTEGER,equity TEXT,PRIMARY KEY(channel,version_id,at));
      CREATE TABLE lab_jobs(id TEXT PRIMARY KEY,status TEXT,request TEXT,result TEXT,error TEXT,created_at TEXT);
      CREATE TABLE promotion_history(id INTEGER PRIMARY KEY,version_id TEXT,previous_version TEXT,report_id TEXT,created_at TEXT);
      PRAGMA user_version=2;
      COMMIT;
    """)
    at = datetime.now(UTC).isoformat()
    for kind in ("breakout", "momentum", "pullback"):
        params = {
            "lookback": 20,
            "volume_ratio": "1.5",
            "stop_loss": "0.008",
            "take_profit": "0.012",
            "max_bars": 15,
        }
        identifier = version_id(kind, params)
        conn.execute(
            "INSERT OR IGNORE INTO strategy_versions VALUES (?,?,?,?)",
            (identifier, kind, json.dumps(params), at),
        )


def migrate(conn):
    _migrate_v2(conn)
    from .private_schema import migrate as migrate_private

    migrate_private(conn)

    from .order_schema import migrate as migrate_orders

    migrate_orders(conn)

    from .automation_schema import migrate as migrate_automation

    migrate_automation(conn)

    from .scanner_schema import migrate as migrate_scanner

    migrate_scanner(conn)

    from .review_schema import migrate as migrate_review

    migrate_review(conn)

    from .reversion_schema import migrate as migrate_reversion

    migrate_reversion(conn)
