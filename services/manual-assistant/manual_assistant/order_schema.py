def migrate(conn):
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 4:
        return
    conn.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE manual_orders(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,market TEXT NOT NULL,side TEXT NOT NULL,budget TEXT NOT NULL,quantity TEXT NOT NULL,parameters TEXT NOT NULL,details TEXT NOT NULL,status TEXT NOT NULL,order_id TEXT,signal_id TEXT,expires_at TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,error TEXT,session TEXT);
        CREATE INDEX manual_orders_state ON manual_orders(account_id,status);
        PRAGMA user_version=4;
        COMMIT;
    """)
