def migrate(conn):
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 5:
        return
    conn.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE auto_signal_claims(account_id TEXT NOT NULL,signal_id TEXT NOT NULL,order_intent_id TEXT,status TEXT NOT NULL,created_at TEXT NOT NULL,message TEXT,PRIMARY KEY(account_id,signal_id));
        CREATE UNIQUE INDEX auto_order_intent ON auto_signal_claims(order_intent_id) WHERE order_intent_id IS NOT NULL;
        CREATE TABLE automation_events(id INTEGER PRIMARY KEY,account_id TEXT,kind TEXT NOT NULL,message TEXT NOT NULL,signal_id TEXT,created_at TEXT NOT NULL);
        CREATE INDEX automation_events_account ON automation_events(account_id,created_at,id);
        CREATE INDEX history_signals ON signals(generated_at,id);
        CREATE INDEX history_private_fills ON private_fills(account_id,received_at,trade_id);
        PRAGMA user_version=5;
        COMMIT;
    """)
