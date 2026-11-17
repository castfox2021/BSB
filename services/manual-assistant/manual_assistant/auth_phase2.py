"""Phase 2 Auth: MEMBER accounts, Scrypt passwords, TOTP MFA, and Encrypted API Key Vault."""

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
import uuid
from datetime import UTC, datetime, timedelta


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32
    )
    return f"scrypt${salt.hex()}${derived.hex()}"


def verify_scrypt(password: str, stored_hash: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored_hash.split("$", 2)
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=16384,
            r=8,
            p=1,
            dklen=32,
        ).hex()
        return hmac.compare_digest(derived, digest_hex)
    except Exception:
        return False


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_code(secret: str, for_time: int | None = None, step: int = 30) -> str:
    padded = secret.upper() + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    counter = int((for_time if for_time is not None else time.time()) // step)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code_int:06d}"


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    clean = str(code or "").strip()
    if len(clean) != 6 or not clean.isdigit():
        return False
    now = int(time.time())
    for delta in range(-window, window + 1):
        if hmac.compare_digest(totp_code(secret, now + delta * 30), clean):
            return True
    return False


def _vault_key() -> bytes:
    raw = os.getenv("BSB_VAULT_MASTER_KEY") or os.getenv("BSB_AUTH_PASSWORD") or "bsb-dev-vault-key-change-me"
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encrypt_secret(plaintext: str) -> str:
    nonce = secrets.token_bytes(16)
    key = _vault_key()
    data = plaintext.encode("utf-8")
    stream = bytearray()
    counter = 0
    while len(stream) < len(data):
        block = hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
        stream.extend(block)
        counter += 1
    cipher = bytes(a ^ b for a, b in zip(data, stream[: len(data)]))
    mac = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + mac + cipher).decode("ascii")


def decrypt_secret(token: str) -> str:
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    nonce, mac, cipher = raw[:16], raw[16:48], raw[48:]
    key = _vault_key()
    expected_mac = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected_mac):
        raise ValueError("금고 복호화 무결성 검증 실패")
    stream = bytearray()
    counter = 0
    while len(stream) < len(cipher):
        block = hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
        stream.extend(block)
        counter += 1
    return bytes(a ^ b for a, b in zip(cipher, stream[: len(cipher)])).decode("utf-8")


def mask_key_hint(key_val: str) -> str:
    s = key_val.strip()
    if len(s) <= 8:
        return "****"
    return f"{s[:4]}****{s[-4:]}"


