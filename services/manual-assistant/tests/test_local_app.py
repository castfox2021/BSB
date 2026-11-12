import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from manual_assistant.app import create_app

ORIGIN = "http://127.0.0.1:13000"
HEADERS = {"origin": ORIGIN, "x-bsb-local": "1"}


@pytest.fixture
def path(tmp_path):
    return tmp_path / "manual.sqlite3"


def client(path):
    return TestClient(
        create_app(path, market_enabled=False), base_url="http://127.0.0.1"
    )


def test_bootstrap_is_honest_about_capabilities(path):
    with client(path) as api:
        health = api.get("/api/health").json()
        assert health["mode"] == "MANUAL LIVE"
        assert health["data_status"] == "OFFLINE"
        assert health["orders_supported"] is True
        assert health["signals_enabled"] is False
        assert api.get("/api/settings").json()["initial_capital_krw"] == "150000"
        assert api.get("/api/positions").json()["account_confirmed"] is False
        assert api.get("/api/risk/status").json()["position_monitoring_enabled"] is True
        assert api.get("/api/market/candidates").json()["items"] == []
        assert api.get("/api/signals?limit=201").status_code == 422
        assert api.post("/api/manual-fills", json={}).status_code == 403


def test_settings_survive_restart_and_are_audited(path):
    with client(path) as api:
        response = api.patch(
            "/api/settings",
            json={"sound_enabled": True, "stop_loss_rate": "0.009"},
            headers=HEADERS,
        )
        assert response.status_code == 200
    with client(path) as api:
        assert api.get("/api/settings").json()["stop_loss_rate"] == "0.009"
        assert api.get("/api/settings").json()["sound_enabled"] is True
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
        row = conn.execute(
            "SELECT details_json FROM system_events WHERE type='settings_changed'"
        ).fetchone()
        assert json.loads(row[0])["before"]["stop_loss_rate"] == "0.008"
        assert json.loads(row[0])["after"]["stop_loss_rate"] == "0.009"


@pytest.mark.parametrize(
    "change",
    [
        {"max_entry_krw": "60000"},
        {"initial_capital_krw": 150000},
        {"stop_loss_rate": "NaN"},
        {"stop_loss_rate": "1"},
        {"daily_loss_alert_krw": "0"},
        {"max_positions": True},
        {"unknown": "secret"},
        {"max_holding_seconds": 1},
        {"initial_capital_krw": "15e4"},
        {"initial_capital_krw": "150000.0"},
    ],
)
def test_invalid_policy_is_atomic(path, change):
    with client(path) as api:
        before = api.get("/api/settings").json()
        assert (
            api.patch("/api/settings", json=change, headers=HEADERS).status_code == 422
        )
        assert api.get("/api/settings").json() == before
    with sqlite3.connect(path) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM system_events WHERE type='settings_changed'"
            ).fetchone()[0]
            == 0
        )


def test_local_origin_cors_and_host_checks(path):
    with client(path) as api:
        assert (
            api.patch("/api/settings", json={"sound_enabled": True}).status_code == 403
        )
        assert (
            api.patch(
                "/api/settings",
                json={"sound_enabled": True},
                headers={"origin": "https://other.example", "x-bsb-local": "1"},
            ).status_code
            == 403
        )
        assert (
            api.get("/api/health", headers={"host": "other.example"}).status_code == 400
        )
        preflight = api.options(
            "/api/settings",
            headers={
                "origin": "https://other.example",
                "access-control-request-method": "PATCH",
            },
        )
        assert preflight.status_code == 400


def test_future_db_is_not_silently_downgraded(path):
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=11")
    with pytest.raises(RuntimeError):
        with client(path):
            pass
