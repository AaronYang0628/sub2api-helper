"""Official price catalog: integrity check, caching, name matching and effective prices."""

import asyncio
import hashlib
import json
import unittest

import httpx

from helper.pricing_catalog import PriceCatalog, model_row

CATALOG = {
    "gpt-sample": {"input_cost_per_token": 1e-06, "output_cost_per_token": 4e-06,
                   "cache_read_input_token_cost": 1e-07, "cache_creation_input_token_cost": 2e-06},
    "claude-alias": {"alias_of": "gpt-sample"},
    "no-prices": {"max_input_tokens": 10},
}
URL = "https://prices.example.test/models.json"


def body_and_digest(data=CATALOG):
    body = json.dumps(data).encode()
    return body, hashlib.sha256(body).hexdigest()


def client_for(body, digest, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(str(request.url))
        if str(request.url) == URL:
            return httpx.Response(200, content=body)
        if str(request.url) == "https://prices.example.test/models.sha256":
            return httpx.Response(200, text=digest + "  models.json\n")
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class PriceCatalogTests(unittest.TestCase):
    def test_verified_catalog_is_cached_and_prices_are_per_million(self):
        async def run():
            calls = []
            body, digest = body_and_digest()
            catalog = PriceCatalog(URL, client_for(body, digest, calls), clock=lambda: 100.0)
            self.assertTrue(await catalog.ensure())
            self.assertTrue(await catalog.ensure())
            self.assertEqual(len(calls), 2, "one JSON and one hash request, then served from memory")
            self.assertEqual(catalog.lookup("gpt-sample"), {"input": 1.0, "output": 4.0, "cache_read": 0.1, "cache_write": 2.0})
            await catalog.close()
        asyncio.run(run())

    def test_hash_mismatch_is_rejected_and_not_retried_immediately(self):
        async def run():
            calls = []
            body, _ = body_and_digest()
            clock = [0.0]
            catalog = PriceCatalog(URL, client_for(body, "0" * 64, calls), clock=lambda: clock[0])
            self.assertFalse(await catalog.ensure())
            self.assertIsNone(catalog.lookup("gpt-sample"))
            before = len(calls)
            self.assertFalse(await catalog.ensure())
            self.assertEqual(len(calls), before, "failure is throttled")
            await catalog.close()
        asyncio.run(run())

    def test_stale_catalog_survives_a_failed_refresh(self):
        async def run():
            body, digest = body_and_digest()
            clock = [0.0]
            catalog = PriceCatalog(URL, client_for(body, digest), ttl=10, clock=lambda: clock[0])
            self.assertTrue(await catalog.ensure())
            catalog.client = client_for(body, "f" * 64)
            clock[0] = 11.0
            self.assertTrue(await catalog.ensure())
            self.assertIsNotNone(catalog.lookup("gpt-sample"))
            await catalog.close()
        asyncio.run(run())

    def test_name_matching_handles_prefix_date_suffix_alias_and_missing_prices(self):
        catalog = PriceCatalog("")
        catalog.models = {key.lower(): value for key, value in CATALOG.items()}
        self.assertIsNotNone(catalog.lookup("openai/GPT-Sample"))
        self.assertIsNotNone(catalog.lookup("gpt-sample-2026-10-01"))
        self.assertIsNotNone(catalog.lookup("gpt-sample-20261001"))
        self.assertEqual(catalog.lookup("claude-alias")["input"], 1.0)
        self.assertIsNone(catalog.lookup("no-prices"))
        self.assertIsNone(catalog.lookup("unknown"))
        self.assertIsNone(catalog.lookup(None))

    def test_effective_price_scales_official_price_by_observed_multiplier(self):
        catalog = PriceCatalog("")
        catalog.models = {"gpt-sample": CATALOG["gpt-sample"]}
        row = model_row({"model": "gpt-sample", "cost": 4, "actual_cost": 3, "requests": 2, "total_tokens": 9,
                         "cache_creation_tokens": 5, "account_cost": 77}, catalog)
        self.assertEqual(row["multiplier"], 0.75)
        self.assertEqual(row["actual"]["input"], 0.75)
        self.assertEqual(row["cache_write_tokens"], 5)
        self.assertNotIn("account_cost", row)
        zero = model_row({"model": "gpt-sample", "cost": 0, "actual_cost": 0}, catalog)
        self.assertIsNone(zero["multiplier"])
        self.assertIsNone(zero["actual"])
        self.assertIsNotNone(zero["official"])


if __name__ == "__main__":
    unittest.main()
