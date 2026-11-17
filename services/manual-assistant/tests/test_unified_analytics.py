import base64
import io
import json
import zipfile
from decimal import Decimal as D
from fastapi.testclient import TestClient

from manual_assistant.app import create_app

ORIGIN = "http://127.0.0.1:13000"
HEADERS = {"origin": ORIGIN, "x-bsb-local": "1"}


def _make_xlsx_bytes(rows: list[list[str]]) -> bytes:
    from manual_assistant.analytics_ledger import _build_minimal_xlsx

    return _build_minimal_xlsx(rows[0], rows[1:])


def test_unified_ledger_dedupe_partial_fills_and_moving_average(tmp_path):
    app = create_app(tmp_path / "ana1.sqlite3")
    with TestClient(app, base_url=ORIGIN) as client:
        with app.state.storage.connect() as conn:
            # Bot order recorded in system_events
            conn.execute(
                "INSERT INTO system_events(type,details_json,created_at) VALUES (?,?,?)",
                ("auto_buy_submitted", json.dumps({"uuid": "ord-bot-1"}), "2026-11-17T01:00:00+00:00"),
            )
            # Duplicate private_fills + multiple buys & partial sells
            conn.execute(
                "INSERT INTO private_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("default", "t1", "ord-bot-1", "KRW-XPR", "bid", "100", "100", "4", "KRW", "2026-11-17T01:00:05+00:00", "2026-11-17T01:00:06+00:00", "REST"),
            )
            conn.execute(
                "INSERT INTO private_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("default", "t2", "ord-bot-1", "KRW-XPR", "bid", "100", "120", "4.8", "KRW", "2026-11-17T01:01:05+00:00", "2026-11-17T01:01:06+00:00", "REST"),
            )
            # Manual sell of 150 out of 200 at 150 KRW
            conn.execute(
                "INSERT INTO private_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("default", "t3", "ord-man-1", "KRW-XPR", "ask", "150", "150", "9", "KRW", "2026-11-17T01:10:05+00:00", "2026-11-17T01:10:06+00:00", "REST"),
            )

        res = client.post("/api/analytics/sync", headers=HEADERS, json={})
        assert res.status_code == 200
        # Sync again -> no duplicates
        res2 = client.post("/api/analytics/sync", headers=HEADERS, json={})
        assert res2.status_code == 200

        summary = client.get("/api/analytics/summary?mode=LIVE", headers=HEADERS).json()
        s = summary["summary"]
        assert s["trade_count"] == 1
        assert s["open_positions_count"] == 1
        assert D(s["realized_pnl_krw"]) > 0
        # Bot origin preserved
        assert summary["trades"][0]["execution_origin"] == "BOT"

        # PAPER mode remains strictly isolated (0 trades)
        paper_s = client.get("/api/analytics/summary?mode=PAPER", headers=HEADERS).json()["summary"]
        assert paper_s["trade_count"] == 0


