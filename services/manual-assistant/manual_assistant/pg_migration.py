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
                ") ON CONFLICT (id) DO NOTHING;"
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
                ") ON CONFLICT (id) DO NOTHING;"
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
                ") ON CONFLICT (id) DO NOTHING;"
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
                ") ON CONFLICT (id) DO NOTHING;"
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
                ") ON CONFLICT (id) DO NOTHING;"
            )

    lines.extend(["", "COMMIT;", ""])
    return "\n".join(lines), counts
