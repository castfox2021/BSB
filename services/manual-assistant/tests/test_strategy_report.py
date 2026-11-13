import hashlib
import io
import json
import zipfile
from decimal import Decimal as D
from fastapi.testclient import TestClient

from manual_assistant.app import create_app
from manual_assistant.strategy_report import _csv_safe

ORIGIN = "http://127.0.0.1:13000"
HEADERS = {"origin": ORIGIN, "x-bsb-local": "1"}


def test_csv_injection_and_numeric_safety():
    assert _csv_safe("=CMD|'/C calc'!A0") == "'=CMD|'/C calc'!A0"
    assert _csv_safe("+1+1") == "'+1+1"
    assert _csv_safe("@SUM(A1)") == "'@SUM(A1)"
    assert _csv_safe("-12.34") == "-12.34"
    assert _csv_safe("150000") == "150000"


def test_empty_report_generation_zip_manifest_and_auth_guard(tmp_path):
    app = create_app(tmp_path / "rep0.sqlite3")
    with TestClient(app, base_url=ORIGIN) as client:
        # Unauthenticated / bad origin blocked
        bad = client.post(
            "/api/admin/strategy-reports",
            headers={"origin": "https://evil.example", "x-bsb-local": "1"},
            json={"mode": "PAPER"},
        )
        assert bad.status_code in (400, 403)

        # Create empty report
        res = client.post(
            "/api/admin/strategy-reports",
            headers=HEADERS,
            json={"mode": "PAPER"},
        )
        assert res.status_code == 200
        body = res.json()
        rid = body["report_id"]
        assert body["status"] == "READY"
        assert body["summary"]["trade_count"] == 0

        # Download ZIP and verify manifest SHA-256
        dl = client.get(f"/api/admin/strategy-reports/{rid}/download", headers=HEADERS)
        assert dl.status_code == 200
        assert dl.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(dl.content)) as zf:
            names = set(zf.namelist())
            expected = {
                "manifest.json",
                "summary.json",
                "summary.md",
                "trades.csv",
                "open_positions.csv",
                "signals.csv",
                "orders.csv",
                "equity_curve.csv",
                "market_context.csv",
                "strategy_config.json",
                "data_quality.json",
                "README.md",
            }
            assert expected.issubset(names)
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["schema_version"] == "bithumb-ai-report/1.0"
            for item in manifest["files"]:
                assert hashlib.sha256(zf.read(item["name"])).hexdigest() == item["sha256"]

        # Path traversal / invalid ID returns 404
        assert (
            client.get("/api/admin/strategy-reports/invalid-id/download", headers=HEADERS).status_code
            == 404
        )


def test_partial_exit_aggregation_and_paper_live_separation(tmp_path):
    app = create_app(tmp_path / "rep1.sqlite3")
    with TestClient(app, base_url=ORIGIN) as client:
        lab = app.state.engine.reversion
        market = app.state.market
        bars = [
            {
                "start": 1_800_000_000 + n * 60,
                "open": "10000",
                "high": "10100",
                "low": "9900",
                "close": "10000",
                "volume": "10",
                "complete": True,
            }
            for n in range(40)
        ]
        quote = {"bids": [["10000", "1000000"]], "asks": [["10000", "1000000"]]}
        lab._enter("KRW-TEST", D("9900"), bars, quote, lab.config(), ["-0.02", "-0.03", "-0.04"])

        # Partial TP1 + TP2 + Trailing close on partial_tp_trailing
        with app.state.engine.store.connect() as conn:
            entry = D(
                conn.execute(
                    "SELECT entry_price FROM reversion_positions WHERE policy='partial_tp_trailing'"
                ).fetchone()[0]
            )
        market.quote = lambda _s: {
            "bids": [[str((entry * D("1.03")).quantize(D("0.01"))), "1000000"]],
            "asks": [[str((entry * D("1.03")).quantize(D("0.01"))), "1000000"]],
        }
        lab._protect(lab.config())
        market.quote = lambda _s: {
            "bids": [[str((entry * D("1.01")).quantize(D("0.01"))), "1000000"]],
            "asks": [[str((entry * D("1.01")).quantize(D("0.01"))), "1000000"]],
        }
        lab._protect(lab.config())

        # Preview PAPER partial_tp_trailing -> 1 closed trade, remaining policies in open_positions
        prev = client.post(
            "/api/admin/strategy-reports/preview",
            headers=HEADERS,
            json={"mode": "PAPER", "strategy_version": "partial_tp_trailing"},
        )
        assert prev.status_code == 200
        pdata = prev.json()
        assert pdata["summary"]["trade_count"] == 1
        assert pdata["summary"]["open_count"] == 0

        # LIVE mode must remain 0 trades (no mixing with PAPER!)
        live_prev = client.post(
            "/api/admin/strategy-reports/preview",
            headers=HEADERS,
            json={"mode": "LIVE"},
        )
        assert live_prev.status_code == 200
        assert live_prev.json()["summary"]["trade_count"] == 0

        # Create ZIP and verify orders.csv has entry + TP1 + TP2 + RUNNER
        rep = client.post(
            "/api/admin/strategy-reports",
            headers=HEADERS,
            json={"mode": "PAPER", "strategy_version": "partial_tp_trailing"},
        )
        assert rep.status_code == 200
        rid = rep.json()["report_id"]
        dl = client.get(f"/api/admin/strategy-reports/{rid}/download", headers=HEADERS)
        with zipfile.ZipFile(io.BytesIO(dl.content)) as zf:
            orders_text = zf.read("orders.csv").decode("utf-8-sig")
            assert "TP1" in orders_text and "TP2" in orders_text and "RUNNER" in orders_text
            trades_text = zf.read("trades.csv").decode("utf-8-sig")
            assert len(trades_text.strip().splitlines()) == 2  # header + 1 aggregated trade

        # Strategy comparison endpoint
        comp = client.post("/api/admin/strategy-comparisons", headers=HEADERS, json={})
        assert comp.status_code == 200
        assert "policies" in comp.json()
