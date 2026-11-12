import asyncio
import json
import logging
import time
from decimal import Decimal

import httpx

from .private_auth import BithumbAuth, serialize

BASE = "https://api.bithumb.com"
PRIVATE_WS = "wss://ws-api.bithumb.com/websocket/v2/private"
ALLOWED = {"/v1/accounts", "/v1/orders/chance", "/v1/order", "/v1/orders"}
ERRORS = {
    "invalid_query_payload": "요청 해시 검증 실패",
    "jwt_verification": "JWT 서명 검증 실패",
    "expired_jwt": "JWT 만료 또는 시계 확인 필요",
    "NotAllowIP": "허용 IP 확인 필요",
    "out_of_scope": "조회 권한 부족",
    "network_error": "네트워크 오류",
    "rate_limited": "호출 제한 대기",
    "api_error": "거래소 조회 오류",
    "invalid_response": "응답 형식 오류",
}


class PrivateError(Exception):
    def __init__(self, code, status=None):
        self.code = code if code in ERRORS else "api_error"
        self.status = status
        super().__init__(ERRORS[self.code])


def quiet_transport_logs():
    for name in [
        "httpx",
        "httpcore",
        "websockets.client",
        *list(logging.Logger.manager.loggerDict),
    ]:
        if name.startswith(("httpx", "httpcore", "websockets")):
            logging.getLogger(name).setLevel(logging.CRITICAL)


class PrivateClient:
    def __init__(self, credentials, transport=None):
        quiet_transport_logs()
        self.auth = BithumbAuth(credentials)
        self.http = httpx.AsyncClient(
            timeout=10, follow_redirects=False, trust_env=False, transport=transport
        )
        self.lock = asyncio.Lock()
        self.next_at = 0.0

    async def get(self, path, parameters=None):
        if path not in ALLOWED:
            raise PermissionError("read-only endpoint allowlist")
        query = serialize(parameters)
        url = httpx.URL(BASE + path).copy_with(query=query.encode("ascii"))
        # Sign the exact encoded query bytes that httpx will transmit.
        canonical = url.query.decode("ascii")
        for attempt in range(3):
            async with self.lock:
                await asyncio.sleep(max(0, self.next_at - time.monotonic()))
                self.next_at = time.monotonic() + 1
            try:
                response = await self.http.get(
                    url,
                    headers={
                        "Authorization": "Bearer " + self.auth.token(canonical),
                        "Accept": "application/json",
                    },
                )
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                raise PrivateError("network_error") from None
            if response.status_code in (418, 429):
                try:
                    delay = max(
                        60, min(600, float(response.headers.get("Retry-After", "60")))
                    )
                except ValueError:
                    delay = 60
                self.next_at = max(self.next_at, time.monotonic() + delay)
                raise PrivateError("rate_limited", response.status_code)
            if response.status_code >= 500 and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            if not 200 <= response.status_code < 300:
                try:
                    code = response.json().get("error", {}).get("name", "api_error")
                except Exception:
                    code = "api_error"
                raise PrivateError(code, response.status_code) from None
            try:
                data = json.loads(response.text, parse_float=Decimal)
                if not isinstance(data, (list, dict)):
                    raise ValueError()
                return data
            except Exception:
                raise PrivateError("invalid_response") from None
        raise PrivateError("network_error")

    async def close(self):
        await self.http.aclose()
