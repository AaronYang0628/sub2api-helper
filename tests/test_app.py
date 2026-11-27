"""HTTP behavior tests using a fake upstream; no live accounts are changed."""

from copy import deepcopy
import asyncio
from datetime import date, datetime, timedelta, timezone
import tempfile
import unittest
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import httpx

from helper.app import create_app
from helper.config import Config
from helper.pricing_catalog import PriceCatalog
from helper.store import Store
from helper.upstream import UpstreamError


def json_text(value):
    import json
    return json.dumps(value, ensure_ascii=False)


class FakeUpstream:
    def __init__(self):
        self.accounts = [
            {"id": 1, "name": "Night account", "schedulable": False, "status": "active", "credentials": {"secret": "never-expose"}},
            {"id": 2, "name": "Other account", "schedulable": True, "status": "active"},
        ]
        self.users = [
            {"id": 7, "username": "Alice", "email": "alice@example.test", "role": "user", "status": "active", "last_used_at": "2026-10-01T00:00:00Z", "password_hash": "hidden-hash"},
            {"id": 8, "username": "Bob", "email": "bob@example.test", "role": "user", "status": "active"},
            {"id": 99, "username": "Administrator", "email": "admin@example.test", "role": "admin", "status": "active"},
        ]
        self.settings_data = {"custom_menu_items": [{"id": "existing-docs", "label": "Docs", "url": "https://docs.example.test", "visibility": "user", "extra": "preserve"}], "session_binding_enabled": False}
        self.profile_tokens = []
        self.model_tokens = []
        self.model_groups = []
        self.breakdown_calls = []
        self.writes = []
        self.menu_write_applies = True
        self.menu_error = None
        self.closed = False
        # user-breakdown rows as the adapter returns them, plus fields the portal API must never pass on.
        # An administrator and a removed user id both out-spend the ordinary users.
        self.usage = [
            {"user_id": 8, "email": "bob@example.test", "requests": 200, "input_tokens": 5000, "output_tokens": 1000, "cache_tokens": 800,
             "total_tokens": 6000, "actual_cost": 2.5, "cost": 3.0, "account_cost": 99.0, "private_future_field": "secret"},
            {"user_id": 7, "email": "alice@example.test", "requests": 100, "input_tokens": 4000, "output_tokens": 500, "cache_tokens": 300,
             "total_tokens": 4500, "actual_cost": 1.5, "cost": 2.0, "account_cost": 77.0, "private_future_field": "secret"},
            {"user_id": 99, "email": "admin@example.test", "requests": 900, "input_tokens": 90000, "output_tokens": 9000, "cache_tokens": 900,
             "total_tokens": 99000, "actual_cost": 90.0, "cost": 90.0, "account_cost": 9.0, "private_future_field": "secret"},
            {"user_id": 6, "email": "removed@example.test", "requests": 50, "input_tokens": 600, "output_tokens": 60, "cache_tokens": 60,
             "total_tokens": 660, "actual_cost": 6.0, "cost": 6.0, "account_cost": 0.6, "private_future_field": "secret"},
        ]

    async def close(self):
        self.closed = True

    async def list_accounts(self):
        return deepcopy(self.accounts)

    async def set_schedulable(self, account_id, value):
        if account_id <= 0:
            raise ValueError("invalid id")
        self.writes.append(("account", account_id, value))

    async def list_users(self):
        return deepcopy(self.users)

    async def get_user(self, user_id):
        if user_id <= 0:
            raise ValueError("invalid id")
        for user in self.users:
            if user["id"] == user_id:
                return deepcopy(user)
        raise UpstreamError("User does not exist", 404)

    async def set_user_status(self, user_id, value):
        self.writes.append(("user", user_id, value))

    async def settings(self):
        return deepcopy(self.settings_data)

    async def update_menus(self, menus):
        if self.menu_error:
            raise self.menu_error
        self.writes.append(("menus", deepcopy(menus)))
        if self.menu_write_applies:
            self.settings_data["custom_menu_items"] = deepcopy(menus)

    async def profile(self, token):
        self.profile_tokens.append(token)
        identities = {"user-jwt": 7, "other-user-jwt": 8, "admin-jwt": 99}
        if token not in identities:
            raise UpstreamError("Invalid user token", 401)
        return await self.get_user(identities[token])

    async def models(self, start_date, end_date, token=None, group_id=None):
        self.model_tokens.append(token)
        self.model_groups.append(group_id)
        return {"models": [{"model": "gpt-sample", "requests": 5 if token else 500, "input_tokens": 1000, "output_tokens": 500,
                            "cache_creation_tokens": 200, "cache_read_tokens": 4000, "total_tokens": 5700,
                            "cost": 2.0, "actual_cost": 1.0, "account_cost": 99.0}], "scope": "user" if token else "admin"}

    async def list_groups(self):
        return [{"id": 3, "name": "OpenAI 主力", "platform": "openai", "status": "active", "secret": "x"}]

    async def available_group_rows(self, token):
        return [{"id": 3, "name": "OpenAI 主力", "platform": "openai", "status": "active"}]

    async def user_breakdown(self, start_date, end_date, timezone=None):
        self.breakdown_calls.append((start_date, end_date, timezone))
        return deepcopy(self.usage)


