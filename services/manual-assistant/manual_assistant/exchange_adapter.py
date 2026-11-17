"""Exchange Adapter abstraction for Phase 2 (Bithumb + Upbit public market data)."""

import time
from abc import ABC, abstractmethod

import httpx


class ExchangeAdapter(ABC):
    name: str

    @abstractmethod
    async def fetch_tickers(self, limit: int = 100) -> list[dict]:
        """Return normalized public ticker list."""


class BithumbAdapter(ExchangeAdapter):
    name = "BITHUMB"

    def __init__(self, market):
        self.market = market

    async def fetch_tickers(self, limit: int = 100) -> list[dict]:
        rows = list(getattr(self.market, "rows", []))
        meta = getattr(self.market, "market_metadata", {})
        return [
            {
                "exchange": self.name,
                "symbol": r["symbol"],
                "coin_name": meta.get(r["symbol"], {}).get("korean_name")
                or r["symbol"].replace("KRW-", ""),
                "price": r.get("price"),
                "change_1m": r.get("completed_price_change_1m"),
                "change_3m": r.get("completed_price_change_3m"),
                "change_5m": r.get("completed_price_change_5m"),
                "ready": bool(r.get("ready")),
            }
            for r in rows[:limit]
        ]


class UpbitAdapter(ExchangeAdapter):
    name = "UPBIT"
    BASE = "https://api.upbit.com/v1"

    def __init__(self, transport=None):
        self.transport = transport
        self._cache: list[dict] = []
        self._cache_at: float = 0.0

    async def fetch_tickers(self, limit: int = 100) -> list[dict]:
        now = time.monotonic()
        if self._cache and now - self._cache_at < 15.0:
            return self._cache[:limit]
        try:
            async with httpx.AsyncClient(
                timeout=6, follow_redirects=False, trust_env=False, transport=self.transport
            ) as client:
                mkts_res = await client.get(f"{self.BASE}/market/all", params={"isDetails": "false"})
                if mkts_res.status_code != 200:
                    return self._cache[:limit]
                krw_mkts = [
                    m for m in mkts_res.json() if isinstance(m, dict) and str(m.get("market", "")).startswith("KRW-")
                ]
                name_map = {m["market"]: m.get("korean_name") or m["market"] for m in krw_mkts}
                codes = list(name_map.keys())[:limit]
                if not codes:
                    return []
                tick_res = await client.get(f"{self.BASE}/ticker", params={"markets": ",".join(codes)})
                if tick_res.status_code != 200:
                    return self._cache[:limit]
                out = [
                    {
                        "exchange": self.name,
                        "symbol": t["market"],
                        "coin_name": name_map.get(t["market"], t["market"].replace("KRW-", "")),
                        "price": str(t.get("trade_price") or "0"),
                        "change_24h": str(t.get("signed_change_rate") or "0"),
                        "acc_trade_price_24h": str(t.get("acc_trade_price_24h") or "0"),
                        "ready": True,
                    }
                    for t in tick_res.json()
                    if isinstance(t, dict) and t.get("market")
                ]
                self._cache = out
                self._cache_at = now
                return out[:limit]
        except Exception:
            return self._cache[:limit]