class Phase2IdentityService:
    """Manages MEMBER users, unified sessions (ADMIN/MEMBER), TOTP MFA, and encrypted API vaults."""

    def __init__(self, store):
        self.store = store
        with self.store.connect() as conn:
            conn.executescript("""
              CREATE TABLE IF NOT EXISTS users(
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'MEMBER',
                totp_secret TEXT,
                mfa_enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS user_sessions(
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                ip TEXT,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS user_exchange_credentials(
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                exchange TEXT NOT NULL,
                label TEXT NOT NULL,
                access_key_enc TEXT NOT NULL,
                secret_key_enc TEXT NOT NULL,
                key_hint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, exchange, label)
              );
            """)

    def register_member(self, email: str, password: str) -> dict:
        clean_email = email.strip().lower()
        if "@" not in clean_email or len(clean_email) < 5:
            raise ValueError("올바른 이메일 형식이 아닙니다.")
        if len(password) < 8:
            raise ValueError("비밀번호는 8자 이상이어야 합니다.")
        uid = uuid.uuid4().hex
        now_iso = datetime.now(UTC).isoformat()
        with self.store.connect() as conn:
            if conn.execute("SELECT 1 FROM users WHERE email=?", (clean_email,)).fetchone():
                raise ValueError("이미 가입된 이메일입니다.")
            conn.execute(
                "INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                (uid, clean_email, hash_password(password), "MEMBER", None, 0, now_iso),
            )
            self.store.audit(conn, "member_registered", {"user_id": uid, "email": clean_email})
        return {"user_id": uid, "email": clean_email, "role": "MEMBER"}

    def authenticate_member(self, email: str, password: str, totp: str = "", ip: str = "unknown") -> tuple[dict | None, int, str]:
        clean_email = email.strip().lower()
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE email=?", (clean_email,)).fetchone()
            if not row or not verify_scrypt(password, row["password_hash"]):
                return None, 401, "이메일 또는 비밀번호가 올바르지 않습니다."
            if row["mfa_enabled"]:
                if not verify_totp(row["totp_secret"], totp):
                    return None, 401, "2단계 인증(TOTP) 코드가 올바르지 않습니다."
            raw_token = secrets.token_urlsafe(32)
            th = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            now_dt = datetime.now(UTC)
            exp_dt = now_dt + timedelta(days=30)
            conn.execute(
                "INSERT INTO user_sessions VALUES (?,?,?,?,?,?,?)",
                (th, row["id"], clean_email, row["role"], ip, exp_dt.isoformat(), now_dt.isoformat()),
            )
        return {
            "token": raw_token,
            "user_id": row["id"],
            "username": clean_email,
            "role": row["role"],
            "mfa_enabled": bool(row["mfa_enabled"]),
        }, 200, "OK"

    def validate_session(self, raw_token: str | None) -> dict | None:
        if not raw_token:
            return None
        th = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM user_sessions WHERE token_hash=?", (th,)).fetchone()
            if not row:
                return None
            if datetime.fromisoformat(row["expires_at"]) < datetime.now(UTC):
                conn.execute("DELETE FROM user_sessions WHERE token_hash=?", (th,))
                return None
            return dict(row)

    def revoke_session(self, raw_token: str | None):
        if not raw_token:
            return
        th = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        with self.store.connect() as conn:
            conn.execute("DELETE FROM user_sessions WHERE token_hash=?", (th,))

    def list_active_sessions(self, user_id: str, current_raw_token: str | None) -> list[dict]:
        cur_th = hashlib.sha256(current_raw_token.encode("utf-8")).hexdigest() if current_raw_token else ""
        now_iso = datetime.now(UTC).isoformat()
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT token_hash, ip, created_at, expires_at FROM user_sessions WHERE user_id=? AND expires_at > ? ORDER BY created_at DESC",
                (user_id, now_iso),
            ).fetchall()
        return [
            {
                "ip": r["ip"] or "unknown",
                "created_at": r["created_at"],
                "expires_at": r["expires_at"],
                "is_current": r["token_hash"] == cur_th,
            }
            for r in rows
        ]

    def revoke_other_sessions(self, user_id: str, current_raw_token: str | None) -> int:
        cur_th = hashlib.sha256(current_raw_token.encode("utf-8")).hexdigest() if current_raw_token else ""
        with self.store.connect() as conn:
            conn.execute(
                "DELETE FROM user_sessions WHERE user_id=? AND token_hash != ?",
                (user_id, cur_th),
            )
            return conn.execute("SELECT changes()").fetchone()[0]

    def setup_mfa(self, user_id: str) -> dict:
        secret = generate_totp_secret()
        with self.store.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users VALUES (?,?,?,?,?,?,?)",
                (user_id, user_id, "admin-managed", "ADMIN", secret, 0, datetime.now(UTC).isoformat()),
            )
            conn.execute("UPDATE users SET totp_secret=?, mfa_enabled=0 WHERE id=?", (secret, user_id))
        return {"secret": secret, "otpauth_uri": f"otpauth://totp/BSB:{user_id}?secret={secret}&issuer=BSB"}

    def enable_mfa(self, user_id: str, code: str) -> bool:
        with self.store.connect() as conn:
            row = conn.execute("SELECT totp_secret FROM users WHERE id=?", (user_id,)).fetchone()
            if not row or not row["totp_secret"]:
                return False
            if not verify_totp(row["totp_secret"], code):
                return False
            conn.execute("UPDATE users SET mfa_enabled=1 WHERE id=?", (user_id,))
        return True

    def is_mfa_enabled(self, user_id: str) -> bool:
        with self.store.connect() as conn:
            row = conn.execute("SELECT mfa_enabled, totp_secret FROM users WHERE id=?", (user_id,)).fetchone()
            return bool(row and row["mfa_enabled"] and row["totp_secret"])

    def verify_user_mfa(self, user_id: str, code: str) -> bool:
        with self.store.connect() as conn:
            row = conn.execute("SELECT totp_secret FROM users WHERE id=?", (user_id,)).fetchone()
            return bool(row and row["totp_secret"] and verify_totp(row["totp_secret"], code))

    def disable_mfa(self, user_id: str, code: str) -> bool:
        if not self.verify_user_mfa(user_id, code):
            return False
        with self.store.connect() as conn:
            conn.execute("UPDATE users SET mfa_enabled=0, totp_secret=NULL WHERE id=?", (user_id,))
        return True

    def delete_member_account(self, user_id: str, password: str) -> tuple[bool, str]:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT password_hash, role FROM users WHERE id=?",
                (user_id,),
            ).fetchone()
            if not row or row["role"] == "ADMIN" or row["password_hash"] == "admin-managed":
                return False, "운영자 계정은 탈퇴할 수 없습니다."
            if not verify_scrypt(password, row["password_hash"]):
                return False, "비밀번호가 올바르지 않습니다."
            conn.execute("DELETE FROM user_exchange_credentials WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        return True, "ok"

    def change_password(self, user_id: str, current_password: str, new_password: str) -> tuple[bool, str]:
        if len(new_password) < 8:
            return False, "새 비밀번호는 8자 이상이어야 합니다."
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT password_hash, role FROM users WHERE id=?",
                (user_id,),
            ).fetchone()
            if not row or row["password_hash"] == "admin-managed":
                return False, "회원 계정만 비밀번호를 변경할 수 있습니다."
            if not verify_scrypt(current_password, row["password_hash"]):
                return False, "현재 비밀번호가 올바르지 않습니다."
            conn.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (hash_password(new_password), user_id),
            )
        return True, "ok"

    def list_credentials(self, user_id: str) -> list[dict]:
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT id,exchange,label,key_hint,created_at FROM user_exchange_credentials WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def save_credential(self, user_id: str, exchange: str, label: str, access_key: str, secret_key: str) -> dict:
        ex = exchange.strip().upper()
        if ex not in ("BITHUMB", "UPBIT"):
            raise ValueError("지원 거래소는 BITHUMB 또는 UPBIT입니다.")
        if len(access_key.strip()) < 8 or len(secret_key.strip()) < 8:
            raise ValueError("API 키 길이가 올바르지 않습니다.")
        cid = uuid.uuid4().hex
        now_iso = datetime.now(UTC).isoformat()
        hint = mask_key_hint(access_key)
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO user_exchange_credentials VALUES (?,?,?,?,?,?,?,?)",
                (
                    cid,
                    user_id,
                    ex,
                    label.strip()[:40] or ex,
                    encrypt_secret(access_key.strip()),
                    encrypt_secret(secret_key.strip()),
                    hint,
                    now_iso,
                ),
            )
        return {"id": cid, "exchange": ex, "label": label.strip()[:40] or ex, "key_hint": hint, "created_at": now_iso}

    def delete_credential(self, user_id: str, cred_id: str) -> bool:
        with self.store.connect() as conn:
            conn.execute(
                "DELETE FROM user_exchange_credentials WHERE id=? AND user_id=?",
                (cred_id, user_id),
            )
            return conn.execute("SELECT changes()").fetchone()[0] > 0

    def get_decrypted_credential(self, user_id: str, exchange: str = "BITHUMB") -> tuple[str, str] | None:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT access_key_enc, secret_key_enc FROM user_exchange_credentials WHERE user_id=? AND exchange=? ORDER BY created_at DESC LIMIT 1",
                (user_id, exchange.upper()),
            ).fetchone()
            if not row:
                return None
            return decrypt_secret(row["access_key_enc"]), decrypt_secret(row["secret_key_enc"])
