import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfoNotFoundError

import httpx

from helper.upstream import Upstream, UpstreamError


def success(data):
    return httpx.Response(200, json={"code": 0, "message": "success", "data": data})


class UpstreamTests(unittest.IsolatedAsyncioTestCase):
    async def use_adapter(self, handler, **client_options):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), **client_options)
        self.addAsyncCleanup(client.aclose)
        return Upstream(SimpleNamespace(upstream_url="https://upstream.test", admin_key="admin-secret", timezone="Asia/Shanghai"), client)

    async def test_all_pages_are_loaded_even_when_upstream_clamps_page_size(self):
        pages = []

        def handler(request):
            page = int(request.url.params["page"])
            pages.append(page)
            self.assertEqual(request.headers["x-api-key"], "admin-secret")
            self.assertEqual(request.url.params["lite"], "true")
            return success({"items": [{"id": page}], "total": 3, "page": page, "page_size": 1, "pages": 3})

        adapter = await self.use_adapter(handler)
        self.assertEqual([item["id"] for item in await adapter.list_accounts()], [1, 2, 3])
        self.assertEqual(pages, [1, 2, 3])

    async def test_empty_list(self):
        adapter = await self.use_adapter(lambda request: success({"items": [], "total": 0}))
        self.assertEqual(await adapter.list_users(), [])

    async def test_pagination_repetition_is_failure_instead_of_silent_truncation(self):
        adapter = await self.use_adapter(lambda request: success({"items": [{"id": 1}], "total": 2}))
        with self.assertRaisesRegex(UpstreamError, "重复"):
            await adapter.list_users()

    async def test_pagination_early_empty_is_failure(self):
        adapter = await self.use_adapter(lambda request: success({"items": [], "total": 2}))
        with self.assertRaisesRegex(UpstreamError, "提前结束"):
            await adapter.list_users()

    async def test_changed_total_is_failure(self):
        adapter = await self.use_adapter(lambda request: success({"items": [{"id": int(request.url.params["page"])}], "total": int(request.url.params["page"]) + 1}))
        with self.assertRaisesRegex(UpstreamError, "读取期间发生变化"):
            await adapter.list_users()

    async def test_invalid_pagination_rejected(self):
        for payload in ({"items": [], "total": "2"}, {"items": [], "total": -1}, {"items": None, "total": 0}):
            adapter = await self.use_adapter(lambda request: success(payload))
            with self.assertRaises(UpstreamError):
                await adapter.list_accounts()

    async def test_user_profile_removes_default_admin_headers_and_cookies(self):
        def handler(request):
            self.assertEqual(request.url.path, "/api/v1/user/profile")
            self.assertEqual(request.headers["authorization"], "Bearer user-jwt")
            self.assertNotIn("x-api-key", request.headers)
            self.assertNotIn("x-goog-api-key", request.headers)
            self.assertNotIn("cookie", request.headers)
            return success({"id": 7, "role": "user"})

        adapter = await self.use_adapter(handler, headers={"x-api-key": "default-admin-secret", "Authorization": "Bearer default-jwt", "x-goog-api-key": "default-google-secret"}, cookies={"session": "admin-cookie"})
        self.assertEqual((await adapter.profile("user-jwt"))["id"], 7)

    async def test_gateway_uses_ordinary_key_without_admin_header(self):
        def handler(request):
            self.assertNotIn("x-api-key", request.headers)
            self.assertEqual(request.headers["authorization"], "Bearer ordinary-api-key")
            self.assertEqual(request.url.path, "/v1/chat/completions")
            self.assertEqual(json.loads(request.content)["model"], "model-1")
            return httpx.Response(200, json={"choices": [{"message": {"content": "42"}}]})

        adapter = await self.use_adapter(handler)
        self.assertIn("choices", await adapter.benchmark_completion("ordinary-api-key", "model-1", "question"))

    async def test_user_models_use_user_endpoint_and_timezone(self):
        def handler(request):
            self.assertEqual(request.url.path, "/api/v1/usage/dashboard/models")
            self.assertEqual(request.url.params["start_date"], "2026-11-01")
            self.assertEqual(request.url.params["end_date"], "2026-11-17")
            self.assertEqual(request.url.params["timezone"], "Asia/Shanghai")
            self.assertNotIn("x-api-key", request.headers)
            return success({"models": []})

        adapter = await self.use_adapter(handler)
        self.assertEqual(await adapter.models("2026-11-01", "2026-11-17", token="jwt"), {"models": []})

    async def test_mutations_use_exact_paths_and_narrow_payloads(self):
        requests = []

        def handler(request):
            requests.append((request.method, request.url.path, json.loads(request.content)))
            return success({})

        adapter = await self.use_adapter(handler)
        await adapter.set_schedulable(3, False)
        await adapter.set_user_status(4, "disabled")
        await adapter.update_menus([{"id": "helper-ranking"}])
        self.assertEqual(requests, [
            ("POST", "/api/v1/admin/accounts/3/schedulable", {"schedulable": False}),
            ("PUT", "/api/v1/admin/users/4", {"status": "disabled"}),
            ("PUT", "/api/v1/admin/settings", {"custom_menu_items": [{"id": "helper-ranking"}]}),
        ])

    async def test_user_breakdown_requests_cost_order_at_the_cap_and_projects_whitelisted_fields(self):
        seen = []

        def handler(request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/v1/admin/dashboard/user-breakdown")
            self.assertEqual(request.headers["x-api-key"], "admin-secret")
            seen.append(dict(request.url.params))
            return success({"users": [
                {"user_id": 7, "email": "alice@example.test", "requests": 10, "input_tokens": 100, "output_tokens": 20, "cache_tokens": 30,
                 "total_tokens": 120, "cost": 99.0, "actual_cost": 1.5, "account_cost": 40.0, "future_field": "never expose"},
                {"user_id": 8, "email": None, "requests": 0, "input_tokens": 0, "output_tokens": 0, "cache_tokens": 0, "total_tokens": 0,
                 "cost": 0, "actual_cost": 0, "account_cost": 0},
            ], "start_date": "2026-11-01", "end_date": "2026-11-26"})

        adapter = await self.use_adapter(handler)
        rows = await adapter.user_breakdown("2026-11-01", "2026-11-26", timezone="Asia/Tokyo")
        self.assertEqual(seen, [{"start_date": "2026-11-01", "end_date": "2026-11-26", "timezone": "Asia/Tokyo", "sort_by": "actual_cost", "limit": "200"}])
        self.assertEqual(rows, [
            {"user_id": 7, "email": "alice@example.test", "requests": 10, "input_tokens": 100, "output_tokens": 20, "cache_tokens": 30,
             "total_tokens": 120, "actual_cost": 1.5},
            {"user_id": 8, "email": "", "requests": 0, "input_tokens": 0, "output_tokens": 0, "cache_tokens": 0, "total_tokens": 0, "actual_cost": 0.0},
        ])
        # Exact key set and order: cost, account_cost and unknown fields never leave the adapter.
        self.assertEqual([list(row) for row in rows], [["user_id", "email", "requests", "input_tokens", "output_tokens", "cache_tokens", "total_tokens", "actual_cost"]] * 2)
        self.assertNotIn("never expose", json.dumps(rows))
        self.assertNotIn("99.0", json.dumps(rows))
        await adapter.user_breakdown("2026-11-01", "2026-11-26")
        self.assertEqual(seen[-1]["timezone"], "Asia/Shanghai")

    async def test_user_breakdown_accepts_an_empty_list_and_non_string_email_as_blank(self):
        adapter = await self.use_adapter(lambda request: success({"users": [{"user_id": 7, "email": 5, "requests": 0, "input_tokens": 0,
                                                                            "output_tokens": 0, "cache_tokens": 0, "total_tokens": 0, "actual_cost": 3}]}))
        self.assertEqual(await adapter.user_breakdown("2026-11-01", "2026-11-26"),
                         [{"user_id": 7, "email": "", "requests": 0, "input_tokens": 0, "output_tokens": 0, "cache_tokens": 0, "total_tokens": 0, "actual_cost": 3.0}])
        adapter = await self.use_adapter(lambda request: success({"users": []}))
        self.assertEqual(await adapter.user_breakdown("2026-11-01", "2026-11-26"), [])

    async def test_user_breakdown_keeps_the_userless_row_upstream_reports_as_zero(self):
        # COALESCE(ul.user_id, 0) in upstream's query: usage without a user must not fail the whole list.
        adapter = await self.use_adapter(lambda request: success({"users": [
            {"user_id": 0, "email": "", "requests": 3, "input_tokens": 30, "output_tokens": 4, "cache_tokens": 5, "total_tokens": 39,
             "cost": 9, "actual_cost": 5.0, "account_cost": 1},
            {"user_id": 7, "email": "alice@example.test", "requests": 1, "input_tokens": 10, "output_tokens": 2, "cache_tokens": 0, "total_tokens": 12,
             "cost": 1, "actual_cost": 1.5, "account_cost": 1},
        ]}))
        self.assertEqual(await adapter.user_breakdown("2026-11-01", "2026-11-26"), [
            {"user_id": 0, "email": "", "requests": 3, "input_tokens": 30, "output_tokens": 4, "cache_tokens": 5, "total_tokens": 39, "actual_cost": 5.0},
            {"user_id": 7, "email": "alice@example.test", "requests": 1, "input_tokens": 10, "output_tokens": 2, "cache_tokens": 0, "total_tokens": 12, "actual_cost": 1.5},
        ])

    async def test_user_breakdown_rejects_malformed_payloads_without_echoing_them(self):
        marker = "private-marker-7f3a@example.test"

        def item(**changes):
            return {"user_id": 7, "email": marker, "requests": 1, "input_tokens": 2, "output_tokens": 3, "cache_tokens": 4, "total_tokens": 5,
                    "cost": 1.0, "actual_cost": 0.5, "account_cost": 0.2, "note": marker} | changes

        def without(key):
            row = item()
            row.pop(key)
            return row

        # An empty dict or string is not an empty list: it must fail instead of showing an empty ranking.
        payloads = [[], None, {}, {"users": None}, {"users": {}}, {"users": ""}, {"users": {"user_id": 7}}]
        payloads += [{"users": [row]} for row in [
            marker, 7, None, {"requests": 1}, without("user_id"), without("requests"), without("actual_cost"),
            item(user_id=True), item(user_id=-7), item(user_id="7"), item(user_id=7.0),
            item(requests=True), item(input_tokens=-1), item(output_tokens=1.0), item(cache_tokens="4"), item(total_tokens=None),
            item(actual_cost=True), item(actual_cost=None), item(actual_cost=-0.5), item(actual_cost="0.5"), item(actual_cost=marker),
            item(actual_cost=10 ** 400), item(actual_cost=-(10 ** 400)),
        ]]
        for payload in payloads:
            with self.subTest(payload=payload):
                adapter = await self.use_adapter(lambda request: success(payload))
                with self.assertRaises(UpstreamError) as caught:
                    await adapter.user_breakdown("2026-11-01", "2026-11-26")
                self.assertEqual(caught.exception.status, 502)
                self.assertNotIn(marker, caught.exception.detail)
                self.assertNotIn(marker, str(caught.exception))
        # NaN and infinities are valid JSON for Python's decoder but are never a cost.
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                text = '{"code": 0, "data": {"users": [{"user_id": 7, "requests": 1, "input_tokens": 2, "output_tokens": 3, "cache_tokens": 4, "total_tokens": 5, "actual_cost": %s}]}}' % value
                adapter = await self.use_adapter(lambda request: httpx.Response(200, text=text, headers={"Content-Type": "application/json"}))
                with self.assertRaises(UpstreamError) as caught:
                    await adapter.user_breakdown("2026-11-01", "2026-11-26")
                self.assertNotIn(value, caught.exception.detail)

    async def test_user_breakdown_validates_dates_and_zone_before_any_request(self):
        adapter = await self.use_adapter(lambda request: self.fail("must not request"))
        with self.assertRaisesRegex(ValueError, "开始日期不得晚于结束日期"):
            await adapter.user_breakdown("2026-11-02", "2026-11-01")
        with self.assertRaises(ZoneInfoNotFoundError):
            await adapter.user_breakdown("2026-11-01", "2026-11-02", timezone="Mars/Olympus_Mons")

    async def test_finite_number_rejects_values_a_float_cannot_hold(self):
        # isfinite() raises OverflowError for such integers; every caller needs a plain refusal instead.
        self.assertTrue(Upstream._finite_number(10 ** 300))
        self.assertTrue(Upstream._finite_number(7))
        for value in (10 ** 400, -(10 ** 400), float("inf"), float("-inf"), float("nan"), Decimal("1e400"), True, "1", None):
            with self.subTest(value=type(value).__name__):
                self.assertFalse(Upstream._finite_number(value))

    async def test_monthly_spending_refuses_a_cost_too_large_for_a_float(self):
        def handler(request):
            if request.url.path.endswith("/users"):
                return success({"total": 1, "items": [{"id": 1, "role": "user", "status": "active"}]})
            return success({"total_actual_cost": 10 ** 400, "total_requests": 5, "total_tokens": 9})

        adapter = await self.use_adapter(handler)
        with self.assertRaisesRegex(UpstreamError, "有效金额"):
            await adapter.monthly_spending("2026-10-01", "2026-10-31", "Asia/Shanghai")

    async def test_upstream_error_body_never_exposes_secrets(self):
        adapter = await self.use_adapter(lambda request: httpx.Response(403, json={"message": "credential admin-secret leaked"}))
        with self.assertRaises(UpstreamError) as caught:
            await adapter.settings()
        self.assertEqual(caught.exception.status, 403)
        self.assertNotIn("admin-secret", caught.exception.detail)

    async def test_network_error_is_sanitized(self):
        def handler(request):
            raise httpx.ConnectError("admin-secret", request=request)

        adapter = await self.use_adapter(handler)
        with self.assertRaises(UpstreamError) as caught:
            await adapter.settings()
        self.assertNotIn("admin-secret", str(caught.exception))

    async def test_envelope_failures_and_html_are_not_success(self):
        responses = [httpx.Response(200, text="<html>login</html>"), httpx.Response(200, json={"code": 1, "message": "failed", "data": {}}), httpx.Response(200, json={"code": False, "data": {}}), httpx.Response(200, json={"data": {}}), httpx.Response(200, json={"code": 0})]
        for response in responses:
            adapter = await self.use_adapter(lambda request: response)
            with self.assertRaises(UpstreamError):
                await adapter.settings()

    async def test_redirect_is_not_followed_with_credential(self):
        seen = []

        def handler(request):
            seen.append(request.url.host)
            return httpx.Response(307, headers={"location": "https://attacker.test/collect"})

        adapter = await self.use_adapter(handler, follow_redirects=True)
        with self.assertRaises(UpstreamError):
            await adapter.settings()
        self.assertEqual(seen, ["upstream.test"])

    async def test_external_and_ambiguous_paths_rejected_before_request(self):
        adapter = await self.use_adapter(lambda request: self.fail("must not request"))
        for path in ("https://attacker.test", "//attacker.test", "/api/v1/admin/../../v1", "/api/v1/admin/a?token=x", "/api/v1/admin/%2e%2e/a"):
            with self.assertRaises(ValueError):
                await adapter.request("GET", path)

    async def test_admin_request_cannot_use_user_token(self):
        adapter = await self.use_adapter(lambda request: self.fail("must not request"))
        with self.assertRaises(ValueError):
            await adapter.request("GET", "/api/v1/admin/users", token="user-jwt")

    async def test_invalid_id_and_mutation_values_rejected(self):
        adapter = await self.use_adapter(lambda request: self.fail("must not request"))
        with self.assertRaises(ValueError):
            await adapter.set_schedulable(-1, True)
        with self.assertRaises(ValueError):
            await adapter.set_schedulable(1, "false")
        with self.assertRaises(ValueError):
            await adapter.set_user_status(1, "deleted")

    async def test_benchmark_key_uses_authenticated_admin_and_does_not_expose_secrets(self):
        def handler(request):
            if request.url.path == "/api/v1/admin/compliance":
                return success({"required": False, "acknowledgement": {"admin_user_id": 9}})
            if request.url.path == "/api/v1/admin/users/9":
                return success({"id": 9, "role": "admin", "status": "active"})
            self.assertEqual(request.url.path, "/api/v1/admin/users/9/api-keys")
            return success({"total": 2, "items": [
                {"id": 41, "user_id": 9, "key": "ordinary-key-secret", "name": "my model key", "status": "active", "group_id": 7, "group": {"id": 7, "name": "XX组", "status": "active"}},
                {"id": 42, "user_id": 10, "key": "other-user-secret", "status": "active", "group_id": 7},
            ]})

        adapter = await self.use_adapter(handler)
        options = await adapter.benchmark_keys()
        self.assertEqual(options, [{"id": 41, "name": "my model key", "group_id": 7, "group_name": "XX组"}])
        self.assertNotIn("secret", json.dumps(options))
        self.assertEqual(await adapter.benchmark_key(41, 7), "ordinary-key-secret")
        with self.assertRaises(UpstreamError):
            await adapter.benchmark_key(41, 8)
        with self.assertRaises(UpstreamError):
            await adapter.benchmark_keys(admin_user_id=10)

    async def test_benchmark_rejects_unusable_keys(self):
        keys = [
            {"id": 1, "user_id": 9, "key": "k", "status": "disabled", "group_id": 7},
            {"id": 2, "user_id": 9, "key": "k", "status": "active", "group_id": None},
            {"id": 3, "user_id": 9, "key": "k", "status": "active", "group_id": 7, "expires_at": "2020-01-01T00:00:00Z"},
            {"id": 4, "user_id": 9, "key": "k", "status": "active", "group_id": 7, "quota": 3, "quota_used": 3},
            {"id": 5, "user_id": 9, "key": "k", "status": "active", "group_id": 7, "group": {"id": 7, "status": "disabled"}},
        ]

        def handler(request):
            if request.url.path.endswith("/compliance"):
                return success({"required": False, "acknowledgement": {"admin_user_id": 9}})
            if request.url.path.endswith("/api-keys"):
                return success({"total": len(keys), "items": keys})
            return success({"id": 9, "role": "admin", "status": "active"})

        adapter = await self.use_adapter(handler)
        self.assertEqual(await adapter.benchmark_keys(), [])

    async def test_benchmark_requires_verified_admin_identity(self):
        adapter = await self.use_adapter(lambda request: success({"required": True}))
        with self.assertRaises(UpstreamError) as caught:
            await adapter.benchmark_keys()
        self.assertEqual(caught.exception.status, 423)

    async def test_embedded_user_key_lookup_is_scoped_and_sanitized_on_every_page(self):
        requested = []

        def handler(request):
            requested.append(request.url.path)
            self.assertNotIn("x-api-key", request.headers)
            self.assertEqual(request.headers["authorization"], "Bearer user-jwt")
            if request.url.path == "/api/v1/user/profile":
                return success({"id": 23, "role": "user", "status": "active"})
            self.assertEqual(request.url.path, "/api/v1/keys")
            page = int(request.url.params["page"])
            self.assertNotIn("token", request.url.params)
            key = {"id": page, "user_id": 23 if page == 2 else 24, "key": f"user-key-secret-{page}", "name": "my key", "status": "active", "group_id": 7, "group": {"id": 7, "name": "XX组", "status": "active"}}
            return success({"total": 2, "items": [key], "page": page})

        adapter = await self.use_adapter(handler, headers={"x-api-key": "admin-secret"})
        options = await adapter.user_benchmark_keys("user-jwt")
        self.assertEqual(options, [{"id": 2, "name": "my key", "group_id": 7, "group_name": "XX组"}])
        self.assertNotIn("secret", json.dumps(options))
        self.assertEqual(requested, ["/api/v1/user/profile", "/api/v1/keys", "/api/v1/keys"])
        self.assertEqual(await adapter.user_benchmark_key("user-jwt", 2, 7), "user-key-secret-2")
        with self.assertRaises(UpstreamError):
            await adapter.user_benchmark_key("user-jwt", 1, 7)
        with self.assertRaises(UpstreamError):
            await adapter.user_benchmark_key("user-jwt", 2, 8)

    async def test_embedded_key_lookup_requires_active_profile_before_listing_keys(self):
        paths = []

        def handler(request):
            paths.append(request.url.path)
            return success({"id": 23, "role": "user", "status": "disabled"})

        adapter = await self.use_adapter(handler)
        with self.assertRaises(UpstreamError):
            await adapter.user_benchmark_keys("jwt")
        self.assertEqual(paths, ["/api/v1/user/profile"])

    async def test_embedded_lookup_shares_key_expiry_and_quota_filters(self):
        def handler(request):
            if request.url.path == "/api/v1/user/profile":
                return success({"id": 23, "role": "user", "status": "active"})
            return success({"total": 3, "items": [
                {"id": 1, "user_id": 23, "key": "expired", "status": "active", "group_id": 7, "expires_at": "2020-01-01T00:00:00Z"},
                {"id": 2, "user_id": 23, "key": "disabled", "status": "disabled", "group_id": 7},
                {"id": 3, "user_id": 23, "key": "empty", "status": "active", "group_id": 7, "quota": 2, "quota_used": 2},
            ]})

        adapter = await self.use_adapter(handler)
        self.assertEqual(await adapter.user_benchmark_keys("jwt"), [])

    async def test_group_and_active_subscription_lists_are_paginated(self):
        def handler(request):
            if request.url.path == "/api/v1/admin/groups":
                return success({"total": 1, "items": [{"id": 7}]})
            self.assertEqual(request.url.path, "/api/v1/admin/subscriptions")
            self.assertEqual(request.url.params["status"], "active")
            self.assertEqual(request.url.params["group_id"], "7")
            return success({"total": 1, "items": [{"id": 21, "group_id": 7}]})

        adapter = await self.use_adapter(handler)
        self.assertEqual(await adapter.list_groups(), [{"id": 7}])
        self.assertEqual(await adapter.list_subscriptions(7), [{"id": 21, "group_id": 7}])

    async def test_subscription_uses_fresh_group_and_progress_preserves_missing_windows(self):
        def handler(request):
            if request.url.path == "/api/v1/admin/subscriptions/21":
                return success({"id": 21, "user_id": 9, "group_id": 7, "status": "active", "group": {"id": 7, "monthly_limit_usd": 100}})
            if request.url.path == "/api/v1/admin/groups/7":
                return success({"id": 7, "monthly_limit_usd": 200})
            self.assertEqual(request.url.path, "/api/v1/admin/subscriptions/21/progress")
            return success({"id": 21, "expires_at": "2026-11-18T00:00:00Z", "monthly": {"limit_usd": 200, "remaining_usd": 150, "resets_at": "2026-11-19T00:00:00Z"}})

        adapter = await self.use_adapter(handler)
        self.assertEqual((await adapter.get_subscription(21))["group"]["monthly_limit_usd"], 200)
        progress = await adapter.subscription_progress(21)
        self.assertNotIn("daily", progress)
        self.assertEqual(progress["monthly"]["remaining_usd"], 150)

    async def test_personal_rate_is_narrow_patch_with_readback(self):
        submitted = []

        def handler(request):
            if request.method == "PUT":
                self.assertEqual(request.url.path, "/api/v1/admin/users/9")
                submitted.append(json.loads(request.content))
                return success({"id": 9})
            self.assertEqual(request.url.path, "/api/v1/admin/groups/7/rate-multipliers")
            value = submitted[-1]["group_rates"]["7"]
            return success([{"user_id": 9, "rate_multiplier": value}, {"user_id": 10, "rate_multiplier": 0.9}])

        adapter = await self.use_adapter(handler)
        await adapter.set_user_group_rate(9, 7, 0.5)
        await adapter.set_user_group_rate(9, 7, None)
        self.assertEqual(submitted, [{"group_rates": {"7": 0.5}}, {"group_rates": {"7": None}}])

    async def test_rate_write_silent_upstream_failure_does_not_pass(self):
        def handler(request):
            if request.method == "PUT":
                return success({"id": 9})
            return success([{"user_id": 9, "rate_multiplier": 1.0}])

        adapter = await self.use_adapter(handler)
        with self.assertRaisesRegex(UpstreamError, "未应用"):
            await adapter.set_user_group_rate(9, 7, 0.5)

    async def test_personal_rate_baseline_uses_reliable_group_side_read(self):
        def handler(request):
            self.assertEqual(request.url.path, "/api/v1/admin/groups/7/rate-multipliers")
            return success([{"user_id": 9, "rate_multiplier": 0.8}, {"user_id": 10, "rpm_override": 5}])

        adapter = await self.use_adapter(handler)
        self.assertEqual(await adapter.user_group_rate(9, 7), 0.8)
        self.assertIsNone(await adapter.user_group_rate(10, 7))
        self.assertIsNone(await adapter.user_group_rate(11, 7))

    async def test_personal_rate_baseline_storage_failure_is_not_absent_override(self):
        adapter = await self.use_adapter(lambda request: httpx.Response(500, json={"message": "db failed"}))
        with self.assertRaises(UpstreamError):
            await adapter.user_group_rate(9, 7)

    async def test_global_rate_only_changes_group_default(self):
        def handler(request):
            self.assertEqual(request.url.path, "/api/v1/admin/groups/7")
            if request.method == "PUT":
                self.assertEqual(json.loads(request.content), {"rate_multiplier": 0.4})
                return success({"id": 7})
            return success({"id": 7, "rate_multiplier": 0.4, "name": "preserved"})

        adapter = await self.use_adapter(handler)
        self.assertEqual((await adapter.set_group_rate(7, 0.4))["name"], "preserved")
        with self.assertRaises(ValueError):
            await adapter.set_group_rate(7, 0)

    async def test_monthly_spending_is_complete_beyond_dashboard_top50(self):
        paths = []

        def handler(request):
            paths.append(request.url.path)
            if request.url.path == "/api/v1/admin/users":
                return success({"total": 53, "items": [{"id": user_id, "role": "admin" if user_id == 1 else "user", "status": "disabled" if user_id == 2 else "active"} for user_id in range(1, 54)]})
            self.assertEqual(request.url.path, "/api/v1/admin/usage/stats")
            self.assertEqual(request.url.params["timezone"], "Asia/Shanghai")
            self.assertEqual(request.url.params["start_date"], "2026-10-01")
            self.assertEqual(request.url.params["end_date"], "2026-10-31")
            self.assertEqual(request.url.params["nocache"], "true")
            uid = int(request.url.params["user_id"])
            return success({"total_actual_cost": uid, "total_requests": 5, "total_tokens": 9})

        adapter = await self.use_adapter(handler)
        rows = await adapter.monthly_spending("2026-10-01", "2026-10-31", "Asia/Shanghai")
        self.assertEqual(len(rows), 51)
        self.assertEqual({row["user_id"] for row in rows}, set(range(3, 54)))
        self.assertNotIn("/api/v1/admin/dashboard/users-ranking", paths)

    async def test_monthly_spending_refuses_partial_aggregate_or_unknown_eligibility(self):
        def handler(request):
            if request.url.path.endswith("/users"):
                return success({"total": 1, "items": [{"id": 1, "role": "user", "status": "active"}]})
            return success({"total_actual_cost": 5})

        adapter = await self.use_adapter(handler)
        with self.assertRaises(UpstreamError):
            await adapter.monthly_spending("2026-10-01", "2026-10-31", "Asia/Shanghai")
        adapter = await self.use_adapter(lambda request: success({"total": 1, "items": [{"id": 1}]}))
        with self.assertRaises(UpstreamError):
            await adapter.monthly_spending("2026-10-01", "2026-10-31", "Asia/Shanghai")

    async def test_balance_credit_has_stable_payload_and_idempotency_key(self):
        captured = []

        def handler(request):
            captured.append(request)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/api/v1/admin/users/9/balance")
            self.assertEqual(request.headers["Idempotency-Key"], "helper-2026-10-user9")
            self.assertEqual(json.loads(request.content), {"balance": 5.25, "operation": "add", "notes": "Helper monthly reward helper-2026-10-user9"})
            return success({"id": 9, "balance": 105.25})

        adapter = await self.use_adapter(handler)
        await adapter.add_balance(9, 5.25, "helper-2026-10-user9")
        self.assertEqual(len(captured), 1)

    async def test_balance_credit_never_retries_after_ambiguous_network_failure(self):
        calls = []

        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("unknown whether credited", request=request)

        adapter = await self.use_adapter(handler)
        with self.assertRaises(UpstreamError):
            await adapter.add_balance(9, 5, "helper-2026-10-user9")
        self.assertEqual(len(calls), 1)

    async def test_only_idempotency_extra_header_is_allowed(self):
        adapter = await self.use_adapter(lambda request: self.fail("must not request"))
        with self.assertRaises(ValueError):
            await adapter.request("POST", "/api/v1/admin/users/9/balance", headers={"x-api-key": "override"})
        with self.assertRaises(ValueError):
            await adapter.request("POST", "/v1/chat/completions", token="userkey", headers={"Idempotency-Key": "key"})
        with self.assertRaises(ValueError):
            await adapter.add_balance(9, 5, "invalid key with spaces")


if __name__ == "__main__":
    unittest.main()
