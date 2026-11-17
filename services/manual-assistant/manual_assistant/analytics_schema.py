"""Schema migration for Task 18 Unified Trading Analytics."""


def migrate_analytics(conn):
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS exchange_orders(
        id TEXT PRIMARY KEY,
        exchange TEXT NOT NULL,
        account_id TEXT NOT NULL,
        exchange_order_id TEXT NOT NULL,
        client_order_id TEXT,
        market TEXT NOT NULL,
        side TEXT NOT NULL,
        order_type TEXT NOT NULL,
        order_price TEXT,
        order_quantity TEXT,
        executed_quantity TEXT NOT NULL DEFAULT '0',
        remaining_quantity TEXT NOT NULL DEFAULT '0',
        executed_amount TEXT NOT NULL DEFAULT '0',
        status TEXT NOT NULL,
        execution_origin TEXT NOT NULL DEFAULT 'UNKNOWN',
        data_source TEXT NOT NULL DEFAULT 'BITHUMB_API',
        ordered_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        raw_payload TEXT NOT NULL,
        UNIQUE(exchange, account_id, exchange_order_id)
      );
      CREATE INDEX IF NOT EXISTS idx_ex_orders_client ON exchange_orders(client_order_id);
      CREATE INDEX IF NOT EXISTS idx_ex_orders_market ON exchange_orders(account_id, market, ordered_at);

      CREATE TABLE IF NOT EXISTS exchange_fills(
        id TEXT PRIMARY KEY,
        exchange TEXT NOT NULL,
        account_id TEXT NOT NULL,
        exchange_fill_id TEXT,
        exchange_order_id TEXT,
        client_order_id TEXT,
        market TEXT NOT NULL,
        side TEXT NOT NULL,
        fill_price TEXT NOT NULL,
        fill_quantity TEXT NOT NULL,
        fill_amount TEXT NOT NULL,
        fee_amount TEXT NOT NULL DEFAULT '0',
        fee_currency TEXT NOT NULL DEFAULT 'KRW',
        fee_krw_equiv TEXT NOT NULL DEFAULT '0',
        fee_eval_note TEXT,
        execution_origin TEXT NOT NULL DEFAULT 'UNKNOWN',
        data_source TEXT NOT NULL DEFAULT 'BITHUMB_API',
        source_hash TEXT,
        filled_at TEXT NOT NULL,
        raw_payload TEXT NOT NULL
      );
      CREATE UNIQUE INDEX IF NOT EXISTS idx_ex_fills_unique_id
        ON exchange_fills(account_id, exchange_fill_id)
        WHERE exchange_fill_id IS NOT NULL AND exchange_fill_id != '';
      CREATE UNIQUE INDEX IF NOT EXISTS idx_ex_fills_source_hash
        ON exchange_fills(account_id, source_hash)
        WHERE source_hash IS NOT NULL AND source_hash != '';
      CREATE INDEX IF NOT EXISTS idx_ex_fills_time
        ON exchange_fills(account_id, market, filled_at);

      CREATE TABLE IF NOT EXISTS trading_strategy_events(
        id TEXT PRIMARY KEY,
        bot_order_id TEXT,
        exchange_order_id TEXT,
        client_order_id TEXT,
        strategy_name TEXT NOT NULL,
        strategy_version TEXT NOT NULL,
        signal_type TEXT NOT NULL,
        signal_score TEXT,
        decision_reason TEXT,
        market_snapshot TEXT,
        stop_loss_price TEXT,
        take_profit_price TEXT,
        trailing_stop_price TEXT,
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_strat_ev_client ON trading_strategy_events(client_order_id);

      CREATE TABLE IF NOT EXISTS trade_positions(
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        market TEXT NOT NULL,
        position_source TEXT NOT NULL,
        strategy_name TEXT,
        strategy_version TEXT,
        opened_at TEXT NOT NULL,
        closed_at TEXT,
        acquired_quantity TEXT NOT NULL,
        disposed_quantity TEXT NOT NULL,
        remaining_quantity TEXT NOT NULL,
        cost_basis TEXT NOT NULL,
        realized_pnl TEXT NOT NULL,
        realized_pnl_pct TEXT,
        fee_total TEXT NOT NULL,
        status TEXT NOT NULL,
        calculation_version TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_trade_pos_acc ON trade_positions(account_id, market, opened_at);

      CREATE TABLE IF NOT EXISTS trade_lot_allocations(
        id TEXT PRIMARY KEY,
        position_id TEXT NOT NULL,
        buy_fill_id TEXT NOT NULL,
        sell_fill_id TEXT NOT NULL,
        market TEXT NOT NULL,
        allocated_quantity TEXT NOT NULL,
        allocated_cost TEXT NOT NULL,
        allocated_proceeds TEXT NOT NULL,
        allocated_buy_fee TEXT NOT NULL,
        allocated_sell_fee TEXT NOT NULL,
        realized_pnl TEXT NOT NULL,
        realized_pnl_pct TEXT,
        holding_seconds INTEGER NOT NULL DEFAULT 0,
        opened_at TEXT NOT NULL,
        closed_at TEXT NOT NULL,
        execution_origin TEXT NOT NULL,
        strategy_name TEXT,
        strategy_version TEXT,
        exit_reason TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_lot_alloc_closed ON trade_lot_allocations(closed_at);

      CREATE TABLE IF NOT EXISTS analytics_sync_jobs(
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        account_id TEXT NOT NULL,
        period_start TEXT,
        period_end TEXT,
        cursor TEXT,
        status TEXT NOT NULL,
        records_fetched INTEGER NOT NULL DEFAULT 0,
        records_inserted INTEGER NOT NULL DEFAULT 0,
        records_updated INTEGER NOT NULL DEFAULT 0,
        error_message TEXT,
        started_at TEXT NOT NULL,
        completed_at TEXT
      );

      CREATE TABLE IF NOT EXISTS account_cash_flows(
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        asset TEXT NOT NULL,
        flow_type TEXT NOT NULL,
        amount TEXT NOT NULL,
        fee TEXT NOT NULL DEFAULT '0',
        occurred_at TEXT NOT NULL,
        external_reference TEXT
      );

      CREATE TABLE IF NOT EXISTS asset_cost_baselines(
        account_id TEXT NOT NULL,
        market TEXT NOT NULL,
        initial_quantity TEXT NOT NULL,
        avg_cost_krw TEXT NOT NULL,
        note TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(account_id, market)
      );

      CREATE TABLE IF NOT EXISTS analytics_import_batches(
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        file_format TEXT NOT NULL,
        total_rows INTEGER NOT NULL,
        inserted_rows INTEGER NOT NULL,
        duplicate_rows INTEGER NOT NULL,
        conflict_rows INTEGER NOT NULL,
        status TEXT NOT NULL,
        errors_json TEXT NOT NULL,
        created_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS analytics_reconciliation_issues(
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        issue_type TEXT NOT NULL,
        market TEXT,
        reference_id TEXT,
        description TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        created_at TEXT NOT NULL,
        resolved_at TEXT
      );
    """)
