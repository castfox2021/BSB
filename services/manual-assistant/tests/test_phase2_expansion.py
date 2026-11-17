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
        assert client.get("/api/admin/pg-migration/status", headers={**HEADERS, "x-bsb-admin-session": m_tok}).status_code == 401
        assert client.get("/api/admin/pg-migration/export", headers={**HEADERS, "x-bsb-admin-session": m_tok}).status_code == 401

        # Admin can read audit logs, PG DDL & export SQL
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
        assert "trade_lot_allocations" in pg.json()["ddl_preview"]
        pg_st = client.get("/api/admin/pg-migration/status", headers=a_h)
        assert pg_st.status_code == 200
        assert "sqlite_counts" in pg_st.json()
        pg_exp = client.get("/api/admin/pg-migration/export", headers=a_h)
        assert pg_exp.status_code == 200
        assert "bsb-pg-migration.sql" in pg_exp.headers.get("content-disposition", "")
        assert "BEGIN;" in pg_exp.text
        assert "INSERT INTO users" in pg_exp.text
        assert "mem@example.com" in pg_exp.text
        assert "ON CONFLICT (id) DO NOTHING;" in pg_exp.text
        assert "COMMIT;" in pg_exp.text


def test_member_upbit_sync_orders_and_trades_isolation(tmp_path, monkeypatch):
    import httpx

    app = create_app(tmp_path / "p4.sqlite3", enforce_admin_auth=True)
    seen_auth_headers: list[str] = []

    class FakeResponse:
        def __init__(self, status_code: int, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aclose(self):
            return None

        async def get(self, url: str, headers: dict | None = None):
            if headers and "Authorization" in headers:
                seen_auth_headers.append(headers["Authorization"])
            if url.startswith("https://api.upbit.com/v1/accounts"):
                return FakeResponse(
                    200,
                    [
                        {
                            "currency": "BTC",
                            "balance": "0.01",
                            "locked": "0",
                            "avg_buy_price": "100000000",
                            "unit_currency": "KRW",
                        }
                    ],
                )
            if url.startswith("https://api.upbit.com/v1/orders/closed"):
                return FakeResponse(
                    200,
                    [
                        {
                            "uuid": "up-buy-1",
                            "side": "bid",
                            "market": "KRW-BTC",
                            "price": "100000000",
                            "executed_volume": "0.01",
                            "executed_funds": "1000000",
                            "paid_fee": "500",
                            "created_at": "2026-11-17T01:00:00+09:00",
                        },
                        {
                            "uuid": "up-sell-1",
                            "side": "ask",
                            "market": "KRW-BTC",
                            "price": "102000000",
                            "executed_volume": "0.01",
                            "executed_funds": "1020000",
                            "paid_fee": "510",
                            "created_at": "2026-11-17T02:00:00+09:00",
                        },
                    ],
                )
            return FakeResponse(404, {})

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    with TestClient(app, base_url=ORIGIN) as client:
        client.post(
            "/api/auth/register",
            headers=HEADERS,
            json={"email": "upA@example.com", "password": "password123"},
        )
        client.post(
            "/api/auth/register",
            headers=HEADERS,
            json={"email": "upB@example.com", "password": "password123"},
        )
        tok_a = client.post(
            "/api/auth/login",
            headers=HEADERS,
            json={"username": "upA@example.com", "password": "password123"},
        ).json()["token"]
        tok_b = client.post(
            "/api/auth/login",
            headers=HEADERS,
            json={"username": "upB@example.com", "password": "password123"},
        ).json()["token"]
        h_a = {**HEADERS, "x-bsb-admin-session": tok_a}
        h_b = {**HEADERS, "x-bsb-admin-session": tok_b}

        res = client.post(
            "/api/member/credentials",
            headers=h_a,
            json={
                "exchange": "UPBIT",
                "label": "upbit-main",
                "access_key": "upbit-access-12345678",
                "secret_key": "upbit-secret-123456789012345678901234",
            },
        )
        assert res.status_code == 200

        sync_res = client.post("/api/member/sync", headers=h_a, json={})
        assert sync_res.status_code == 200
        sync_body = sync_res.json()
        assert sync_body["synced"] is True
        assert sync_body["accounts_count"] == 1
        assert sync_body["inserted_fills"] == 2
        assert len(seen_auth_headers) == 2

        acc_a = client.get("/api/member/accounts", headers=h_a).json()["items"]
        assert len(acc_a) == 1
        assert acc_a[0]["currency"] == "UPBIT:BTC"

        trades_a = client.get("/api/member/trades", headers=h_a).json()
        assert trades_a["total"] == 1
        assert trades_a["items"][0]["market"] == "KRW-BTC"

        # Member B must not see Member A's synced trades or accounts
        assert client.get("/api/member/accounts", headers=h_b).json()["items"] == []
        assert client.get("/api/member/trades", headers=h_b).json()["total"] == 0

        # Member must be blocked from /api/admin/members, Admin can view metadata only
        assert client.get("/api/admin/members", headers=h_a).status_code == 401
        a_tok = client.post(
            "/api/auth/login",
            headers=HEADERS,
            json={"username": "admin", "password": "bsb1234!"},
        ).json()["token"]
        members_res = client.get("/api/admin/members", headers={**HEADERS, "x-bsb-admin-session": a_tok})
        assert members_res.status_code == 200
        items = members_res.json()["items"]
        up_a = next(m for m in items if m["email"] == "upa@example.com")
        assert up_a["exchanges"] == ["UPBIT"]
        assert up_a["credentials_count"] == 1
        assert "password_hash" not in up_a
        assert "totp_secret" not in up_a

        # Member isolated export (CSV/JSON)
        csv_a = client.get("/api/member/export?format=csv", headers=h_a)
        assert csv_a.status_code == 200
        assert "KRW-BTC" in csv_a.text
        csv_b = client.get("/api/member/export?format=csv", headers=h_b)
        assert csv_b.status_code == 200
        assert "KRW-BTC" not in csv_b.text

        # Member password change validation & re-login
        assert (
            client.post(
                "/api/member/password",
                headers=h_a,
                json={"current_password": "wrong", "new_password": "newpassword123"},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/member/password",
                headers=h_a,
                json={"current_password": "password123", "new_password": "short"},
            ).status_code
            == 400
        )
        ok_pw = client.post(
            "/api/member/password",
            headers=h_a,
            json={"current_password": "password123", "new_password": "newpassword123"},
        )
        assert ok_pw.status_code == 200
        assert (
            client.post(
                "/api/auth/login",
                headers=HEADERS,
                json={"username": "upA@example.com", "password": "password123"},
            ).status_code
            == 401
        )
        tok_a2 = client.post(
            "/api/auth/login",
            headers=HEADERS,
            json={"username": "upA@example.com", "password": "newpassword123"},
        )
        assert tok_a2.status_code == 200
        h_a2 = {**HEADERS, "x-bsb-admin-session": tok_a2.json()["token"]}
        sess_list = client.get("/api/member/sessions", headers=h_a2).json()["items"]
        assert len(sess_list) >= 2
        assert any(s["is_current"] for s in sess_list)
        rev = client.post("/api/member/sessions/revoke-others", headers=h_a2, json={})
        assert rev.status_code == 200
        assert rev.json()["revoked"] >= 1
        assert client.get("/api/member/sessions", headers=h_a).status_code == 401
        assert client.get("/api/member/sessions", headers=h_a2).status_code == 200

        # MFA disable requires valid TOTP
        from manual_assistant.auth_phase2 import totp_code

        sec = client.post("/api/auth/mfa/setup", headers=h_a2, json={}).json()["secret"]
        assert client.post("/api/auth/mfa/enable", headers=h_a2, json={"code": totp_code(sec)}).status_code == 200
        assert client.post("/api/auth/mfa/disable", headers=h_a2, json={"code": "000000"}).status_code == 400
        dis = client.post("/api/auth/mfa/disable", headers=h_a2, json={"code": totp_code(sec)})
        assert dis.status_code == 200
        assert dis.json()["mfa_enabled"] is False

        # Member account deletion (wrong password 400, admin blocked 400, valid password 200)
        assert client.post("/api/member/account/delete", headers=h_a2, json={"password": "wrong"}).status_code == 400
        assert (
            client.post(
                "/api/member/account/delete",
                headers={**HEADERS, "x-bsb-admin-session": a_tok},
                json={"password": "bsb1234!"},
            ).status_code
            == 400
        )
        del_res = client.post("/api/member/account/delete", headers=h_a2, json={"password": "newpassword123"})
        assert del_res.status_code == 200
        assert del_res.json()["deleted"] is True
        assert client.get("/api/member/sessions", headers=h_a2).status_code == 401
        assert (
            client.post(
                "/api/auth/login",
                headers=HEADERS,
                json={"username": "upA@example.com", "password": "newpassword123"},
            ).status_code
            == 401
        )
