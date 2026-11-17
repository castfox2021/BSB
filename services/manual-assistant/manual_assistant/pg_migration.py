"""PostgreSQL multi-tenant schema generator & SQLite inspection helper (Phase 3)."""

from pathlib import Path

PG_DDL = """-- BSB PostgreSQL Multi-Tenant Schema (Phase 3)
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'MEMBER',
  totp_secret TEXT,
  mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS admin_sessions (
  token_hash TEXT PRIMARY KEY,
  username TEXT NOT NULL,
  ip TEXT,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  username TEXT NOT NULL,
  role TEXT NOT NULL,
  ip TEXT,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_exchange_credentials (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  exchange TEXT NOT NULL,
  label TEXT NOT NULL,
  access_key_enc TEXT NOT NULL,
  secret_key_enc TEXT NOT NULL,
  key_hint TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(user_id, exchange, label)
);

CREATE TABLE IF NOT EXISTS exchange_orders (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
  exchange TEXT NOT NULL,
  account_id TEXT NOT NULL,
  exchange_order_id TEXT NOT NULL,
  client_order_id TEXT,
  market TEXT NOT NULL,
  side TEXT NOT NULL,
  order_type TEXT NOT NULL,
  order_price NUMERIC(36, 18),
  order_quantity NUMERIC(36, 18),
  executed_quantity NUMERIC(36, 18) NOT NULL DEFAULT 0,
  remaining_quantity NUMERIC(36, 18) NOT NULL DEFAULT 0,
  executed_amount NUMERIC(36, 18) NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  execution_origin TEXT NOT NULL DEFAULT 'UNKNOWN',
  data_source TEXT NOT NULL DEFAULT 'BITHUMB_API',
  ordered_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  raw_payload JSONB NOT NULL,
  UNIQUE(exchange, account_id, exchange_order_id)
);

CREATE TABLE IF NOT EXISTS exchange_fills (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
  exchange TEXT NOT NULL,
  account_id TEXT NOT NULL,
  exchange_fill_id TEXT,
  exchange_order_id TEXT,
  client_order_id TEXT,
  market TEXT NOT NULL,
  side TEXT NOT NULL,
  fill_price NUMERIC(36, 18) NOT NULL,
  fill_quantity NUMERIC(36, 18) NOT NULL,
  fill_amount NUMERIC(36, 18) NOT NULL,
  fee_amount NUMERIC(36, 18) NOT NULL DEFAULT 0,
  fee_currency TEXT NOT NULL DEFAULT 'KRW',
  fee_krw_equiv NUMERIC(36, 18) NOT NULL DEFAULT 0,
  fee_eval_note TEXT,
  execution_origin TEXT NOT NULL DEFAULT 'UNKNOWN',
  data_source TEXT NOT NULL DEFAULT 'BITHUMB_API',
  source_hash TEXT,
  filled_at TIMESTAMPTZ NOT NULL,
  raw_payload JSONB NOT NULL
);
ALTER TABLE exchange_fills ADD COLUMN IF NOT EXISTS fee_eval_note TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_ex_fills_unique_id
  ON exchange_fills(account_id, exchange_fill_id)
  WHERE exchange_fill_id IS NOT NULL AND exchange_fill_id <> '';

CREATE TABLE IF NOT EXISTS trade_positions (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
  account_id TEXT NOT NULL,
  market TEXT NOT NULL,
  position_source TEXT NOT NULL,
  strategy_name TEXT,
  strategy_version TEXT,
  opened_at TIMESTAMPTZ NOT NULL,
  closed_at TIMESTAMPTZ,
  acquired_quantity NUMERIC(36, 18) NOT NULL,
  disposed_quantity NUMERIC(36, 18) NOT NULL,
  remaining_quantity NUMERIC(36, 18) NOT NULL,
  cost_basis NUMERIC(36, 18) NOT NULL,
  realized_pnl NUMERIC(36, 18) NOT NULL,
  realized_pnl_pct NUMERIC(18, 6),
  fee_total NUMERIC(36, 18) NOT NULL,
  status TEXT NOT NULL,
  calculation_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_lot_allocations (
  id TEXT PRIMARY KEY,
  position_id TEXT NOT NULL REFERENCES trade_positions(id) ON DELETE CASCADE,
  buy_fill_id TEXT NOT NULL,
  sell_fill_id TEXT NOT NULL,
  market TEXT NOT NULL,
  allocated_quantity NUMERIC(36, 18) NOT NULL,
  allocated_cost NUMERIC(36, 18) NOT NULL,
  allocated_proceeds NUMERIC(36, 18) NOT NULL,
  allocated_buy_fee NUMERIC(36, 18) NOT NULL,
  allocated_sell_fee NUMERIC(36, 18) NOT NULL,
  realized_pnl NUMERIC(36, 18) NOT NULL,
  realized_pnl_pct NUMERIC(18, 6),
  holding_seconds INTEGER NOT NULL DEFAULT 0,
  opened_at TIMESTAMPTZ NOT NULL,
  closed_at TIMESTAMPTZ NOT NULL,
  execution_origin TEXT NOT NULL,
  strategy_name TEXT,
  strategy_version TEXT,
  exit_reason TEXT
);

CREATE TABLE IF NOT EXISTS analytics_sync_jobs (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  account_id TEXT NOT NULL,
  period_start TIMESTAMPTZ,
  period_end TIMESTAMPTZ,
  cursor TEXT,
  status TEXT NOT NULL,
  records_fetched INTEGER NOT NULL DEFAULT 0,
  records_inserted INTEGER NOT NULL DEFAULT 0,
  records_updated INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  started_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS analytics_reconciliation_issues (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  issue_type TEXT NOT NULL,
  market TEXT,
  reference_id TEXT,
  description TEXT NOT NULL,
  payload_json JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'OPEN',
  created_at TIMESTAMPTZ NOT NULL,
  resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS asset_cost_baselines (
  account_id TEXT NOT NULL,
  market TEXT NOT NULL,
  initial_quantity NUMERIC(36, 18) NOT NULL,
  avg_cost_krw NUMERIC(36, 18) NOT NULL,
  note TEXT,
  updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY(account_id, market)
);

CREATE TABLE IF NOT EXISTS reversion_positions (
  id TEXT PRIMARY KEY,
  policy TEXT NOT NULL,
  symbol TEXT NOT NULL,
  status TEXT NOT NULL,
  quantity NUMERIC(36, 18) NOT NULL,
  entry_price NUMERIC(36, 18) NOT NULL,
  cost NUMERIC(36, 18) NOT NULL,
  net_pnl NUMERIC(36, 18) NOT NULL DEFAULT 0,
  exit_reason TEXT,
  opened_at TIMESTAMPTZ NOT NULL,
  closed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS position_exit_policy (
  position_id TEXT PRIMARY KEY,
  strategy_name TEXT NOT NULL,
  version TEXT NOT NULL,
  stop_loss_rate NUMERIC(18, 6) NOT NULL,
  tp1_rate NUMERIC(18, 6) NOT NULL,
  tp1_ratio NUMERIC(18, 6) NOT NULL,
  tp2_rate NUMERIC(18, 6) NOT NULL,
  tp2_ratio NUMERIC(18, 6) NOT NULL,
  runner_ratio NUMERIC(18, 6) NOT NULL,
  trailing_drop NUMERIC(18, 6) NOT NULL,
  max_hold_seconds INTEGER NOT NULL,
  peak_price NUMERIC(36, 18) NOT NULL,
  protective_stop_price NUMERIC(36, 18) NOT NULL,
  state TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS position_exit_leg (
  id TEXT PRIMARY KEY,
  position_id TEXT NOT NULL,
  leg_type TEXT NOT NULL,
  client_order_id TEXT UNIQUE,
  target_quantity NUMERIC(36, 18) NOT NULL,
  filled_quantity NUMERIC(36, 18) NOT NULL DEFAULT 0,
  avg_fill_price NUMERIC(36, 18),
  realized_pnl NUMERIC(36, 18) NOT NULL DEFAULT 0,
  fees NUMERIC(36, 18) NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  reason TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS position_exit_event (
  id TEXT PRIMARY KEY,
  position_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  price NUMERIC(36, 18),
  quantity NUMERIC(36, 18),
  reason TEXT,
  details_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics_import_batches (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  filename TEXT NOT NULL,
  file_format TEXT NOT NULL,
  total_rows INTEGER NOT NULL,
  inserted_rows INTEGER NOT NULL,
  duplicate_rows INTEGER NOT NULL,
  conflict_rows INTEGER NOT NULL,
  status TEXT NOT NULL,
  errors_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS system_events (
  id BIGSERIAL PRIMARY KEY,
  type TEXT NOT NULL,
  details_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def generate_pg_ddl() -> str:
    return PG_DDL


def ensure_pg_schema(store) -> bool:
    """Idempotently create all PostgreSQL tables/indexes in PG_DDL when USE_POSTGRES=1."""
    if not store.is_postgres_enabled():
        return False
    with store.identity_connect() as conn:
        conn.executescript(PG_DDL)
    return True


def inspect_sqlite_counts(store) -> dict[str, int]:
    tables = (
        "users",
        "user_exchange_credentials",
        "exchange_orders",
        "exchange_fills",
        "trade_positions",
        "trade_lot_allocations",
        "reversion_positions",
        "paper_trades",
        "system_events",
    )
    counts: dict[str, int] = {}
    with store.connect() as conn:
        for t in tables:
            try:
                counts[t] = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            except Exception:
                counts[t] = 0
    return counts


def export_pg_ddl_file(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(PG_DDL, encoding="utf-8")
    return target


def _sql_text(val) -> str:
    if val is None:
        return "NULL"
    return "'" + str(val).replace("'", "''") + "'"


def _sql_num(val, allow_null: bool = False) -> str:
    if val is None or str(val).strip() == "":
        return "NULL" if allow_null else "0"
    return str(val).strip()


def _sql_jsonb(val) -> str:
    raw = "{}" if val is None or str(val).strip() == "" else str(val)
    return _sql_text(raw) + "::jsonb"


def _derive_user_id(account_id: str | None) -> str | None:
    if account_id and account_id.startswith("member:"):
        uid = account_id.split(":", 1)[1].strip()
        return uid or None
    return None


def export_sqlite_to_pg_sql(store) -> tuple[str, dict[str, int]]:
    """Export multi-tenant SQLite rows into an idempotent PostgreSQL migration script."""
    lines = [PG_DDL.strip(), "", "BEGIN;", ""]
    counts: dict[str, int] = {}

    with store.connect() as conn:
        user_rows = conn.execute(
            "SELECT id, email, password_hash, role, totp_secret, mfa_enabled, created_at FROM users"
        ).fetchall()
        counts["users"] = len(user_rows)
        for r in user_rows:
            mfa = "TRUE" if r["mfa_enabled"] else "FALSE"
            lines.append(
                "INSERT INTO users (id, email, password_hash, role, totp_secret, mfa_enabled, created_at) VALUES ("
                f"{_sql_text(r['id'])}, {_sql_text(r['email'])}, {_sql_text(r['password_hash'])}, "
                f"{_sql_text(r['role'])}, {_sql_text(r['totp_secret'])}, {mfa}, {_sql_text(r['created_at'])}"
                ") ON CONFLICT DO NOTHING;"
            )

        cred_rows = conn.execute(
            "SELECT id, user_id, exchange, label, access_key_enc, secret_key_enc, key_hint, created_at FROM user_exchange_credentials"
        ).fetchall()
        counts["user_exchange_credentials"] = len(cred_rows)
        for r in cred_rows:
            lines.append(
                "INSERT INTO user_exchange_credentials (id, user_id, exchange, label, access_key_enc, secret_key_enc, key_hint, created_at) VALUES ("
                f"{_sql_text(r['id'])}, {_sql_text(r['user_id'])}, {_sql_text(r['exchange'])}, {_sql_text(r['label'])}, "
                f"{_sql_text(r['access_key_enc'])}, {_sql_text(r['secret_key_enc'])}, {_sql_text(r['key_hint'])}, {_sql_text(r['created_at'])}"
                ") ON CONFLICT DO NOTHING;"
            )

        ord_rows = conn.execute(
            "SELECT id, exchange, account_id, exchange_order_id, client_order_id, market, side, order_type, "
            "order_price, order_quantity, executed_quantity, remaining_quantity, executed_amount, status, "
            "execution_origin, data_source, ordered_at, updated_at, raw_payload FROM exchange_orders"
        ).fetchall()
        counts["exchange_orders"] = len(ord_rows)
        for r in ord_rows:
            uid = _derive_user_id(r["account_id"])
            lines.append(
                "INSERT INTO exchange_orders (id, user_id, exchange, account_id, exchange_order_id, client_order_id, market, side, order_type, "
                "order_price, order_quantity, executed_quantity, remaining_quantity, executed_amount, status, execution_origin, data_source, ordered_at, updated_at, raw_payload) VALUES ("
                f"{_sql_text(r['id'])}, {_sql_text(uid)}, {_sql_text(r['exchange'])}, {_sql_text(r['account_id'])}, "
                f"{_sql_text(r['exchange_order_id'])}, {_sql_text(r['client_order_id'])}, {_sql_text(r['market'])}, {_sql_text(r['side'])}, {_sql_text(r['order_type'])}, "
                f"{_sql_num(r['order_price'], True)}, {_sql_num(r['order_quantity'], True)}, {_sql_num(r['executed_quantity'])}, "
                f"{_sql_num(r['remaining_quantity'])}, {_sql_num(r['executed_amount'])}, {_sql_text(r['status'])}, "
                f"{_sql_text(r['execution_origin'])}, {_sql_text(r['data_source'])}, {_sql_text(r['ordered_at'])}, "
                f"{_sql_text(r['updated_at'])}, {_sql_jsonb(r['raw_payload'])}"
                ") ON CONFLICT DO NOTHING;"
            )

        fill_rows = conn.execute(
            "SELECT id, exchange, account_id, exchange_fill_id, exchange_order_id, client_order_id, market, side, "
            "fill_price, fill_quantity, fill_amount, fee_amount, fee_currency, fee_krw_equiv, execution_origin, "
            "data_source, source_hash, filled_at, raw_payload FROM exchange_fills"
        ).fetchall()
        counts["exchange_fills"] = len(fill_rows)
        for r in fill_rows:
            uid = _derive_user_id(r["account_id"])
            lines.append(
                "INSERT INTO exchange_fills (id, user_id, exchange, account_id, exchange_fill_id, exchange_order_id, client_order_id, market, side, "
                "fill_price, fill_quantity, fill_amount, fee_amount, fee_currency, fee_krw_equiv, execution_origin, data_source, source_hash, filled_at, raw_payload) VALUES ("
                f"{_sql_text(r['id'])}, {_sql_text(uid)}, {_sql_text(r['exchange'])}, {_sql_text(r['account_id'])}, "
                f"{_sql_text(r['exchange_fill_id'])}, {_sql_text(r['exchange_order_id'])}, {_sql_text(r['client_order_id'])}, {_sql_text(r['market'])}, {_sql_text(r['side'])}, "
                f"{_sql_num(r['fill_price'])}, {_sql_num(r['fill_quantity'])}, {_sql_num(r['fill_amount'])}, {_sql_num(r['fee_amount'])}, "
                f"{_sql_text(r['fee_currency'])}, {_sql_num(r['fee_krw_equiv'])}, {_sql_text(r['execution_origin'])}, "
                f"{_sql_text(r['data_source'])}, {_sql_text(r['source_hash'])}, {_sql_text(r['filled_at'])}, {_sql_jsonb(r['raw_payload'])}"
                ") ON CONFLICT DO NOTHING;"
            )

        pos_rows = conn.execute(
            "SELECT id, account_id, market, position_source, strategy_name, strategy_version, opened_at, closed_at, "
            "acquired_quantity, disposed_quantity, remaining_quantity, cost_basis, realized_pnl, realized_pnl_pct, "
            "fee_total, status, calculation_version FROM trade_positions"
        ).fetchall()
        counts["trade_positions"] = len(pos_rows)
        for r in pos_rows:
            uid = _derive_user_id(r["account_id"])
            lines.append(
                "INSERT INTO trade_positions (id, user_id, account_id, market, position_source, strategy_name, strategy_version, opened_at, closed_at, "
                "acquired_quantity, disposed_quantity, remaining_quantity, cost_basis, realized_pnl, realized_pnl_pct, fee_total, status, calculation_version) VALUES ("
                f"{_sql_text(r['id'])}, {_sql_text(uid)}, {_sql_text(r['account_id'])}, {_sql_text(r['market'])}, {_sql_text(r['position_source'])}, "
                f"{_sql_text(r['strategy_name'])}, {_sql_text(r['strategy_version'])}, {_sql_text(r['opened_at'])}, {_sql_text(r['closed_at'])}, "
                f"{_sql_num(r['acquired_quantity'])}, {_sql_num(r['disposed_quantity'])}, {_sql_num(r['remaining_quantity'])}, "
                f"{_sql_num(r['cost_basis'])}, {_sql_num(r['realized_pnl'])}, {_sql_num(r['realized_pnl_pct'], True)}, "
                f"{_sql_num(r['fee_total'])}, {_sql_text(r['status'])}, {_sql_text(r['calculation_version'])}"
                ") ON CONFLICT DO NOTHING;"
            )

        lot_rows = conn.execute(
            "SELECT id, position_id, buy_fill_id, sell_fill_id, market, allocated_quantity, allocated_cost, "
            "allocated_proceeds, allocated_buy_fee, allocated_sell_fee, realized_pnl, realized_pnl_pct, "
            "holding_seconds, opened_at, closed_at, execution_origin, strategy_name, strategy_version, exit_reason FROM trade_lot_allocations"
        ).fetchall()
        counts["trade_lot_allocations"] = len(lot_rows)
        for r in lot_rows:
            lines.append(
                "INSERT INTO trade_lot_allocations (id, position_id, buy_fill_id, sell_fill_id, market, allocated_quantity, allocated_cost, "
                "allocated_proceeds, allocated_buy_fee, allocated_sell_fee, realized_pnl, realized_pnl_pct, holding_seconds, opened_at, closed_at, execution_origin, strategy_name, strategy_version, exit_reason) VALUES ("
                f"{_sql_text(r['id'])}, {_sql_text(r['position_id'])}, {_sql_text(r['buy_fill_id'])}, {_sql_text(r['sell_fill_id'])}, {_sql_text(r['market'])}, "
                f"{_sql_num(r['allocated_quantity'])}, {_sql_num(r['allocated_cost'])}, {_sql_num(r['allocated_proceeds'])}, "
                f"{_sql_num(r['allocated_buy_fee'])}, {_sql_num(r['allocated_sell_fee'])}, {_sql_num(r['realized_pnl'])}, "
                f"{_sql_num(r['realized_pnl_pct'], True)}, {int(r['holding_seconds'] or 0)}, {_sql_text(r['opened_at'])}, "
                f"{_sql_text(r['closed_at'])}, {_sql_text(r['execution_origin'])}, {_sql_text(r['strategy_name'])}, "
                f"{_sql_text(r['strategy_version'])}, {_sql_text(r['exit_reason'])}"
                ") ON CONFLICT DO NOTHING;"
            )

        base_rows = conn.execute(
            "SELECT account_id, market, initial_quantity, avg_cost_krw, note, updated_at FROM asset_cost_baselines"
        ).fetchall()
        counts["asset_cost_baselines"] = len(base_rows)
        for r in base_rows:
            lines.append(
                "INSERT INTO asset_cost_baselines (account_id, market, initial_quantity, avg_cost_krw, note, updated_at) VALUES ("
                f"{_sql_text(r['account_id'])}, {_sql_text(r['market'])}, {_sql_num(r['initial_quantity'])}, "
                f"{_sql_num(r['avg_cost_krw'])}, {_sql_text(r['note'])}, {_sql_text(r['updated_at'])}"
                ") ON CONFLICT (account_id, market) DO NOTHING;"
            )

        rev_rows = conn.execute(
            "SELECT id, policy, symbol, status, quantity, entry_price, cost, net_pnl, exit_reason, opened_at, closed_at FROM reversion_positions"
        ).fetchall()
        counts["reversion_positions"] = len(rev_rows)
        for r in rev_rows:
            lines.append(
                "INSERT INTO reversion_positions (id, policy, symbol, status, quantity, entry_price, cost, net_pnl, exit_reason, opened_at, closed_at) VALUES ("
                f"{_sql_text(r['id'])}, {_sql_text(r['policy'])}, {_sql_text(r['symbol'])}, {_sql_text(r['status'])}, "
                f"{_sql_num(r['quantity'])}, {_sql_num(r['entry_price'])}, {_sql_num(r['cost'])}, {_sql_num(r['net_pnl'])}, "
                f"{_sql_text(r['exit_reason'])}, {_sql_text(r['opened_at'])}, {_sql_text(r['closed_at'])}"
                ") ON CONFLICT DO NOTHING;"
            )

        ev_rows = conn.execute(
            "SELECT id, type, details_json, created_at FROM system_events ORDER BY id ASC"
        ).fetchall()
        counts["system_events"] = len(ev_rows)
        for r in ev_rows:
            lines.append(
                "INSERT INTO system_events (id, type, details_json, created_at) VALUES ("
                f"{int(r['id'])}, {_sql_text(r['type'])}, {_sql_jsonb(r['details_json'])}, {_sql_text(r['created_at'])}"
                ") ON CONFLICT DO NOTHING;"
            )
        if ev_rows:
            lines.append(
                "SELECT setval(pg_get_serial_sequence('system_events', 'id'), COALESCE((SELECT MAX(id) FROM system_events), 1), true);"
            )

        for table_sql, tbl_name in (
            ("SELECT token_hash, user_id, username, role, ip, expires_at, created_at FROM user_sessions", "user_sessions"),
            ("SELECT token_hash, username, ip, expires_at, created_at FROM admin_sessions", "admin_sessions"),
        ):
            try:
                srows = conn.execute(table_sql).fetchall()
            except Exception:
                srows = []
            counts[tbl_name] = len(srows)
            for r in srows:
                if tbl_name == "user_sessions":
                    lines.append(
                        "INSERT INTO user_sessions (token_hash, user_id, username, role, ip, expires_at, created_at) VALUES ("
                        f"{_sql_text(r['token_hash'])}, {_sql_text(r['user_id'])}, {_sql_text(r['username'])}, "
                        f"{_sql_text(r['role'])}, {_sql_text(r['ip'])}, {_sql_text(r['expires_at'])}, {_sql_text(r['created_at'])}"
                        ") ON CONFLICT DO NOTHING;"
                    )
                else:
                    lines.append(
                        "INSERT INTO admin_sessions (token_hash, username, ip, expires_at, created_at) VALUES ("
                        f"{_sql_text(r['token_hash'])}, {_sql_text(r['username'])}, {_sql_text(r['ip'])}, "
                        f"{_sql_text(r['expires_at'])}, {_sql_text(r['created_at'])}"
                        ") ON CONFLICT DO NOTHING;"
                    )

        try:
            job_rows = conn.execute(
                "SELECT id, source, account_id, period_start, period_end, cursor, status, records_fetched, records_inserted, records_updated, error_message, started_at, completed_at FROM analytics_sync_jobs"
            ).fetchall()
        except Exception:
            job_rows = []
        counts["analytics_sync_jobs"] = len(job_rows)
        for r in job_rows:
            lines.append(
                "INSERT INTO analytics_sync_jobs (id, source, account_id, period_start, period_end, cursor, status, records_fetched, records_inserted, records_updated, error_message, started_at, completed_at) VALUES ("
                f"{_sql_text(r['id'])}, {_sql_text(r['source'])}, {_sql_text(r['account_id'])}, {_sql_text(r['period_start'])}, "
                f"{_sql_text(r['period_end'])}, {_sql_text(r['cursor'])}, {_sql_text(r['status'])}, {int(r['records_fetched'] or 0)}, "
                f"{int(r['records_inserted'] or 0)}, {int(r['records_updated'] or 0)}, {_sql_text(r['error_message'])}, "
                f"{_sql_text(r['started_at'])}, {_sql_text(r['completed_at'])}"
                ") ON CONFLICT DO NOTHING;"
            )

    lines.extend(["", "COMMIT;", ""])
    return "\n".join(lines), counts


def check_pg_connection_and_parity(store, dsn: str | None = None) -> dict:
    """Inspect DATABASE_URL PostgreSQL connectivity and compare table row counts vs SQLite."""
    import os

    target_dsn = (dsn or os.environ.get("DATABASE_URL") or "").strip()
    sqlite_counts = inspect_sqlite_counts(store)
    use_pg = os.environ.get("USE_POSTGRES") == "1" and bool(target_dsn)
    result = {
        "configured": bool(target_dsn),
        "identity_mode": "POSTGRES_IDENTITY" if use_pg else "SQLITE",
        "audit_mode": "POSTGRES_AUDIT" if use_pg else "SQLITE",
        "analytics_mode": "POSTGRES_ANALYTICS" if use_pg else "SQLITE",
        "driver_available": False,
        "connected": False,
        "pg_version": None,
        "sqlite_counts": sqlite_counts,
        "pg_counts": {},
        "parity_ok": False,
        "error": None,
    }
    try:
        import psycopg  # type: ignore

        result["driver_available"] = True
    except ImportError:
        result["error"] = "psycopg 드라이버가 설치되지 않았습니다 (pip install psycopg[binary])."
        return result

    if not target_dsn:
        result["error"] = "DATABASE_URL 환경변수가 설정되지 않았습니다."
        return result

    pg_tables = (
        "users",
        "user_sessions",
        "admin_sessions",
        "user_exchange_credentials",
        "exchange_orders",
        "exchange_fills",
        "trade_positions",
        "trade_lot_allocations",
        "analytics_sync_jobs",
        "reversion_positions",
        "system_events",
    )
    try:
        with psycopg.connect(target_dsn, connect_timeout=4) as pg_conn:
            with pg_conn.cursor() as cur:
                cur.execute("SELECT version()")
                ver_row = cur.fetchone()
                result["pg_version"] = ver_row[0] if ver_row else None
                result["connected"] = True
                pg_counts: dict[str, int] = {}
                for tbl in pg_tables:
                    try:
                        cur.execute(f"SELECT count(*) FROM {tbl}")
                        pg_counts[tbl] = int(cur.fetchone()[0])
                    except Exception:
                        pg_conn.rollback()
                        pg_counts[tbl] = -1
                result["pg_counts"] = pg_counts
                result["parity_ok"] = all(
                    pg_counts.get(t, -1) == sqlite_counts.get(t, 0) for t in pg_tables
                )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def apply_sqlite_to_pg(store, dsn: str | None = None) -> dict:
    """Apply PG_DDL and idempotent SQLite row export directly to PostgreSQL."""
    import os

    target_dsn = (dsn or os.environ.get("DATABASE_URL") or "").strip()
    if not target_dsn:
        return {"applied": False, "error": "DATABASE_URL 환경변수가 설정되지 않았습니다."}
    try:
        import psycopg  # type: ignore
    except ImportError:
        return {
            "applied": False,
            "error": "psycopg 드라이버가 설치되지 않았습니다 (pip install psycopg[binary]).",
        }

    sql_text, counts = export_sqlite_to_pg_sql(store)
    try:
        with psycopg.connect(target_dsn, connect_timeout=10) as pg_conn:
            with pg_conn.cursor() as cur:
                cur.execute(sql_text)
            pg_conn.commit()
    except Exception as exc:
        return {"applied": False, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "applied": True,
        "exported_counts": counts,
        "status": check_pg_connection_and_parity(store, target_dsn),
    }
