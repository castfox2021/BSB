"""JWT signing only. No order execution and no credentials from process environment."""

import hashlib
import re
import stat
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import jwt
from dotenv import dotenv_values


class CredentialError(ValueError):
    pass


@dataclass(frozen=True)
class Credentials:
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    @property
    def account_id(self):
        return hashlib.sha256(self.access_key.encode()).hexdigest()

    def __repr__(self):
        return "<BithumbCredentials redacted>"


def load_credentials(path: Path):
    if not path.is_file():
        return None, False, "NOT_CONFIGURED"
    if path.is_symlink() or (stat.S_IMODE(path.stat().st_mode) & 0o077):
        raise CredentialError("ENV_PERMISSIONS")
    values = dotenv_values(path, interpolate=False)
    enabled = (values.get("BITHUMB_PRIVATE_ENABLED") or "false").lower() in (
        "1",
        "true",
    )
    if not enabled:
        return None, False, "DISABLED"
    if (values.get("BITHUMB_KEYS_ROTATED") or "false").lower() not in ("1", "true"):
        raise CredentialError("ROTATION_CONFIRMATION_REQUIRED")
    access = values.get("BITHUMB_ACCESS_KEY", "")
    secret = values.get("BITHUMB_SECRET_KEY", "")
    if (
        not access
        or not secret
        or len(access) > 512
        or not 32 <= len(secret) <= 2048
        or any(c in access + secret for c in "\r\n\x00")
    ):
        raise CredentialError("INVALID_CREDENTIAL_FILE")
    return Credentials(access, secret), True, "CONFIGURED"


def serialize(parameters=None):
    """Single ordered encoder for hash + actual wire query. Body signing is pure; never sent."""
    pairs = []
    for key, value in (parameters or {}).items():
        if not isinstance(key, str) or not re.fullmatch(
            r"[a-z_][a-z0-9_]*(?:\[\])?", key
        ):
            raise ValueError("invalid parameter name")
        values = value if isinstance(value, (list, tuple)) else [value]
        wire_key = (
            (key if key.endswith("[]") else key + "[]")
            if isinstance(value, (list, tuple))
            else key
        )
        for item in values:
            if isinstance(item, bool) or not isinstance(item, (str, int, Decimal)):
                raise ValueError(
                    "parameters must be strings, integers, or Decimal; no floats"
                )
            if isinstance(item, Decimal):
                if not item.is_finite():
                    raise ValueError("non-finite parameter")
                text = format(item, "f")
            else:
                text = str(item)
            if any(ord(c) < 32 or ord(c) == 127 for c in text):
                raise ValueError("control character in parameter")
            pairs.append((wire_key, text))
    return urlencode(pairs, safe="[]")


class BithumbAuth:
    def __init__(self, credentials: Credentials):
        self._credentials = credentials

    def __repr__(self):
        return "<BithumbAuth redacted>"

    def token(self, query=""):
        payload = {
            "access_key": self._credentials.access_key,
            "nonce": str(uuid4()),
            "timestamp": time.time_ns() // 1_000_000,
        }
        if query:
            payload.update(
                query_hash=hashlib.sha512(query.encode("utf-8")).hexdigest(),
                query_hash_alg="SHA512",
            )
        return jwt.encode(payload, self._credentials.secret_key, algorithm="HS256")
