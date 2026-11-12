def migrate(conn):
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 3:
        return
    conn.executescript("""
      BEGIN IMMEDIATE;
      CREATE TABLE private_accounts(account_id TEXT,currency TEXT,balance TEXT,locked TEXT,average_price TEXT,unit_currency TEXT,updated_at TEXT,PRIMARY KEY(account_id,currency));
      CREATE TABLE private_orders(account_id TEXT,order_id TEXT,market TEXT,side TEXT,state TEXT,executed_quantity TEXT,paid_fee TEXT,created_at TEXT,updated_at TEXT,PRIMARY KEY(account_id,order_id));
      CREATE TABLE private_fills(account_id TEXT,trade_id TEXT,order_id TEXT,symbol TEXT,side TEXT,quantity TEXT,price TEXT,fee_krw TEXT,fee_status TEXT,filled_at TEXT,received_at TEXT,source TEXT,PRIMARY KEY(account_id,trade_id));
      CREATE TABLE private_notes(account_id TEXT,trade_id TEXT,notes TEXT,PRIMARY KEY(account_id,trade_id));
      CREATE TABLE private_state(account_id TEXT PRIMARY KEY,reconciled INTEGER NOT NULL DEFAULT 0,last_rest TEXT);
      CREATE TABLE trading_control(id INTEGER PRIMARY KEY CHECK(id=1),mode TEXT,state TEXT,updated_at TEXT);
      INSERT INTO trading_control VALUES(1,'MANUAL_APPROVAL','STOPPED',datetime('now'));
      PRAGMA user_version=3;
      COMMIT;
    """)
