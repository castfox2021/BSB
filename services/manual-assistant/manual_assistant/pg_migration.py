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
  execution_origin TEXT NOT NULL DEFAULT 'UNKNOWN',
  data_source TEXT NOT NULL DEFAULT 'BITHUMB_API',
  source_hash TEXT,
  filled_at TIMESTAMPTZ NOT NULL,
  raw_payload JSONB NOT NULL
);

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
"""


def generate_pg_ddl() -> str:
    return PG_DDL


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
