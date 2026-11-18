"""Public read-only DTO builders and Operator Session Auth for Task 19."""

import hashlib
import hmac
import os
import secrets
import time
from datetime import UTC, datetime, timedelta


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_password(plain: str, expected_plain: str, expected_hash: str | None = None) -> bool:
    if expected_hash and expected_hash.startswith("scrypt$"):
        try:
            _, salt_hex, digest_hex = expected_hash.split("$", 2)
            derived = hashlib.scrypt(
                plain.encode("utf-8"),
                salt=bytes.fromhex(salt_hex),
                n=16384,
                r=8,
                p=1,
                dklen=32,
            ).hex()
            return hmac.compare_digest(derived, digest_hex)
        except Exception:
            return False
    # Constant-time comparison via SHA-256 digest
    return hmac.compare_digest(
        hashlib.sha256(plain.encode("utf-8")).digest(),
        hashlib.sha256(expected_plain.encode("utf-8")).digest(),
    )


class AdminAuthService:
    """Server-side session authentication & rate limiting for single-operator mode."""

    def __init__(self, store):
        self.store = store
        self.failed_attempts: dict[str, list[float]] = {}
        with self.store.identity_connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS admin_sessions(
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    ip TEXT,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )

    def _check_rate_limit(self, ip: str) -> bool:
        now = time.monotonic()
        attempts = [t for t in self.failed_attempts.get(ip, []) if now - t < 300]
        self.failed_attempts[ip] = attempts
        return len(attempts) < 5

    def _record_failure(self, ip: str):
        now = time.monotonic()
        attempts = [t for t in self.failed_attempts.get(ip, []) if now - t < 300]
        attempts.append(now)
        self.failed_attempts[ip] = attempts

    def login(self, username: str, password: str, ip: str = "unknown") -> tuple[str | None, int, str]:
        if not self._check_rate_limit(ip):
            self.store.audit_event("admin_login_rate_limited", {"ip": ip})
            return None, 429, "로그인 시도 횟수를 초과했습니다. 5분 후 다시 시도하세요."

        expected_user = os.getenv("BSB_AUTH_USER", "admin")
        expected_pass = os.getenv("BSB_AUTH_PASSWORD")
        expected_hash = os.getenv("BSB_ADMIN_PASSWORD_HASH")
        if not expected_pass and not expected_hash:
            if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("BSB_DEV_MODE") == "1":
                expected_pass = "bsb1234!"
            else:
                self.store.audit_event("admin_login_blocked_no_secret", {"ip": ip})
                return None, 503, "서버 환경변수(BSB_AUTH_PASSWORD 또는 BSB_ADMIN_PASSWORD_HASH)가 설정되지 않았습니다."

        user_ok = hmac.compare_digest(username.encode("utf-8"), expected_user.encode("utf-8"))
        pass_ok = verify_password(password, expected_pass, expected_hash)
        if not (user_ok and pass_ok):
            self._record_failure(ip)
            self.store.audit_event("admin_login_failed", {"username": username[:32], "ip": ip})
            return None, 401, "아이디 또는 비밀번호가 올바르지 않습니다."

        self.failed_attempts.pop(ip, None)
        raw_token = secrets.token_urlsafe(32)
        now_dt = datetime.now(UTC)
        expires_dt = now_dt + timedelta(days=30)
        with self.store.identity_connect() as conn:
            conn.execute(
                "INSERT INTO admin_sessions VALUES (?,?,?,?,?)",
                (_hash_token(raw_token), expected_user, ip, expires_dt.isoformat(), now_dt.isoformat()),
            )
        self.store.audit_event("admin_login_success", {"username": expected_user, "ip": ip})
        return raw_token, 200, "OK"

    def validate(self, raw_token: str | None) -> str | None:
        if not raw_token:
            return None
        th = _hash_token(raw_token)
        with self.store.identity_connect() as conn:
            row = conn.execute(
                "SELECT username, expires_at FROM admin_sessions WHERE token_hash=?",
                (th,),
            ).fetchone()
            if not row:
                return None
            if datetime.fromisoformat(str(row["expires_at"])) < datetime.now(UTC):
                conn.execute("DELETE FROM admin_sessions WHERE token_hash=?", (th,))
                return None
            return row["username"]

    def logout(self, raw_token: str | None, ip: str = "unknown"):
        if not raw_token:
            return
        with self.store.identity_connect() as conn:
            conn.execute("DELETE FROM admin_sessions WHERE token_hash=?", (_hash_token(raw_token),))
        self.store.audit_event("admin_logout", {"ip": ip})