def test_missing_cost_basis_baseline_and_csv_xlsx_import_conflict(tmp_path):
    app = create_app(tmp_path / "ana2.sqlite3")
    with TestClient(app, base_url=ORIGIN) as client:
        # 1. Import CSV with a SELL that has no prior BUY -> must raise MISSING_COST_BASIS, not invent profit
        csv_text = "거래일시,마켓,구분,체결가격,수량,체결금액,수수료,수수료통화\n2026-11-17 10:00:00,KRW-BTC,매도,100000000,0.01,1000000,400,KRW\n"
        imp = client.post(
            "/api/analytics/import",
            headers=HEADERS,
            json={
                "filename": "bithumb.csv",
                "content_base64": base64.b64encode(csv_text.encode("utf-8")).decode("ascii"),
            },
        )
        assert imp.status_code == 200
        assert imp.json()["inserted_rows"] == 1

        risk = client.get("/api/analytics/risk?mode=LIVE", headers=HEADERS).json()
        assert any(i["issue_type"] == "MISSING_COST_BASIS" for i in risk["issues"])

        # 2. User sets baseline cost -> recalculation resolves MISSING_COST_BASIS
        base_res = client.post(
            "/api/analytics/baseline",
            headers=HEADERS,
            json={"market": "KRW-BTC", "initial_quantity": "0.01", "avg_cost_krw": "90000000"},
        )
        assert base_res.status_code == 200
        assert base_res.json()["missing_cost_basis_issues"] == 0

        # 3. Import XLSX with non-KRW fee & check conflict detection against API fill
        with app.state.storage.connect() as conn:
            conn.execute(
                "INSERT INTO exchange_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("f-api-1", "bithumb", "default", "ex-1", "o-1", None, "KRW-ETH", "BUY", "4000000", "0.5", "2000000", "800", "KRW", "800", "api", "MANUAL", "BITHUMB_API", None, "2026-11-17T02:00:00+00:00", "{}"),
            )
        xlsx_bytes = _make_xlsx_bytes([
            ["체결일시", "마켓", "구분", "체결가격", "수량", "체결금액", "수수료", "수수료통화"],
            ["2026-11-17 11:00:30", "KRW-ETH", "매수", "4000000", "0.5", "2000000", "0.0002", "ETH"],
        ])
        imp_x = client.post(
            "/api/analytics/import",
            headers=HEADERS,
            json={
                "filename": "bithumb.xlsx",
                "content_base64": base64.b64encode(xlsx_bytes).decode("ascii"),
            },
        )
        assert imp_x.status_code == 200
        assert imp_x.json()["conflict_rows"] == 1

        # 4. Export CSV, XLSX, JSON, Markdown
        for fmt in ("csv", "xlsx", "json", "markdown"):
            exp = client.get(f"/api/analytics/export?format={fmt}&mode=LIVE", headers=HEADERS)
            assert exp.status_code == 200
            if fmt == "xlsx":
                with zipfile.ZipFile(io.BytesIO(exp.content)) as zf:
                    assert "xl/worksheets/sheet1.xml" in zf.namelist()


def test_query_analytics_exchange_filter(tmp_path):
    app = create_app(tmp_path / "ana_ex.sqlite3")
    with TestClient(app, base_url=ORIGIN):
        acc = "member:u-ex-1"
        with app.state.storage.identity_connect() as conn:
            conn.executemany(
                "INSERT INTO exchange_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    ("fb1", "bithumb", acc, "ex-b1", "ob1", None, "KRW-BTC", "BUY", "100000000", "0.01", "1000000", "400", "KRW", "400", "api", "MANUAL", "BITHUMB_API", None, "2026-11-17T01:00:00+00:00", "{}"),
                    ("fb2", "bithumb", acc, "ex-b2", "ob2", None, "KRW-BTC", "SELL", "110000000", "0.01", "1100000", "440", "KRW", "440", "api", "MANUAL", "BITHUMB_API", None, "2026-11-17T02:00:00+00:00", "{}"),
                    ("fu1", "upbit", acc, "ex-u1", "ou1", None, "KRW-ETH", "BUY", "4000000", "1", "4000000", "2000", "KRW", "2000", "api", "MANUAL", "UPBIT_API", None, "2026-11-17T03:00:00+00:00", "{}"),
                    ("fu2", "upbit", acc, "ex-u2", "ou2", None, "KRW-ETH", "SELL", "3900000", "1", "3900000", "1950", "KRW", "1950", "api", "MANUAL", "UPBIT_API", None, "2026-11-17T04:00:00+00:00", "{}"),
                ],
            )
        app.state.analytics_ledger.recalculate(acc)

        all_res = app.state.analytics_ledger.query_analytics({"mode": "LIVE", "period": "all", "account_id": acc, "exchange": "ALL"})
        bt_res = app.state.analytics_ledger.query_analytics({"mode": "LIVE", "period": "all", "account_id": acc, "exchange": "BITHUMB"})
        up_res = app.state.analytics_ledger.query_analytics({"mode": "LIVE", "period": "all", "account_id": acc, "exchange": "UPBIT"})

        assert all_res["summary"]["trade_count"] == 2
        assert bt_res["summary"]["trade_count"] == 1
        assert bt_res["trades"][0]["market"] == "KRW-BTC"
        assert bt_res["trades"][0]["exchange"] == "bithumb"
        assert D(bt_res["summary"]["realized_pnl_krw"]) > 0
        assert up_res["summary"]["trade_count"] == 1
        assert up_res["trades"][0]["market"] == "KRW-ETH"
        assert up_res["trades"][0]["exchange"] == "upbit"
        assert D(up_res["summary"]["realized_pnl_krw"]) < 0
