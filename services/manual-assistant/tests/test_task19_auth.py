from fastapi.testclient import TestClient

from manual_assistant.app import create_app

ORIGIN = "http://127.0.0.1:13000"
HEADERS = {"origin": ORIGIN, "x-bsb-local": "1"}


def test_public_apis_open_and_admin_apis_blocked_without_session(tmp_path):
    app = create_app(tmp_path / "t19.sqlite3", enforce_admin_auth=True)
    with TestClient(app, base_url=ORIGIN) as client:
        # 1. Public endpoints work without auth and leak no sensitive fields
        for ep in (
            "/api/public/market/summary",
            "/api/public/market/tickers",
            "/api/public/strategies/overview",
            "/api/public/performance/summary",
            "/api/public/platform/status",
        ):
            r = client.get(ep)
            assert r.status_code == 200
            txt = r.text.lower()
            assert "access_key" not in txt
            assert "secret_key" not in txt
            assert "private_accounts" not in txt

        # 2. Admin / trading endpoints MUST reject Origin + X-BSB-Local without session
        for admin_ep in (
            "/api/settings",
            "/api/reversion/config",
            "/api/private/status",
            "/api/orders/status",
            "/api/analytics/summary",
            "/api/admin/strategy-reports",
        ):
            r = client.get(admin_ep, headers=HEADERS)
            assert r.status_code == 401

        r_post = client.post("/api/orders/submit", headers=HEADERS, json={})
        assert r_post.status_code == 401


def test_operator_login_logout_and_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("BSB_AUTH_USER", "admin")
    monkeypatch.setenv("BSB_AUTH_PASSWORD", "test-secret-pass")
    app = create_app(tmp_path / "t19_auth.sqlite3", enforce_admin_auth=True)
    with TestClient(app, base_url=ORIGIN) as client:
        # Wrong password 5 times -> 6th returns 429
        for _ in range(5):
            r = client.post(
                "/api/auth/login",
                headers=HEADERS,
                json={"username": "admin", "password": "wrong"},
            )
            assert r.status_code == 401
        r_limit = client.post(
            "/api/auth/login",
            headers=HEADERS,
            json={"username": "admin", "password": "wrong"},
        )
        assert r_limit.status_code == 429

        # Clear rate limit for test IP and login with valid password
        app.state.admin_auth.failed_attempts.clear()
        ok = client.post(
            "/api/auth/login",
            headers=HEADERS,
            json={"username": "admin", "password": "test-secret-pass"},
        )
        assert ok.status_code == 200
        token = ok.json()["token"]

        # Admin endpoint now succeeds with session token
        authed_headers = {**HEADERS, "x-bsb-admin-session": token}
        assert client.get("/api/settings", headers=authed_headers).status_code == 200

        # Logout revokes session
        assert client.post("/api/auth/logout", headers=authed_headers, json={}).status_code == 200
        assert client.get("/api/settings", headers=authed_headers).status_code == 401
