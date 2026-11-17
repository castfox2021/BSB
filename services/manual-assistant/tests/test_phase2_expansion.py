from fastapi.testclient import TestClient

from manual_assistant.app import create_app
from manual_assistant.auth_phase2 import decrypt_secret, totp_code

ORIGIN = "http://127.0.0.1:13000"
HEADERS = {"origin": ORIGIN, "x-bsb-local": "1"}


def test_member_registration_rbac_isolation_and_encrypted_vault(tmp_path):
    app = create_app(tmp_path / "p2.sqlite3", enforce_admin_auth=True)
    with TestClient(app, base_url=ORIGIN) as client:
        # 1. Register Member A and Member B
        r1 = client.post(
            "/api/auth/register",
            headers=HEADERS,
            json={"email": "alice@example.com", "password": "password123"},
        )
        assert r1.status_code == 200
        assert r1.json()["role"] == "MEMBER"

        r2 = client.post(
            "/api/auth/register",
            headers=HEADERS,
            json={"email": "bob@example.com", "password": "password123"},
        )
        assert r2.status_code == 200

        # 2. Login as Alice
        login_a = client.post(
            "/api/auth/login",
            headers=HEADERS,
            json={"username": "alice@example.com", "password": "password123"},
        )
        assert login_a.status_code == 200
        assert login_a.json()["role"] == "MEMBER"
        tok_a = login_a.json()["token"]
        h_a = {**HEADERS, "x-bsb-admin-session": tok_a}

        # 3. Member MUST be blocked from Admin/Trading endpoints
        assert client.get("/api/settings", headers=h_a).status_code == 401
        assert client.post("/api/orders/submit", headers=h_a, json={}).status_code == 401
        assert client.get("/api/analytics/summary", headers=h_a).status_code == 401

        # 4. Member CAN save & list encrypted API keys (never returned in plaintext)
        cred_res = client.post(
            "/api/member/credentials",
            headers=h_a,
            json={
                "exchange": "UPBIT",
                "label": "MyUpbit",
                "access_key": "upbit-access-12345678",
                "secret_key": "upbit-secret-abcdefgh",
            },
        )
        assert cred_res.status_code == 200
        cid = cred_res.json()["id"]
        assert "upbit-access-12345678" not in cred_res.text
        assert cred_res.json()["key_hint"] == "upbi****5678"

        # Verify encrypted at rest in SQLite
        with app.state.storage.connect() as conn:
            row = conn.execute(
                "SELECT access_key_enc, secret_key_enc FROM user_exchange_credentials WHERE id=?",
                (cid,),
            ).fetchone()
            assert "upbit-access-12345678" not in row["access_key_enc"]
            assert decrypt_secret(row["access_key_enc"]) == "upbit-access-12345678"

        # 5. Bob cannot see or delete Alice's credentials
        login_b = client.post(
            "/api/auth/login",
            headers=HEADERS,
            json={"username": "bob@example.com", "password": "password123"},
        )
        h_b = {**HEADERS, "x-bsb-admin-session": login_b.json()["token"]}
        assert client.get("/api/member/credentials", headers=h_b).json()["items"] == []
        assert client.delete(f"/api/member/credentials/{cid}", headers=h_b).status_code == 404


def test_totp_mfa_and_exchange_adapters(tmp_path):
    app = create_app(tmp_path / "p2_mfa.sqlite3", enforce_admin_auth=True)
    with TestClient(app, base_url=ORIGIN) as client:
        client.post(
            "/api/auth/register",
            headers=HEADERS,
            json={"email": "mfa@example.com", "password": "password123"},
        )
        tok = client.post(
            "/api/auth/login",
            headers=HEADERS,
            json={"username": "mfa@example.com", "password": "password123"},
        ).json()["token"]
        h = {**HEADERS, "x-bsb-admin-session": tok}

        setup_res = client.post("/api/auth/mfa/setup", headers=h, json={})
        assert setup_res.status_code == 200
        secret = setup_res.json()["secret"]

        code = totp_code(secret)
        en = client.post("/api/auth/mfa/enable", headers=h, json={"code": code})
        assert en.status_code == 200

        # Login without TOTP fails
        fail_login = client.post(
            "/api/auth/login",
            headers=HEADERS,
            json={"username": "mfa@example.com", "password": "password123"},
        )
        assert fail_login.status_code == 401

        # Login with TOTP succeeds
        ok_login = client.post(
            "/api/auth/login",
            headers=HEADERS,
            json={"username": "mfa@example.com", "password": "password123", "totp_code": code},
        )
        assert ok_login.status_code == 200

        # Public exchange ticker endpoints
        assert client.get("/api/public/exchanges/tickers?exchange=bithumb").status_code == 200

        # Member analytics endpoint is isolated and accessible to member
        m_ana = client.get("/api/member/analytics", headers=h)
        assert m_ana.status_code == 200
        assert m_ana.json()["summary"]["trade_count"] == 0


def test_strategy_proposal_preview_never_auto_applies(tmp_path):
    app = create_app(tmp_path / "p2_prop.sqlite3", enforce_admin_auth=False)
    with TestClient(app, base_url=ORIGIN) as client:
        res = client.post(
            "/api/admin/strategy-proposals/preview",
            headers=HEADERS,
            json={
                "proposal_schema": "bithumb-strategy-proposal/1.0",
                "hypothesis": "Test drop_1m tuning",
                "changes": [{"path": "entry_filters.drop_1m", "to": "0.025"}],
                "auto_apply": True,
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["valid"] is True
        assert body["auto_applied"] is False
        assert body["diffs"][0]["proposed"] == "0.025"


def test_admin_audit_logs_rbac_and_pg_migration_preview(tmp_path):
    app = create_app(tmp_path / "p3.sqlite3", enforce_admin_auth=True)
    with TestClient(app, base_url=ORIGIN) as client:
        client.post(
            "/api/auth/register",
            headers=HEADERS,
            json={"email": "mem@example.com", "password": "password123"},
        )
        m_tok = client.post(
            "/api/auth/login",
            headers=HEADERS,
            json={"username": "mem@example.com", "password": "password123"},
        ).json()["token"]
        # Member must be blocked from admin audit logs & pg preview
        assert client.get("/api/admin/audit-logs", headers={**HEADERS, "x-bsb-admin-session": m_tok}).status_code == 401
        assert client.get("/api/admin/pg-migration/preview", headers={**HEADERS, "x-bsb-admin-session": m_tok}).status_code == 401

        # Admin can read audit logs & PG DDL
        a_tok = client.post(
            "/api/auth/login",
            headers=HEADERS,
            json={"username": "admin", "password": "bsb1234!"},
        ).json()["token"]
        a_h = {**HEADERS, "x-bsb-admin-session": a_tok}
        assert client.get("/api/admin/audit-logs", headers=a_h).status_code == 200
        pg = client.get("/api/admin/pg-migration/preview", headers=a_h)
        assert pg.status_code == 200
        assert "NUMERIC(36, 18)" in pg.json()["ddl_preview"]
