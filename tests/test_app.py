"""HTTP behavior tests using a fake upstream; no live accounts are changed."""

from copy import deepcopy
import asyncio
from datetime import datetime, timezone
import tempfile
import unittest
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import httpx

from helper.app import create_app
from helper.config import Config
from helper.store import Store
from helper.upstream import UpstreamError


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
        self.writes = []
        self.menu_write_applies = True
        self.menu_error = None
        self.closed = False

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

    async def models(self, start_date, end_date, token=None):
        self.model_tokens.append(token)
        return {"models": [{"model": "sample", "requests": 5 if token else 500}], "scope": "user" if token else "admin"}

    async def ranking(self, start_date, end_date):
        return {
            "ranking": [
                {"user_id": 8, "username": "Bob", "email": "bob@example.test", "requests": 200, "tokens": 900, "actual_cost": 2.5, "private_future_field": "secret"},
                {"user_id": 7, "username": "Alice", "email": "alice@example.test", "requests": 100, "tokens": 800, "actual_cost": 1.5},
            ],
            "total_cost": 999,
            "admin_future_field": {"secret": "never expose"},
        }


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
        self.app = create_app(self.config, upstream=self.upstream, store=self.store, mailer=self.mailer, background=False)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.auth = {"Authorization": "Bearer " + self.config.admin_token}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def schedule(self, **changes):
        return {"name": "Night schedule", "account_ids": [1], "start": "23:00", "end": "09:00", "timezone": "Asia/Shanghai", "weekdays": list(range(7)), "enabled": True} | changes

    def portal(self, token="user-jwt"):
        response = self.client.post("/api/portal/session", json={"token": token})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(token, response.text)
        return {"X-Portal-Session": response.json()["session"]}

    def enable_modules(self, **values):
        self.store.set_setting("modules", {"benchmark": False, "pricing": False, "ranking": False} | values)

    def test_management_requires_helper_token_and_never_exposes_upstream_secrets(self):
        for path in ("/api/status", "/api/accounts", "/api/users", "/api/audit", "/api/schedules", "/api/modules"):
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
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertNotIn("upstream_url", response.json())

    def test_enabled_schedule_ownership_conflicts_but_disabled_schedule_allowed(self):
        first = self.client.post("/api/schedules", json=self.schedule(), headers=self.auth)
        self.assertEqual(first.status_code, 200, first.text)
        conflict = self.client.post("/api/schedules", json=self.schedule(name="Overlap"), headers=self.auth)
        self.assertEqual(conflict.status_code, 409)
        inactive = self.client.post("/api/schedules", json=self.schedule(name="Disabled", enabled=False), headers=self.auth)
        self.assertEqual(inactive.status_code, 200)
        response = self.client.put("/api/schedules/" + str(inactive.json()["id"]), json=self.schedule(name="Disabled", enabled=True), headers=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.store.list("schedules")), 2)

    def test_unknown_accounts_rejected_without_persisting_schedule(self):
        response = self.client.post("/api/schedules", json=self.schedule(account_ids=[1, 12345]), headers=self.auth)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.store.list("schedules"), [])

    def test_invalid_windows_and_duplicate_ids_rejected(self):
        for changes in ({"end": "23:00"}, {"start": "25:00"}, {"weekdays": [7]}, {"account_ids": [1, 1]}, {"timezone": "unknown/place"}):
            with self.subTest(changes=changes):
                response = self.client.post("/api/schedules", json=self.schedule(**changes), headers=self.auth)
                self.assertEqual(response.status_code, 422)

    def test_manual_toggle_requires_schedule_to_be_disabled(self):
        response = self.client.post("/api/schedules", json=self.schedule(), headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post("/api/accounts/1/schedulable", json={"schedulable": True}, headers=self.auth).status_code, 409)
        self.assertEqual(self.upstream.writes, [])
        response = self.client.put("/api/schedules/" + str(response.json()["id"]), json=self.schedule(enabled=False), headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post("/api/accounts/1/schedulable", json={"schedulable": True}, headers=self.auth).status_code, 200)
        self.assertEqual(self.upstream.writes, [("account", 1, True)])

    def test_enabled_rule_requires_smtp_and_only_one_enabled_rule(self):
        self.mailer.configured = False
        rule = {"name": "Inactive users", "enabled": True, "days": 30, "disable_after_email": True}
        self.assertEqual(self.client.post("/api/rules", json=rule, headers=self.auth).status_code, 409)
        self.assertEqual(self.store.list("rules"), [])
        self.assertEqual(self.client.post("/api/rules", json=rule | {"enabled": False}, headers=self.auth).status_code, 200)
        self.mailer.configured = True
        self.assertEqual(self.client.post("/api/rules", json=rule, headers=self.auth).status_code, 200)
        self.assertEqual(self.client.post("/api/rules", json=rule | {"name": "Duplicate"}, headers=self.auth).status_code, 409)

    def test_administrator_status_change_is_blocked(self):
        response = self.client.post("/api/users/99/status", json={"status": "disabled"}, headers=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.upstream.writes, [])

    def test_unknown_user_role_cannot_be_disabled(self):
        self.upstream.users[0].pop("role")
        response = self.client.post("/api/users/7/status", json={"status": "disabled"}, headers=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.upstream.writes, [])

    def test_negative_ids_are_validation_errors(self):
        for path, body in (("/api/accounts/-1/schedulable", {"schedulable": False}), ("/api/users/-1/status", {"status": "disabled"}), ("/api/users/-1/email", {"subject": "Hello", "body": "Message"})):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, json=body, headers=self.auth).status_code, 422)
        self.assertEqual(self.upstream.writes, [])

    def test_manual_email_does_not_change_user_status_or_expose_mail_errors(self):
        response = self.client.post("/api/users/7/email", json={"subject": "Hello", "body": "Your account"}, headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.mailer.sent, [("alice@example.test", "Hello", "Your account")])
        self.assertEqual(self.upstream.writes, [])
        self.mailer.error = RuntimeError("sensitive-smtp-password")
        response = self.client.post("/api/users/7/email", json={"subject": "Hello", "body": "Your account"}, headers=self.auth)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("sensitive-smtp-password", response.text)
        self.assertNotIn("sensitive-smtp-password", str(self.store.audits()))

    def test_portal_token_does_not_authorize_admin_endpoints(self):
        portal = self.portal()
        self.assertEqual(self.client.get("/api/status", headers=portal).status_code, 401)
        self.assertEqual(self.client.get("/api/portal/modules", headers=self.auth).status_code, 401)
        self.assertEqual(self.client.get("/api/portal/modules", headers=portal).status_code, 200)
        self.assertTrue(all(token == "user-jwt" for token in self.upstream.profile_tokens))
        self.assertNotIn("user-jwt", str(self.store.audits()))

    def test_disabled_user_and_bound_upstream_sessions_rejected(self):
        self.upstream.users[0]["status"] = "disabled"
        self.assertEqual(self.client.post("/api/portal/session", json={"token": "user-jwt"}).status_code, 403)
        self.upstream.settings_data["session_binding_enabled"] = True
        count = len(self.upstream.profile_tokens)
        self.assertEqual(self.client.post("/api/portal/session", json={"token": "other-user-jwt"}).status_code, 409)
        self.assertEqual(len(self.upstream.profile_tokens), count)

    def test_malformed_profile_cannot_create_a_portal_session(self):
        for profile in ({"username": "unconfirmed"}, {"id": 7, "role": "user"}, {"id": -1, "role": "user", "status": "active"}, None):
            with self.subTest(profile=profile):
                self.upstream.profile = AsyncMock(return_value=profile)
                response = self.client.post("/api/portal/session", json={"token": "user-jwt"})
                self.assertEqual(response.status_code, 502)
                self.assertNotIn("session", response.json())

    def test_user_models_use_user_bearer_instead_of_admin_analytics(self):
        self.enable_modules(pricing=True)
        portal = self.portal()
        response = self.client.get("/api/portal/models?start_date=2026-11-01&end_date=2026-11-17", headers=portal)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scope"], "user")
        self.assertEqual(self.upstream.model_tokens, ["user-jwt"])
        self.assertEqual(self.client.get("/api/analytics/models", headers=self.auth).status_code, 200)
        self.assertEqual(self.upstream.model_tokens, ["user-jwt", None])

    def test_user_ranking_is_anonymous_projection_and_marks_self(self):
        self.enable_modules(ranking=True)
        response = self.client.get("/api/portal/ranking", headers=self.portal())
        self.assertEqual(response.status_code, 200)
        ranking = response.json()["ranking"]
        self.assertEqual([row["is_me"] for row in ranking], [False, True])
        self.assertEqual([row["username"] for row in ranking], ["用户 #1", "用户 #2"])
        for forbidden in ("Alice", "Bob", "email", "user_id", "private_future_field", "total_cost", "admin_future_field"):
            self.assertNotIn(forbidden, response.text)
        self.assertEqual(set(response.json()), {"ranking"})

    def test_admin_portal_ranking_keeps_admin_visibility(self):
        self.enable_modules(ranking=True)
        response = self.client.get("/api/portal/ranking", headers=self.portal("admin-jwt"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ranking"][0]["email"], "bob@example.test")

    def test_disabled_module_denies_portal_data_and_page(self):
        portal = self.portal()
        self.assertEqual(self.client.get("/api/portal/ranking", headers=portal).status_code, 404)
        self.assertEqual(self.client.get("/features/ranking").status_code, 404)
        self.assertEqual(self.client.get("/features/unknown").status_code, 404)

    def test_menu_sync_preserves_unrelated_entries_and_commits_after_verification(self):
        original = deepcopy(self.upstream.settings_data["custom_menu_items"][0])
        response = self.client.put("/api/modules", json={"pricing": True}, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        menus = self.upstream.settings_data["custom_menu_items"]
        self.assertEqual(menus[0], original)
        self.assertEqual(menus[1]["id"], "sub2api-helper-pricing")
        self.assertEqual(menus[1]["url"], "https://helper.example.test/features/pricing")
        self.assertEqual(self.store.get_setting("modules"), {"benchmark": False, "pricing": True, "ranking": False})
        response = self.client.put("/api/modules", json={}, headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.upstream.settings_data["custom_menu_items"], [original])

    def test_unconfirmed_or_failed_menu_write_never_changes_local_visibility(self):
        self.upstream.menu_write_applies = False
        response = self.client.put("/api/modules", json={"ranking": True}, headers=self.auth)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.store.get_setting("modules", {}), {})
        self.assertEqual(self.client.get("/features/ranking").status_code, 404)
        self.upstream.menu_error = UpstreamError("Permission denied", 403)
        response = self.client.put("/api/modules", json={"ranking": True}, headers=self.auth)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.store.get_setting("modules", {}), {})

    def test_malformed_upstream_menu_is_handled_as_incompatible(self):
        self.upstream.settings_data["custom_menu_items"] = [None]
        response = self.client.put("/api/modules", json={"ranking": True}, headers=self.auth)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.upstream.writes, [])

    def test_date_validation_prevents_unbounded_or_inverted_requests(self):
        for query in ("start_date=2026-11-17&end_date=2026-01-01", "start_date=2020-01-01&end_date=2026-11-17", "start_date=tomorrow"):
            with self.subTest(query=query):
                response = self.client.get("/api/analytics/models?" + query, headers=self.auth)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.upstream.model_tokens, [])


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
            update = asyncio.create_task(client.put("/api/schedules/" + str(saved["id"]), headers={"Authorization": "Bearer " + config.admin_token}, json=schedule | {"enabled": False}))
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