class FakeMailer:
    configured = True

    def __init__(self):
        self.sent = []
        self.error = None

    async def send(self, to, subject, body):
        if self.error:
            raise self.error
        self.sent.append((to, subject, body))


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(
            sub2api_url="https://sub2api.example.test",
            sub2api_admin_key="upstream-admin-key-never-expose",
            admin_token="helper-admin-token-never-expose",
            public_url="https://helper.example.test",
            db_path=self.temp.name + "/state.db",
        )
        self.store = Store(self.config.db_path)
        self.upstream = FakeUpstream()
        self.mailer = FakeMailer()
        self.catalog = PriceCatalog("")
        self.catalog.models = {"gpt-sample": {"input_cost_per_token": 1e-06, "output_cost_per_token": 4e-06,
                                              "cache_read_input_token_cost": 1e-07, "cache_creation_input_token_cost": 2e-06}}
        self.catalog.loaded_at = 0.0
        self.catalog.ttl = float("inf")
        self.app = create_app(self.config, upstream=self.upstream, store=self.store, mailer=self.mailer, background=False, price_catalog=self.catalog)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.auth = {"Authorization": "Bearer " + self.config.admin_token}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def schedule(self, **changes):
        return {"name": "Night schedule", "account_ids": [1], "start": "23:00", "end": "09:00", "timezone": "Asia/Shanghai", "weekdays": list(range(7)), "enabled": True} | changes

    def portal(self, token="user-jwt"):
        response = self.client.post("/_helper/api/portal/session", json={"token": token})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(token, response.text)
        return {"X-Portal-Session": response.json()["session"]}

    def enable_modules(self, **values):
        self.store.set_setting("modules", {"benchmark": False, "pricing": False, "ranking": False} | values)

    def test_management_requires_helper_token_and_never_exposes_upstream_secrets(self):
        for path in ("/_helper/api/admin/status", "/_helper/api/admin/accounts", "/_helper/api/admin/users", "/_helper/api/admin/audit", "/_helper/api/admin/schedules", "/_helper/api/admin/modules"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
                self.assertEqual(self.client.get(path, headers={"X-Api-Key": self.config.sub2api_admin_key}).status_code, 401)
                self.assertEqual(self.client.get(path, headers={"Authorization": "Bearer " + self.config.sub2api_admin_key}).status_code, 401)
                response = self.client.get(path, headers=self.auth)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(self.config.sub2api_admin_key, response.text)
                self.assertNotIn(self.config.admin_token, response.text)
                self.assertNotIn("never-expose", response.text)
                self.assertNotIn("password_hash", response.text)

    def test_health_is_public_and_has_no_configuration(self):
        response = self.client.get("/_helper/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertNotIn("upstream_url", response.json())

    def test_benchmark_document_allows_https_image_previews_without_expanding_other_policies(self):
        self.enable_modules(benchmark=True, ranking=True)
        for path in ("/_helper/features/benchmark", "/_helper/console"):
            response = self.client.get(path)
            self.assertIn("img-src 'self' data: https:;", response.headers["Content-Security-Policy"])
            self.assertIn("script-src 'self';", response.headers["Content-Security-Policy"])
            self.assertIn("connect-src 'self';", response.headers["Content-Security-Policy"])
            self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertIn("img-src 'self' data:;", self.client.get("/_helper/features/ranking").headers["Content-Security-Policy"])

    def test_enabled_schedule_ownership_conflicts_but_disabled_schedule_allowed(self):
        first = self.client.post("/_helper/api/admin/schedules", json=self.schedule(), headers=self.auth)
        self.assertEqual(first.status_code, 200, first.text)
        conflict = self.client.post("/_helper/api/admin/schedules", json=self.schedule(name="Overlap"), headers=self.auth)
        self.assertEqual(conflict.status_code, 409)
        inactive = self.client.post("/_helper/api/admin/schedules", json=self.schedule(name="Disabled", enabled=False), headers=self.auth)
        self.assertEqual(inactive.status_code, 200)
        response = self.client.put("/_helper/api/admin/schedules/" + str(inactive.json()["id"]), json=self.schedule(name="Disabled", enabled=True), headers=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.store.list("schedules")), 2)

    def test_unknown_accounts_rejected_without_persisting_schedule(self):
        response = self.client.post("/_helper/api/admin/schedules", json=self.schedule(account_ids=[1, 12345]), headers=self.auth)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.store.list("schedules"), [])

    def test_invalid_windows_and_duplicate_ids_rejected(self):
        for changes in ({"end": "23:00"}, {"start": "25:00"}, {"weekdays": [7]}, {"account_ids": [1, 1]}, {"timezone": "unknown/place"}):
            with self.subTest(changes=changes):
                response = self.client.post("/_helper/api/admin/schedules", json=self.schedule(**changes), headers=self.auth)
                self.assertEqual(response.status_code, 422)

    def test_manual_toggle_requires_schedule_to_be_disabled(self):
        response = self.client.post("/_helper/api/admin/schedules", json=self.schedule(), headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post("/_helper/api/admin/accounts/1/schedulable", json={"schedulable": True}, headers=self.auth).status_code, 409)
        self.assertEqual(self.upstream.writes, [])
        response = self.client.put("/_helper/api/admin/schedules/" + str(response.json()["id"]), json=self.schedule(enabled=False), headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post("/_helper/api/admin/accounts/1/schedulable", json={"schedulable": True}, headers=self.auth).status_code, 200)
        self.assertEqual(self.upstream.writes, [("account", 1, True)])

    def test_enabled_rule_requires_smtp_and_only_one_enabled_rule(self):
        self.mailer.configured = False
        rule = {"name": "Inactive users", "enabled": True, "days": 30, "disable_after_email": True}
        self.assertEqual(self.client.post("/_helper/api/admin/rules", json=rule, headers=self.auth).status_code, 409)
        self.assertEqual(self.store.list("rules"), [])
        self.assertEqual(self.client.post("/_helper/api/admin/rules", json=rule | {"enabled": False}, headers=self.auth).status_code, 200)
        self.mailer.configured = True
        self.assertEqual(self.client.post("/_helper/api/admin/rules", json=rule, headers=self.auth).status_code, 200)
        self.assertEqual(self.client.post("/_helper/api/admin/rules", json=rule | {"name": "Duplicate"}, headers=self.auth).status_code, 409)

    def test_administrator_status_change_is_blocked(self):
        response = self.client.post("/_helper/api/admin/users/99/status", json={"status": "disabled"}, headers=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.upstream.writes, [])

    def test_unknown_user_role_cannot_be_disabled(self):
        self.upstream.users[0].pop("role")
        response = self.client.post("/_helper/api/admin/users/7/status", json={"status": "disabled"}, headers=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.upstream.writes, [])

    def test_negative_ids_are_validation_errors(self):
        for path, body in (("/_helper/api/admin/accounts/-1/schedulable", {"schedulable": False}), ("/_helper/api/admin/users/-1/status", {"status": "disabled"}), ("/_helper/api/admin/users/-1/email", {"subject": "Hello", "body": "Message"})):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, json=body, headers=self.auth).status_code, 422)
        self.assertEqual(self.upstream.writes, [])

    def test_manual_email_does_not_change_user_status_or_expose_mail_errors(self):
        response = self.client.post("/_helper/api/admin/users/7/email", json={"subject": "Hello", "body": "Your account"}, headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.mailer.sent, [("alice@example.test", "Hello", "Your account")])
        self.assertEqual(self.upstream.writes, [])
        self.mailer.error = RuntimeError("sensitive-smtp-password")
        response = self.client.post("/_helper/api/admin/users/7/email", json={"subject": "Hello", "body": "Your account"}, headers=self.auth)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("sensitive-smtp-password", response.text)
        self.assertNotIn("sensitive-smtp-password", str(self.store.audits()))

    def test_portal_token_does_not_authorize_admin_endpoints(self):
        portal = self.portal()
        self.assertEqual(self.client.get("/_helper/api/admin/status", headers=portal).status_code, 401)
        self.assertEqual(self.client.get("/_helper/api/portal/modules", headers=self.auth).status_code, 401)
        self.assertEqual(self.client.get("/_helper/api/portal/modules", headers=portal).status_code, 200)
        self.assertTrue(all(token == "user-jwt" for token in self.upstream.profile_tokens))
        self.assertNotIn("user-jwt", str(self.store.audits()))

    def test_disabled_user_and_bound_upstream_sessions_rejected(self):
        self.upstream.users[0]["status"] = "disabled"
        self.assertEqual(self.client.post("/_helper/api/portal/session", json={"token": "user-jwt"}).status_code, 403)
        self.upstream.settings_data["session_binding_enabled"] = True
        count = len(self.upstream.profile_tokens)
        self.assertEqual(self.client.post("/_helper/api/portal/session", json={"token": "other-user-jwt"}).status_code, 409)
        self.assertEqual(len(self.upstream.profile_tokens), count)

    def test_malformed_profile_cannot_create_a_portal_session(self):
        for profile in ({"username": "unconfirmed"}, {"id": 7, "role": "user"}, {"id": -1, "role": "user", "status": "active"}, None):
            with self.subTest(profile=profile):
                self.upstream.profile = AsyncMock(return_value=profile)
                response = self.client.post("/_helper/api/portal/session", json={"token": "user-jwt"})
                self.assertEqual(response.status_code, 502)
                self.assertNotIn("session", response.json())

    def test_user_models_use_user_bearer_and_expose_official_vs_effective_prices(self):
        self.enable_modules(pricing=True)
        response = self.client.get("/_helper/api/portal/models?start_date=2026-11-01&end_date=2026-11-17&group_id=3", headers=self.portal())
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(self.upstream.model_tokens, ["user-jwt"])
        self.assertEqual(self.upstream.model_groups, [3])
        self.assertEqual(body["groups"], [{"id": 3, "name": "OpenAI 主力", "platform": "openai"}])
        row = body["models"][0]
        self.assertEqual(row["multiplier"], 0.5)
        self.assertEqual(row["official"], {"input": 1.0, "output": 4.0, "cache_read": 0.1, "cache_write": 2.0})
        self.assertEqual(row["actual"], {"input": 0.5, "output": 2.0, "cache_read": 0.05, "cache_write": 1.0})
        self.assertNotIn("account_cost", response.text)
        self.assertEqual(self.client.get("/_helper/api/admin/analytics/models", headers=self.auth).status_code, 200)
        self.assertEqual(self.upstream.model_tokens, ["user-jwt", None])

    def test_models_reject_groups_the_user_cannot_use_but_admin_may_query_any(self):
        self.enable_modules(pricing=True)
        self.assertEqual(self.client.get("/_helper/api/portal/models?group_id=4", headers=self.portal()).status_code, 403)
        self.assertEqual(self.client.get("/_helper/api/portal/models?group_id=0", headers=self.portal()).status_code, 422)
        self.assertEqual(self.upstream.model_tokens, [])
        response = self.client.get("/_helper/api/portal/models?group_id=4", headers=self.portal("admin-jwt"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.upstream.model_tokens, [None])

    def test_models_without_catalog_entry_show_no_official_price(self):
        self.enable_modules(pricing=True)
        self.catalog.models = {}
        row = self.client.get("/_helper/api/portal/models", headers=self.portal()).json()["models"][0]
        self.assertIsNone(row["official"])
        self.assertIsNone(row["actual"])

    ROW_KEYS = ["rank", "label", "requests", "input_tokens", "output_tokens", "cache_tokens", "total_tokens", "actual_cost", "is_me", "reward"]

    def monthly_rewards(self, zone="Asia/Shanghai", enabled=True, rewards=("30", "20", "10"), **extra):
        rule = {"type": "monthly_rewards", "enabled": enabled, "timezone": zone, "mode": "approval", "enabled_at": "2026-01-01T00:00:00+00:00",
                "last_error": "private", "rewards": [{"rank": rank, "amount": amount} for rank, amount in zip((1, 2, 3), rewards)]} | extra
        return self.store.create("automations", rule)

    def test_user_ranking_is_anonymous_projection_and_marks_self(self):
        self.enable_modules(ranking=True)
        response = self.client.get("/_helper/api/portal/ranking", headers=self.portal())
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"start_date", "end_date", "timezone", "ranking", "rewards", "truncated"})
        self.assertIsNone(body["rewards"])
        self.assertIs(body["truncated"], False)
        rows = body["ranking"]
        # Ordinary viewers get rank pseudonyms only: no names, e-mail, user ids or upstream fields of any kind.
        self.assertEqual([list(row) for row in rows], [self.ROW_KEYS, self.ROW_KEYS])
        self.assertEqual(rows, [
            {"rank": 1, "label": "用户 #1", "requests": 200, "input_tokens": 5000, "output_tokens": 1000, "cache_tokens": 800,
             "total_tokens": 6000, "actual_cost": 2.5, "is_me": False, "reward": None},
            {"rank": 2, "label": "用户 #2", "requests": 100, "input_tokens": 4000, "output_tokens": 500, "cache_tokens": 300,
             "total_tokens": 4500, "actual_cost": 1.5, "is_me": True, "reward": None},
        ])
        for forbidden in ("Alice", "Bob", "alice@example.test", "bob@example.test", "Administrator", "admin@example.test",
                          "removed@example.test", "email", "username", "user_id", "private_future_field", "account_cost", "secret"):
            self.assertNotIn(forbidden, response.text)
        start, end, zone = self.upstream.breakdown_calls[0]
        self.assertEqual((body["start_date"], body["end_date"], body["timezone"]), (start, end, zone))
        self.assertEqual(zone, "Asia/Shanghai")
        self.assertTrue(start.endswith("-01") and start[:7] == end[:7])

    def test_ranking_lists_only_reward_eligible_users_and_exposes_reward_summary(self):
        self.enable_modules(ranking=True)
        self.upstream.users[1]["status"] = "disabled"
        self.monthly_rewards()
        body = self.client.get("/_helper/api/portal/ranking", headers=self.portal()).json()
        self.assertEqual([(row["rank"], row["label"], row["reward"]) for row in body["ranking"]], [(1, "用户 #1", "30.00")])
        rewards = body["rewards"]
        self.assertEqual(set(rewards), {"rewards", "timezone", "mode", "settlement_at"})
        self.assertEqual(rewards["rewards"], [{"rank": 1, "amount": "30"}, {"rank": 2, "amount": "20"}, {"rank": 3, "amount": "10"}])
        self.assertEqual((rewards["mode"], rewards["timezone"]), ("approval", "Asia/Shanghai"))
        # The last second of the ranking month is the statistics cutoff, never the first day of the next one.
        self.assertRegex(rewards["settlement_at"], r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T23:59:59[+]08:00$")
        cutoff = date.fromisoformat(rewards["settlement_at"][:10])
        self.assertEqual((cutoff + timedelta(days=1)).day, 1, "cutoff must be the last day of the month")
        self.assertEqual(cutoff.replace(day=1).isoformat(), body["start_date"])
        self.assertEqual(self.upstream.breakdown_calls, [(body["start_date"], body["end_date"], "Asia/Shanghai")])
        self.assertNotIn("private", json_text(body))
        self.assertNotIn("enabled_at", json_text(body))

    def test_ranking_cutoff_is_the_last_second_of_the_window_month_in_the_rule_timezone(self):
        self.enable_modules(ranking=True)
        for zone in ("UTC", "America/New_York", "Pacific/Chatham"):
            with self.subTest(zone=zone):
                rule_id = self.monthly_rewards(zone)["id"]
                body = self.client.get("/_helper/api/portal/ranking", headers=self.portal()).json()
                self.store.delete("automations", rule_id)
                self.assertEqual(self.upstream.breakdown_calls[-1], (body["start_date"], body["end_date"], zone))
                settlement_at = body["rewards"]["settlement_at"]
                self.assertRegex(settlement_at, r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T23:59:59[+-][0-9]{2}:[0-9]{2}$")
                cutoff = datetime.fromisoformat(settlement_at)
                self.assertEqual(cutoff.strftime("%Y-%m"), body["start_date"][:7])
                # The cutoff closes the month the window starts in: one second later is the next month's
                # first midnight, the boundary the engine settles after.
                self.assertEqual((cutoff + timedelta(seconds=1)).strftime("%dT%H:%M:%S"), "01T00:00:00")
                self.assertTrue(date.fromisoformat(body["end_date"]) <= cutoff.date())

    def test_ranking_without_enabled_rule_has_no_rewards_and_uses_the_configured_timezone(self):
        self.enable_modules(ranking=True)
        self.monthly_rewards(zone="Asia/Tokyo", enabled=False)
        body = self.client.get("/_helper/api/portal/ranking", headers=self.portal()).json()
        self.assertIsNone(body["rewards"])
        self.assertEqual([row["rank"] for row in body["ranking"]], [1, 2])
        self.assertTrue(all(row["reward"] is None for row in body["ranking"]))
        self.assertEqual(body["timezone"], "Asia/Shanghai")
        self.assertEqual(self.upstream.breakdown_calls[-1][2], "Asia/Shanghai")

    def test_ranking_orders_like_the_settlement_and_marks_provisional_rewards(self):
        self.enable_modules(ranking=True)
        self.upstream.users += [{"id": user_id, "username": name, "email": name.lower() + "@example.test", "role": "user", "status": "active"}
                                for user_id, name in ((9, "Carol"), (10, "Dave"), (11, "Erin"), (12, "Frank"))]

        def usage(user_id, cost, tokens):
            return {"user_id": user_id, "email": "", "requests": 7, "input_tokens": tokens - 15, "output_tokens": 10, "cache_tokens": 5,
                    "total_tokens": tokens, "actual_cost": cost, "cost": cost * 2, "account_cost": 1.0}

        # Returned in an order that disagrees with every tie-break. The cost leader has the fewest tokens;
        # 9 and 8 tie on cost and split by tokens against their user ids; 10 and 11 tie on both and split
        # by user id; 12 spent nothing.
        self.upstream.usage = [usage(12, 0, 50), usage(11, 1, 9000), usage(10, 1, 9000), usage(9, 3, 9000), usage(8, 3, 500), usage(7, 5, 100)]
        self.monthly_rewards(zone="Asia/Tokyo", rewards=("30", "20.5", "10.000000"))
        body = self.client.get("/_helper/api/portal/ranking", headers=self.portal("admin-jwt")).json()
        self.assertEqual([(row["rank"], row["label"], row["user_id"], row["total_tokens"], row["is_me"], row["reward"]) for row in body["ranking"]], [
            (1, "Alice", 7, 100, False, "30.00"), (2, "Carol", 9, 9000, False, "20.50"), (3, "Bob", 8, 500, False, "10.00"),
            (4, "Dave", 10, 9000, False, None), (5, "Erin", 11, 9000, False, None), (6, "Frank", 12, 50, False, None)])
        self.assertEqual(list(body["ranking"][0]), self.ROW_KEYS[:-1] + ["user_id", "reward"])
        # Day boundaries use the rule timezone, the same zone the month window is computed in.
        self.assertEqual(self.upstream.breakdown_calls, [(body["start_date"], body["end_date"], "Asia/Tokyo")])
        self.assertEqual(body["timezone"], "Asia/Tokyo")
        self.assertRegex(body["rewards"]["settlement_at"], r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T23:59:59[+]09:00$")
        ordinary = self.client.get("/_helper/api/portal/ranking", headers=self.portal()).json()
        self.assertEqual([(row["rank"], row["label"], row["is_me"], row["reward"]) for row in ordinary["ranking"]], [
            (1, "用户 #1", True, "30.00"), (2, "用户 #2", False, "20.50"), (3, "用户 #3", False, "10.00"),
            (4, "用户 #4", False, None), (5, "用户 #5", False, None), (6, "用户 #6", False, None)])
        self.assertEqual([list(row) for row in ordinary["ranking"]], [self.ROW_KEYS] * 6)
        for forbidden in ("Alice", "Bob", "Carol", "Dave", "Erin", "Frank", "@example.test", "user_id", "account_cost", "secret"):
            self.assertNotIn(forbidden, json_text(ordinary))

    def test_ranking_gives_zero_spend_rows_no_reward_even_in_the_top_three(self):
        self.enable_modules(ranking=True)
        self.upstream.usage = [{"user_id": 7, "requests": 9, "input_tokens": 90, "output_tokens": 10, "cache_tokens": 0, "total_tokens": 100, "actual_cost": 5},
                               {"user_id": 8, "requests": 3, "input_tokens": 30, "output_tokens": 5, "cache_tokens": 0, "total_tokens": 35, "actual_cost": 0}]
        self.monthly_rewards()
        body = self.client.get("/_helper/api/portal/ranking", headers=self.portal()).json()
        self.assertEqual([(row["rank"], row["is_me"], row["reward"]) for row in body["ranking"]], [(1, True, "30.00"), (2, False, None)])

    def test_ranking_drops_usage_without_a_user_instead_of_failing(self):
        self.enable_modules(ranking=True)
        # Upstream reports usage that has no user as user_id 0 (COALESCE(ul.user_id, 0)). It spends the most,
        # but it is nobody's reward: it must neither break the page nor take a rank.
        self.upstream.usage = [{"user_id": 0, "email": "", "requests": 50, "input_tokens": 5000, "output_tokens": 500, "cache_tokens": 0, "total_tokens": 5500, "actual_cost": 99},
                               {"user_id": 8, "requests": 3, "input_tokens": 30, "output_tokens": 5, "cache_tokens": 0, "total_tokens": 35, "actual_cost": 1},
                               {"user_id": 7, "requests": 9, "input_tokens": 90, "output_tokens": 10, "cache_tokens": 0, "total_tokens": 100, "actual_cost": 5}]
        self.monthly_rewards()
        body = self.client.get("/_helper/api/portal/ranking", headers=self.portal("admin-jwt")).json()
        self.assertEqual([(row["rank"], row["user_id"], row["label"], row["reward"]) for row in body["ranking"]],
                         [(1, 7, "Alice", "30.00"), (2, 8, "Bob", "20.00")])
        self.assertIs(body["truncated"], False)

    def test_ranking_gives_a_rank_without_amount_no_reward(self):
        self.enable_modules(ranking=True)
        self.upstream.users.append({"id": 9, "username": "Carol", "email": "carol@example.test", "role": "user", "status": "active"})
        self.upstream.usage.append({"user_id": 9, "requests": 1, "input_tokens": 100, "output_tokens": 10, "cache_tokens": 0, "total_tokens": 110, "actual_cost": 1.0})
        self.monthly_rewards(rewards=("30", "0", "10"))
        # The engine skips a rank whose amount is zero instead of crediting it, so no reward is promised for it.
        body = self.client.get("/_helper/api/portal/ranking", headers=self.portal()).json()
        self.assertEqual([(row["rank"], row["reward"]) for row in body["ranking"]], [(1, "30.00"), (2, None), (3, "10.00")])
        self.assertEqual(body["rewards"]["rewards"], [{"rank": 1, "amount": "30"}, {"rank": 2, "amount": "0"}, {"rank": 3, "amount": "10"}])

    def test_ranking_reports_a_capped_upstream_list_even_after_ineligible_rows_are_dropped(self):
        self.enable_modules(ranking=True)
        self.upstream.users.append({"id": 1100, "username": "Zed", "email": "zed@example.test", "role": "user", "status": "active"})
        self.upstream.usage = [{"user_id": user_id, "requests": 1, "input_tokens": 10, "output_tokens": 5, "cache_tokens": 0, "total_tokens": 15, "actual_cost": 0.5}
                               for user_id in range(1000, 1200)]  # the cap; 199 of these ids belong to unlisted users
        body = self.client.get("/_helper/api/portal/ranking", headers=self.portal()).json()
        self.assertIs(body["truncated"], True)
        self.assertEqual([row["label"] for row in body["ranking"]], ["用户 #1"])
        self.upstream.usage.pop()
        body = self.client.get("/_helper/api/portal/ranking", headers=self.portal()).json()
        self.assertIs(body["truncated"], False)
        self.assertEqual(len(body["ranking"]), 1)

    def test_ranking_shows_the_rule_amount_without_rounding(self):
        self.enable_modules(ranking=True)
        self.upstream.users += [{"id": user_id, "username": name, "email": name.lower() + "@example.test", "role": "user", "status": "active"}
                                for user_id, name in ((9, "Carol"), (10, "Dave"))]
        self.upstream.usage = [{"user_id": user_id, "requests": 1, "input_tokens": 100, "output_tokens": 10, "cache_tokens": 0, "total_tokens": 110, "actual_cost": float(cost)}
                               for user_id, cost in ((7, 5), (8, 3), (9, 2), (10, 1))]
        self.monthly_rewards(rewards=("0.004", "12.345", "0.005"))
        # The engine credits the exact amount, so a sub-cent prize must not read as $0.00.
        body = self.client.get("/_helper/api/portal/ranking", headers=self.portal()).json()
        self.assertEqual([row["reward"] for row in body["ranking"]], ["0.004", "12.345", "0.005", None])

    def test_ranking_promises_no_reward_for_a_rank_the_capped_list_cannot_settle(self):
        self.enable_modules(ranking=True)
        self.upstream.users += [{"id": user_id, "username": f"user{user_id}", "email": f"user{user_id}@example.test", "role": "user", "status": "active"}
                                for user_id in range(1000, 1200)]
        self.upstream.usage = [{"user_id": user_id, "requests": 1, "input_tokens": 10, "output_tokens": 5, "cache_tokens": 0, "total_tokens": 15, "actual_cost": 1.0}
                               for user_id in range(1000, 1200)]
        self.monthly_rewards()
        # Upstream cuts after ORDER BY actual_cost DESC without a tie-break. Every row shares the cut-off cost,
        # so an omitted peer could take any of the top ranks: nothing is promised until the list is complete.
        body = self.client.get("/_helper/api/portal/ranking", headers=self.portal("admin-jwt")).json()
        self.assertIs(body["truncated"], True)
        self.assertEqual([row["rank"] for row in body["ranking"][:3]], [1, 2, 3])
        self.assertTrue(all(row["reward"] is None and row["tentative"] is True for row in body["ranking"]))
        # Only the rows sharing the cut-off cost are provisional; rows safely above the cut keep their reward.
        for user_id, cost in ((1000, 5), (1001, 4), (1002, 3)):
            next(row for row in self.upstream.usage if row["user_id"] == user_id)["actual_cost"] = float(cost)
        body = self.client.get("/_helper/api/portal/ranking", headers=self.portal("admin-jwt")).json()
        self.assertEqual([(row["rank"], row["user_id"], row["reward"], row.get("tentative")) for row in body["ranking"][:4]],
                         [(1, 1000, "30.00", None), (2, 1001, "20.00", None), (3, 1002, "10.00", None), (4, 1003, None, True)])
        self.assertTrue(all(row.get("tentative") is True for row in body["ranking"][3:]))
        self.assertEqual(list(body["ranking"][0]), self.ROW_KEYS[:-1] + ["user_id", "reward"])
        self.assertEqual(list(body["ranking"][3]), self.ROW_KEYS[:-1] + ["user_id", "reward", "tentative"])
        self.upstream.usage.pop()
        body = self.client.get("/_helper/api/portal/ranking", headers=self.portal()).json()
        self.assertIs(body["truncated"], False)
        self.assertEqual([row["reward"] for row in body["ranking"][:3]], ["30.00", "20.00", "10.00"])
        self.assertTrue(all("tentative" not in row for row in body["ranking"]))
        self.assertEqual([list(row) for row in body["ranking"]], [self.ROW_KEYS] * 199)

    def test_admin_portal_ranking_shows_real_names_and_ids_only(self):
        self.enable_modules(ranking=True)
        response = self.client.get("/_helper/api/portal/ranking", headers=self.portal("admin-jwt"))
        self.assertEqual(response.status_code, 200)
        rows = response.json()["ranking"]
        self.assertEqual([(row["rank"], row["label"], row["user_id"], row["is_me"]) for row in rows],
                         [(1, "Bob", 8, False), (2, "Alice", 7, False)])
        self.assertEqual([list(row) for row in rows], [self.ROW_KEYS[:-1] + ["user_id", "reward"]] * 2)
        self.assertEqual(rows[0], {"rank": 1, "label": "Bob", "requests": 200, "input_tokens": 5000, "output_tokens": 1000, "cache_tokens": 800,
                                   "total_tokens": 6000, "actual_cost": 2.5, "is_me": False, "user_id": 8, "reward": None})
        for forbidden in ("private_future_field", "account_cost", "Administrator", "admin@example.test", "removed@example.test", "secret"):
            self.assertNotIn(forbidden, response.text)

    def test_admin_ranking_label_falls_back_to_email_then_user_id(self):
        self.enable_modules(ranking=True)
        self.upstream.users[0].pop("username")
        self.upstream.users[1].update(username="", email=None)
        rows = self.client.get("/_helper/api/portal/ranking", headers=self.portal("admin-jwt")).json()["ranking"]
        self.assertEqual([(row["label"], row["user_id"]) for row in rows], [("用户 #8", 8), ("alice@example.test", 7)])
        self.assertEqual(all(list(row) == self.ROW_KEYS[:-1] + ["user_id", "reward"] for row in rows), True)

    def test_disabled_module_denies_portal_data_and_page(self):
        portal = self.portal()
        self.assertEqual(self.client.get("/_helper/api/portal/ranking", headers=portal).status_code, 404)
        self.assertEqual(self.client.get("/_helper/features/ranking").status_code, 404)
        self.assertEqual(self.client.get("/_helper/features/unknown").status_code, 404)

    def test_menu_sync_preserves_unrelated_entries_and_commits_after_verification(self):
        original = deepcopy(self.upstream.settings_data["custom_menu_items"][0])
        response = self.client.put("/_helper/api/admin/modules", json={"pricing": True}, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        menus = self.upstream.settings_data["custom_menu_items"]
        self.assertEqual(menus[0], original)
        self.assertEqual(menus[1]["id"], "sub2api-helper-pricing")
        self.assertEqual(menus[1]["url"], "https://helper.example.test/_helper/features/pricing")
        self.assertEqual(self.store.get_setting("modules"), {"benchmark": False, "pricing": True, "ranking": False})
        response = self.client.put("/_helper/api/admin/modules", json={}, headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.upstream.settings_data["custom_menu_items"], [original])

    def test_unconfirmed_or_failed_menu_write_never_changes_local_visibility(self):
        self.upstream.menu_write_applies = False
        response = self.client.put("/_helper/api/admin/modules", json={"ranking": True}, headers=self.auth)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.store.get_setting("modules", {}), {})
        self.assertEqual(self.client.get("/_helper/features/ranking").status_code, 404)
        self.upstream.menu_error = UpstreamError("Permission denied", 403)
        response = self.client.put("/_helper/api/admin/modules", json={"ranking": True}, headers=self.auth)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.store.get_setting("modules", {}), {})

    def test_malformed_upstream_menu_is_handled_as_incompatible(self):
        self.upstream.settings_data["custom_menu_items"] = [None]
        response = self.client.put("/_helper/api/admin/modules", json={"ranking": True}, headers=self.auth)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.upstream.writes, [])

    def test_date_validation_prevents_unbounded_or_inverted_requests(self):
        for query in ("start_date=2026-11-17&end_date=2026-01-01", "start_date=2020-01-01&end_date=2026-11-17", "start_date=tomorrow"):
            with self.subTest(query=query):
                response = self.client.get("/_helper/api/admin/analytics/models?" + query, headers=self.auth)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.upstream.model_tokens, [])

    def test_email_templates_crud_and_benchmark_summary_persistence(self):
        created = self.client.post("/_helper/api/admin/email-templates", json={
            "name": "维护通知", "subject": "计划维护 {username}", "body": "请知悉 {email}",
        }, headers=self.auth)
        self.assertEqual(created.status_code, 200, created.text)
        tid = created.json()["id"]
        listed = self.client.get("/_helper/api/admin/email-templates", headers=self.auth).json()
        self.assertEqual([t["id"] for t in listed], [tid])
        updated = self.client.put(f"/_helper/api/admin/email-templates/{tid}", json={
            "name": "紧急通知", "subject": "更新主题", "body": "更新正文",
        }, headers=self.auth)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["name"], "紧急通知")
        self.assertEqual(self.client.delete(f"/_helper/api/admin/email-templates/{tid}", headers=self.auth).status_code, 200)
        self.assertEqual(self.client.get("/_helper/api/admin/email-templates", headers=self.auth).json(), [])
        # Benchmark run recording keeps minimal summary only (no key/prompts/answers) and prunes to 20
        for idx in range(22):
            self.store.record_benchmark_run(
                group_id=3, model="gpt-5", api_format="responses",
                passed=4 if idx < 21 else 2, graded=5, total=5, error_count=0, latency_ms=120, status="ok",
            )
        count = self.store._db.execute(
            "SELECT COUNT(*) FROM benchmark_runs WHERE group_id=? AND model=?", (3, "gpt-5")
        ).fetchone()[0]
        self.assertEqual(count, 20)
        summary = self.store.benchmark_summaries([3])[(3, "gpt-5")]
        self.assertEqual(len(summary["graded"]), 2)
        self.assertEqual((summary["graded"][0]["passed"], summary["graded"][1]["passed"]), (2, 4))


class SchedulerConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_edit_waits_for_an_inflight_reconciliation(self):
        """A stop action cannot race a stale tick into writing after it completes."""
        store = Store(":memory:")
        upstream = FakeUpstream()
        config = Config(sub2api_url="https://sub2api.example.test", sub2api_admin_key="upstream-secret", admin_token="helper-local-secret")
        app = create_app(config, upstream=upstream, store=store, mailer=FakeMailer(), background=False)
        schedule = {"name": "Night", "account_ids": [1], "start": "23:00", "end": "09:00", "timezone": "Asia/Shanghai", "weekdays": list(range(7)), "enabled": True}
        saved = store.create("schedules", schedule)
        entered = asyncio.Event()
        resume = asyncio.Event()
        original_list = upstream.list_accounts

        async def delayed_accounts():
            entered.set()
            await resume.wait()
            return await original_list()

        upstream.list_accounts = delayed_accounts
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://helper.test") as client:
            tick = asyncio.create_task(app.state.engine.tick(datetime(2026, 11, 17, 15, tzinfo=timezone.utc)))
            await asyncio.wait_for(entered.wait(), 1)
            update = asyncio.create_task(client.put("/_helper/api/admin/schedules/" + str(saved["id"]), headers={"Authorization": "Bearer " + config.admin_token}, json=schedule | {"enabled": False}))
            try:
                await asyncio.sleep(0.02)
                self.assertFalse(update.done(), "Schedule mutation must wait until the current tick finishes")
                self.assertTrue(store.get("schedules", saved["id"])["enabled"])
            finally:
                resume.set()
                await asyncio.wait_for(tick, 1)
                response = await asyncio.wait_for(update, 1)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(store.get("schedules", saved["id"])["enabled"])
                self.assertEqual(upstream.writes, [("account", 1, True)])
                await upstream.close()
                store.close()


if __name__ == "__main__":
    unittest.main()