def public_market_summary(market) -> dict:
    rows = list(getattr(market, "rows", []))
    meta = getattr(market, "market_metadata", {})
    ready = [r for r in rows if r.get("ready")]
    sorted_1m = sorted(
        [r for r in rows if r.get("completed_price_change_1m") is not None],
        key=lambda r: float(r["completed_price_change_1m"]),
    )
    top_drops = [
        {
            "symbol": r["symbol"],
            "coin_name": meta.get(r["symbol"], {}).get("korean_name") or r["symbol"].replace("KRW-", ""),
            "price": r.get("price"),
            "change_1m": r.get("completed_price_change_1m"),
            "change_3m": r.get("completed_price_change_3m"),
            "change_5m": r.get("completed_price_change_5m"),
        }
        for r in sorted_1m[:5]
    ]
    top_gainers = [
        {
            "symbol": r["symbol"],
            "coin_name": meta.get(r["symbol"], {}).get("korean_name") or r["symbol"].replace("KRW-", ""),
            "price": r.get("price"),
            "change_1m": r.get("completed_price_change_1m"),
            "change_3m": r.get("completed_price_change_3m"),
            "change_5m": r.get("completed_price_change_5m"),
        }
        for r in reversed(sorted_1m[-5:])
    ]
    return {
        "connected_markets": len(getattr(market, "universe", [])),
        "ready_markets": len(ready),
        "top_drops": top_drops,
        "top_gainers": top_gainers,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def public_market_tickers(market, limit: int = 100) -> dict:
    rows = list(getattr(market, "rows", []))
    meta = getattr(market, "market_metadata", {})
    items = [
        {
            "symbol": r["symbol"],
            "coin_name": meta.get(r["symbol"], {}).get("korean_name") or r["symbol"].replace("KRW-", ""),
            "price": r.get("price"),
            "ready": bool(r.get("ready")),
            "change_1m": r.get("completed_price_change_1m"),
            "change_3m": r.get("completed_price_change_3m"),
            "change_5m": r.get("completed_price_change_5m"),
        }
        for r in rows[:limit]
    ]
    return {"items": items, "total": len(rows)}


def public_strategies_overview(reversion) -> dict:
    s = reversion.summary()
    return {
        "mode": "PAPER_SIMULATION",
        "watching_count": s["watching"],
        "watching_items": [
            {
                "symbol": w["symbol"],
                "coin_name": w["coin_name"],
                "drops": w["drops"],
                "base_bars": w.get("base_bars", 0),
            }
            for w in s.get("watching_items", [])
        ],
        "comparison": [
            {
                "policy": c["policy"],
                "trades": c["trades"],
                "win_rate": c["win_rate"],
                "realized_krw": c["realized"],
                "max_drawdown": c["max_drawdown"],
            }
            for c in s.get("comparison", [])
        ],
    }


def public_performance_summary(reversion) -> dict:
    s = reversion.summary()
    comp = reversion.get_exit_comparison()
    return {
        "mode": "PAPER_SIMULATION",
        "policies": comp.get("policies", []),
        "recent_anonymized_results": [
            {
                "symbol": r["symbol"],
                "coin_name": r.get("coin_name") or r["symbol"].replace("KRW-", ""),
                "exit_reason": r.get("exit_reason"),
                "net_pnl": r.get("net_pnl"),
                "opened_at": r.get("opened_at"),
                "closed_at": r.get("closed_at"),
            }
            for r in s.get("recent_results", [])
        ],
    }


def public_platform_status(market, engine) -> dict:
    return {
        "status": "OPERATIONAL" if getattr(engine, "last_error", None) is None else "DEGRADED",
        "connected_markets": len(getattr(market, "universe", [])),
        "last_tick_at": getattr(engine, "last_tick_at", None),
        "paper_simulation_active": True,
        "version": "19.0",
    }
