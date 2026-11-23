"""Official model price catalog (same LiteLLM-derived list sub2api bills against).

The catalog is fetched server-side, verified against its published SHA-256 and
cached in memory. Nothing about users or this deployment is sent upstream.
"""

import asyncio
import hashlib
import hmac
import json
import re
import time
from typing import Any

import httpx

DEFAULT_URL = "https://raw.githubusercontent.com/Wei-Shaw/model-price-repo/main/model_prices_and_context_window.json"
MAX_BYTES = 8 * 1024 * 1024
TTL_SECONDS = 6 * 3600
RETRY_SECONDS = 300
PRICE_KEYS = {
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "cache_read": "cache_read_input_token_cost",
    "cache_write": "cache_creation_input_token_cost",
}
_DATE_SUFFIX = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})$")


def _per_million(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return round(float(value) * 1_000_000, 6)


def official_price(entry: Any) -> dict[str, float | None] | None:
    if not isinstance(entry, dict):
        return None
    prices = {name: _per_million(entry.get(key)) for name, key in PRICE_KEYS.items()}
    return prices if any(value is not None for value in prices.values()) else None


class PriceCatalog:
    def __init__(self, url: str = DEFAULT_URL, client: httpx.AsyncClient | None = None,
                 ttl: float = TTL_SECONDS, clock=time.monotonic):
        self.url = url
        self.client = client
        self.ttl = ttl
        self.clock = clock
        self.models: dict[str, Any] = {}
        self.loaded_at: float | None = None
        self.retry_at = 0.0
        self.lock = asyncio.Lock()

    @staticmethod
    def parse(body: bytes, digest_text: str) -> dict[str, Any]:
        expected = digest_text.strip().split()[0].lower() if digest_text.strip() else ""
        if not re.fullmatch(r"[0-9a-f]{64}", expected) or not hmac.compare_digest(hashlib.sha256(body).hexdigest(), expected):
            raise ValueError("价格库校验失败")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("价格库格式不兼容")
        return {str(name).lower(): entry for name, entry in data.items() if isinstance(entry, dict)}

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()

    async def _fetch(self, url: str) -> bytes:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=False)
        async with self.client.stream("GET", url) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("价格库过大")
            return bytes(body)

    async def refresh(self) -> None:
        body, digest = await asyncio.gather(self._fetch(self.url), self._fetch(re.sub(r"\.json$", "", self.url) + ".sha256"))
        self.models = self.parse(body, digest.decode("utf-8", "replace"))
        self.loaded_at = self.clock()

    async def ensure(self) -> bool:
        """Load or refresh when stale. Returns True when any catalog is available."""
        if not self.url:
            return False
        now = self.clock()
        if self.loaded_at is not None and now - self.loaded_at < self.ttl:
            return True
        if now < self.retry_at:
            return bool(self.models)
        async with self.lock:
            now = self.clock()
            if self.loaded_at is not None and now - self.loaded_at < self.ttl:
                return True
            try:
                await self.refresh()
            except (httpx.HTTPError, ValueError, UnicodeError):
                self.retry_at = self.clock() + RETRY_SECONDS
        return bool(self.models)

    def lookup(self, model: str) -> dict[str, float | None] | None:
        name = str(model or "").strip().lower()
        candidates = [name, name.rsplit("/", 1)[-1]]
        candidates += [_DATE_SUFFIX.sub("", item) for item in candidates]
        for candidate in dict.fromkeys(candidates):
            entry = self.models.get(candidate)
            if isinstance(entry, dict) and isinstance(entry.get("alias_of"), str):
                entry = self.models.get(entry["alias_of"].lower(), entry)
            price = official_price(entry)
            if price:
                return price
        return None


def model_row(stat: dict[str, Any], catalog: PriceCatalog) -> dict[str, Any]:
    """Project one upstream ModelStat into official vs. effective unit prices.

    sub2api exposes only per-model cost totals, so the effective price is the
    official price scaled by the observed multiplier actual_cost / cost.
    """
    def number(key):
        value = stat.get(key)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

    cost, actual = number("cost"), number("actual_cost")
    multiplier = round(actual / cost, 6) if cost > 0 else None
    official = catalog.lookup(stat.get("model"))
    effective = None
    if official and multiplier is not None:
        effective = {key: None if value is None else round(value * multiplier, 6) for key, value in official.items()}
    return {
        "model": str(stat.get("model") or ""),
        "requests": number("requests"),
        "input_tokens": number("input_tokens"),
        "output_tokens": number("output_tokens"),
        "cache_read_tokens": number("cache_read_tokens"),
        "cache_write_tokens": number("cache_creation_tokens"),
        "total_tokens": number("total_tokens"),
        "cost": cost,
        "actual_cost": actual,
        "multiplier": multiplier,
        "official": official,
        "actual": effective,
    }
