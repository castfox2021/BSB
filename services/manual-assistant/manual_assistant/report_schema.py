"""Schema migration for Task 18 AI Strategy Analysis Report Bridge."""


def migrate_reports(conn):
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS strategy_reports(
        report_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        mode TEXT NOT NULL,
        filters_json TEXT NOT NULL,
        summary_json TEXT NOT NULL,
        data_quality_json TEXT NOT NULL,
        manifest_json TEXT NOT NULL,
        zip_path TEXT NOT NULL,
        file_size_bytes INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        error TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_strategy_reports_created ON strategy_reports(created_at DESC);
    """)
