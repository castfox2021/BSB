import json
from datetime import UTC, datetime
from decimal import Decimal as D

POLICIES = (
    "structure_trailing",
    "fixed_trailing",
    "atr_trailing",
    "structure_fixed_take",
    "partial_tp_trailing",
)

DEFAULT_EXIT_POLICY = {
    "policy_version": 1,
    "tp1_pct": "0.012",
    "tp1_fraction": "0.25",
    "tp2_pct": "0.025",
    "tp2_fraction": "0.25",
    "trailing_drawdown_pct": "0.010",
    "runner_activation": "AFTER_TP2",
    "break_even_after_tp1": False,
    "maximum_stop_rate": "0.015",
    "min_order_krw": "5000",
    "mode": "PAPER",
}


def migrate_exit_tables(conn):
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS position_exit_policy(
        position_id TEXT PRIMARY KEY,
        policy_version INTEGER NOT NULL,
        entry_avg_price TEXT NOT NULL,
        initial_quantity TEXT NOT NULL,
        tp1_pct TEXT NOT NULL,
        tp1_fraction TEXT NOT NULL,
        tp2_pct TEXT NOT NULL,
        tp2_fraction TEXT NOT NULL,
        trailing_drawdown_pct TEXT NOT NULL,
        runner_activation TEXT NOT NULL,
        break_even_after_tp1 INTEGER NOT NULL DEFAULT 0,
        peak_price TEXT NOT NULL,
        protective_stop_price TEXT NOT NULL,
        state TEXT NOT NULL,
        mode TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS position_exit_leg(
        id TEXT PRIMARY KEY,
        position_id TEXT NOT NULL,
        leg TEXT NOT NULL,
        target_quantity TEXT NOT NULL,
        filled_quantity TEXT NOT NULL,
        avg_fill_price TEXT,
        order_id TEXT,
        client_order_id TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL,
        realized_pnl TEXT NOT NULL DEFAULT '0',
        fees TEXT NOT NULL DEFAULT '0',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_exit_leg_position ON position_exit_leg(position_id, leg);
      CREATE TABLE IF NOT EXISTS position_exit_event(
        id TEXT PRIMARY KEY,
        position_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        market_price TEXT NOT NULL,
        state_before TEXT NOT NULL,
        state_after TEXT NOT NULL,
        reason TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_exit_event_position ON position_exit_event(position_id, created_at);
    """)
    conn.execute(
        "INSERT OR IGNORE INTO reversion_accounts VALUES (?,?,?,?,?)",
        ("partial_tp_trailing", "150000", "0", "150000", "0"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO strategy_state VALUES('exit_policy_config',?)",
        (json.dumps(DEFAULT_EXIT_POLICY),),
    )


def migrate(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= 10:
        migrate_exit_tables(conn)
        return
    if version < 8:
        conn.executescript("""
      BEGIN IMMEDIATE;
      CREATE TABLE reversion_accounts(policy TEXT PRIMARY KEY,cash TEXT NOT NULL,realized TEXT NOT NULL,peak_equity TEXT NOT NULL,max_drawdown TEXT NOT NULL);
      CREATE TABLE reversion_positions(id TEXT PRIMARY KEY,entry_group TEXT NOT NULL,policy TEXT NOT NULL,symbol TEXT NOT NULL,status TEXT NOT NULL,quantity TEXT NOT NULL,cost TEXT NOT NULL,entry_price TEXT NOT NULL,structure_low TEXT NOT NULL,initial_stop TEXT NOT NULL,current_stop TEXT NOT NULL,peak_price TEXT NOT NULL,trail_active INTEGER NOT NULL,opened_at TEXT NOT NULL,closed_at TEXT,exit_price TEXT,net_pnl TEXT,exit_reason TEXT,config TEXT NOT NULL,UNIQUE(entry_group,policy));
      CREATE INDEX reversion_open_positions ON reversion_positions(status,symbol);
      CREATE TABLE reversion_events(id TEXT PRIMARY KEY,position_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
      CREATE TABLE reversion_cooldowns(symbol TEXT PRIMARY KEY,until_at TEXT NOT NULL,reason TEXT NOT NULL);
      PRAGMA user_version=8;
      COMMIT;
    """)
    for policy in POLICIES:
        conn.execute(
            "INSERT OR IGNORE INTO reversion_accounts VALUES (?,?,?,?,?)",
            (policy, "150000", "0", "150000", "0"),
        )
    conn.execute(
        "INSERT OR IGNORE INTO strategy_state VALUES('reversion_config',?)",
        (
            json.dumps(
                {
                    "enabled": True,
                    "live_execution": False,
                    "drop_1m": "0.02",
                    "drop_3m": "0.03",
                    "drop_5m": "0.04",
                    "rebound_rate": "0.008",
                    "maximum_stop_rate": "0.015",
                    "structure_buffer_rate": "0.002",
                    "trailing_activation_rate": "0.02",
                    "trailing_drawdown_rate": "0.01",
                    "failure_seconds": 600,
                    "cooldown_seconds": 1800,
                    "fixed_take_comparison_rate": "0.025",
                    "entry_budget_krw": "50000",
                }
            ),
        ),
    )
    row = conn.execute(
        "SELECT value FROM strategy_state WHERE key='reversion_config'"
    ).fetchone()
    config = json.loads(row[0])
    config.update(
        maximum_stop_rate="0.015",
        trailing_activation_rate="0.020",
        trailing_drawdown_rate="0.010",
        failure_seconds=600,
        fixed_take_comparison_rate="0.025",
        rebound_rate="0.008",
        min_base_bars=5,
        base_range_rate="0.015",
        confirmation_bars=2,
        breakout_buffer_rate="0.002",
        max_chase_rate="0.003",
        drop_1m_confirm_3m="0.012",
    )
    conn.execute(
        "UPDATE strategy_state SET value=? WHERE key='reversion_config'",
        (json.dumps(config),),
    )
    for policy in POLICIES:
        equity = peak = D("150000")
        maximum = D(0)
        for closed in conn.execute(
            "SELECT net_pnl FROM reversion_positions WHERE policy=? AND status='CLOSED' ORDER BY closed_at,id",
            (policy,),
        ):
            equity += D(closed[0])
            peak = max(peak, equity)
            maximum = max(maximum, (peak - equity) / peak if peak else D(0))
        conn.execute(
            "UPDATE reversion_accounts SET peak_equity=?,max_drawdown=? WHERE policy=?",
            (format(peak, "f"), format(maximum, "f"), policy),
        )
    conn.execute("PRAGMA user_version=10")
    migrate_exit_tables(conn)
    conn.execute(
        "INSERT INTO system_events(type,details_json,created_at) VALUES (?,?,?)",
        (
            "REVERSION_SCHEMA_ENABLED",
            json.dumps({"paper_only": True, "live_execution": False, "policy_revision": 3}),
            datetime.now(UTC).isoformat(),
        ),
    )
