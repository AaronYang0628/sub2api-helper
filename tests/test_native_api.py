"""Native row actions use fresh IDs and never adopt accounts on read."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from helper.engine import Engine
from helper.native_api import install_native_api
from helper.store import Store


class Upstream:
    def __init__(self):
        self.accounts = [{"id": 1, "name": "Night", "schedulable": False},
                         {"id": 2, "name": "Other", "schedulable": True}]
        self.users = {7: {"id": 7, "username": "Alice", "email": "current@example.test",
                          "role": "user", "status": "active", "password_hash": "never expose"}}
        self.writes = []
        self.applies = True
        self.error = None
        self.user_reads = 0

    async def list_accounts(self):
        return deepcopy(self.accounts)

    async def set_schedulable(self, account_id, desired):
        self.writes.append((account_id, desired))
        if self.error:
            raise self.error
        if self.applies:
            next(row for row in self.accounts if row["id"] == account_id)["schedulable"] = desired

    async def get_user(self, user_id):
        self.user_reads += 1
        return deepcopy(self.users.get(user_id, {}))


class Mailer:
    configured = True

    def __init__(self):
        self.sent = []
        self.error = None

    async def send(self, recipient, subject, body):
        if self.error:
            raise self.error
        self.sent.append((recipient, subject, body))


class NativeApiTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.upstream = Upstream()
        self.mailer = Mailer()
        self.engine = Engine(self.store, self.upstream, self.mailer)
        self.config = SimpleNamespace(timezone="Asia/Shanghai")
        self.limits = []
        app = FastAPI()

        async def native_admin(authorization: str = Header(default="")):
            if authorization != "Bearer verified-admin":
                raise HTTPException(401, "sign in")
            return {"id": 99, "role": "admin", "status": "active"}

        def limit(request, category, count):
            self.limits.append((category, count))

        install_native_api(app, self.store, self.upstream, self.mailer, self.engine,
                           lambda: self.config, native_admin, limit)
        self.client = TestClient(app)
        self.client.headers.update({"Authorization": "Bearer verified-admin"})
        self.path = "/_helper/api/accounts/1/schedule"

    def tearDown(self):
        self.client.close()
        self.store.close()

    def plan(self, **changes):
        value = {"name": "Native", "account_ids": [1], "start": "23:00", "end": "09:00",
                 "weekdays": list(range(7)), "timezone": "Asia/Shanghai", "enabled": True,
                 "owner": "native-account", "native_account_id": 1}
        return self.store.create("schedules", value | changes)

    def window(self, **changes):
        value = {"mode": "window", "start": "23:00", "end": "09:00", "timezone": "Asia/Shanghai",
                 "weekdays": [4]}
        return value | changes

    def test_default_read_is_unmanaged_and_preserves_manual_disabled_state(self):
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "all_day")
        self.assertFalse(response.json()["managed"])
        self.assertFalse(response.json()["schedulable"])
        self.assertFalse(response.json()["read_only"])
        self.assertEqual(self.store.list("schedules"), [])
        self.assertEqual(self.upstream.writes, [])
        self.assertFalse(self.upstream.accounts[0]["schedulable"])

    def test_authentication_dependency_applies_to_every_action(self):
        self.client.headers.clear()
        calls = [("get", self.path, None), ("put", self.path, {"mode": "all_day"}),
                 ("get", "/_helper/api/users/7", None),
                 ("post", "/_helper/api/users/7/email", {"subject": "Hi", "body": "Hi"}),
                 ("get", "/_helper/api/status", None)]
        for method, path, body in calls:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, path, json=body).status_code, 401)
        self.assertEqual(self.upstream.writes, [])
        self.assertEqual(self.mailer.sent, [])

    def test_status_has_capabilities_without_credentials(self):
        self.assertEqual(self.client.get("/_helper/api/status").json(),
                         {"timezone": "Asia/Shanghai", "mail_configured": True})

    def test_friday_window_still_applies_on_saturday_in_shanghai(self):
        # Saturday 08:00 CST belongs to Friday's 23:00 opening day.
        with patch("helper.native_api.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 20, 23, 59, tzinfo=timezone.utc)
            response = self.client.put(self.path, json=self.window())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["managed"])
        self.assertTrue(response.json()["schedulable"])
        self.assertEqual(self.upstream.writes, [(1, True)])
        plan = self.store.list("schedules")[0]
        self.assertEqual(plan["owner"], "native-account")
        self.assertEqual(plan["native_account_id"], 1)
        self.assertEqual(plan["account_ids"], [1])
        self.assertEqual(plan["weekdays"], [4])
        self.assertIn("actor=99", self.store.audits()[0]["detail"])

    def test_window_closes_at_exclusive_end_without_changing_other_account(self):
        self.upstream.accounts[0]["schedulable"] = True
        with patch("helper.native_api.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 21, 1, 0, tzinfo=timezone.utc)
            response = self.client.put(self.path, json=self.window())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["schedulable"])
        self.assertEqual(self.upstream.writes, [(1, False)])
        self.assertTrue(self.upstream.accounts[1]["schedulable"])

    def test_update_reuses_native_plan_and_preserves_disabled_foreign_plan(self):
        native = self.plan()
        foreign = self.plan(owner="batch", native_account_id=None, enabled=False)
        response = self.client.put(self.path, json=self.window(start="22:00"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.store.list("schedules")), 2)
        self.assertEqual(self.store.get("schedules", native["id"])["start"], "22:00")
        self.assertEqual(self.store.get("schedules", native["id"])["name"], "Native")
        self.assertEqual(self.store.get("schedules", foreign["id"]), foreign)

    def test_custom_name_is_returned_updated_and_preserved_when_omitted(self):
        self.assertEqual(self.client.get(self.path).json()["name"], "账号 #1 定时调度")
        created = self.client.put(self.path, json=self.window())
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["name"], "账号 #1 定时调度")
        named = self.client.put(self.path, json=self.window(name="夜间主力账号"))
        self.assertEqual(named.status_code, 200)
        self.assertEqual(named.json()["name"], "夜间主力账号")
        self.assertEqual(self.client.get(self.path).json()["name"], "夜间主力账号")
        preserved = self.client.put(self.path, json=self.window(start="22:30", name="   "))
        self.assertEqual(preserved.status_code, 200)
        self.assertEqual(preserved.json()["name"], "夜间主力账号")
        self.assertEqual(self.store.list("schedules")[0]["name"], "夜间主力账号")

    def test_foreign_enabled_rule_is_read_only_and_cannot_be_overridden(self):
        foreign = self.plan(owner="batch", native_account_id=None, account_ids=[1, 2])
        response = self.client.get(self.path)
        self.assertTrue(response.json()["read_only"])
        self.assertIsInstance(response.json()["conflict"], str)
        for value in ({"mode": "all_day"}, self.window()):
            self.assertEqual(self.client.put(self.path, json=value).status_code, 409)
        self.assertEqual(self.upstream.writes, [])
        self.assertEqual(self.store.list("schedules"), [foreign])

    def test_corrupt_or_duplicate_native_ownership_fails_closed(self):
        for changes in ({"account_ids": [1, 2]}, {"account_ids": [2]}, {"native_account_id": 2}):
            with self.subTest(changes=changes):
                item = self.plan(**changes)
                self.assertTrue(self.client.get(self.path).json()["read_only"])
                self.assertEqual(self.client.put(self.path, json={"mode": "all_day"}).status_code, 409)
                self.store.delete("schedules", item["id"])
        self.plan()
        self.plan()
        self.assertEqual(self.client.put(self.path, json=self.window()).status_code, 409)
        self.assertEqual(self.upstream.writes, [])

    def test_explicit_all_day_enables_and_removes_only_own_plan(self):
        self.plan()
        other = self.plan(native_account_id=2, account_ids=[2])
        disabled = self.plan(owner="batch", native_account_id=None, enabled=False)
        response = self.client.put(self.path, json={"mode": "all_day"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["managed"])
        self.assertTrue(response.json()["schedulable"])
        self.assertEqual(self.upstream.writes, [(1, True)])
        self.assertEqual(self.store.list("schedules"), [other, disabled])

    def test_failed_all_day_readback_retains_previous_ownership(self):
        old = self.plan()
        self.upstream.applies = False
        response = self.client.put(self.path, json={"mode": "all_day"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.store.list("schedules"), [old])

    def test_all_day_without_native_ownership_preserves_disabled_batch_schedule(self):
        batch = self.plan(owner="batch", native_account_id=None, enabled=False,
                          account_ids=[1, 2], name="Paused batch shared with another account")
        before = self.client.get(self.path).json()
        self.assertFalse(before["managed"])
        self.assertFalse(before["read_only"])
        response = self.client.put(self.path, json={"mode": "all_day"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["schedulable"])
        self.assertFalse(response.json()["managed"])
        self.assertEqual(self.store.list("schedules"), [batch])
        self.assertTrue(self.upstream.accounts[1]["schedulable"])
        self.assertEqual(self.upstream.writes, [(1, True)])

    def test_failed_window_readback_retains_plan_for_restart_reconciliation(self):
        self.upstream.applies = False
        with patch("helper.native_api.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc)
            response = self.client.put(self.path, json=self.window())
        self.assertEqual(response.status_code, 502)
        self.assertIn("已保存", response.json()["detail"])
        plans = self.store.list("schedules")
        self.assertEqual(len(plans), 1)
        self.assertTrue(plans[0]["enabled"])
        self.assertEqual(plans[0]["last_error"], "ValueError")
        self.assertTrue(self.client.get(self.path).json()["managed"])

    def test_transport_failure_returns_no_sensitive_detail(self):
        self.upstream.error = RuntimeError("secret administrative credential")
        response = self.client.put(self.path, json={"mode": "all_day"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret", response.text)

    def test_invalid_windows_rejected_before_store_or_upstream_mutation(self):
        for changes in ({"start": "09:00", "end": "09:00"}, {"start": "25:00"},
                        {"timezone": "Not/AZone"}, {"weekdays": [0, 0]}, {"weekdays": []},
                        {"weekdays": [7]}, {"mode": "unknown"}, {"account_ids": [2]}):
            with self.subTest(changes=changes):
                self.assertEqual(self.client.put(self.path, json=self.window(**changes)).status_code, 422)
        self.assertEqual(self.store.list("schedules"), [])
        self.assertEqual(self.upstream.writes, [])

    def test_missing_or_invalid_account_state_cannot_be_claimed(self):
        self.assertEqual(self.client.put("/_helper/api/accounts/3/schedule", json=self.window()).status_code, 404)
        self.upstream.accounts[0]["schedulable"] = "false"
        self.assertEqual(self.client.put(self.path, json=self.window()).status_code, 502)
        self.assertEqual(self.store.list("schedules"), [])

    def test_user_data_filtered_and_mail_recipient_read_again_on_send(self):
        user = self.client.get("/_helper/api/users/7").json()
        self.assertNotIn("password_hash", user)
        self.upstream.users[7]["email"] = "new@example.test"
        response = self.client.post("/_helper/api/users/7/email", json={"subject": "Notice", "body": "Hello"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.mailer.sent, [("new@example.test", "Notice", "Hello")])
        self.assertEqual(self.upstream.user_reads, 2)
        self.assertEqual(self.limits, [("native.email", 10)])
        self.assertIn("actor=99", self.store.audits()[0]["detail"])

    def test_mail_rejects_supplied_recipient_and_header_injection(self):
        for body in ({"subject": "Hi", "body": "Hello", "email": "attacker@example.test"},
                     {"subject": "Hi\r\nBcc: attacker@example.test", "body": "Hello"}):
            self.assertEqual(self.client.post("/_helper/api/users/7/email", json=body).status_code, 422)
        self.assertEqual(self.mailer.sent, [])

    def test_mail_configuration_identity_and_delivery_failures_do_not_change_user(self):
        payload = {"subject": "Hi", "body": "Hello"}
        before = deepcopy(self.upstream.users)
        self.mailer.configured = False
        self.assertEqual(self.client.post("/_helper/api/users/7/email", json=payload).status_code, 409)
        self.mailer.configured = True
        self.upstream.users[7]["id"] = 8
        self.assertEqual(self.client.post("/_helper/api/users/7/email", json=payload).status_code, 502)
        self.upstream.users[7]["id"] = 7
        self.mailer.error = RuntimeError("smtp-password-secret")
        response = self.client.post("/_helper/api/users/7/email", json=payload)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("smtp-password-secret", response.text)
        self.assertEqual(self.upstream.users, before)
        self.assertEqual(self.mailer.sent, [])

    def test_schedule_get_lists_available_rules_and_apply_rule_joins_and_reconciles(self):
        rule = self.plan(owner="setup", native_account_id=None, account_ids=[2], name="夜间主力时段",
                         start="22:00", end="08:00", weekdays=[4], enabled=True)
        paused = self.plan(owner="setup", native_account_id=None, account_ids=[], name="停用规则", enabled=False)
        native_other = self.plan(native_account_id=2, account_ids=[2], name="其他账号原生")
        with patch("helper.native_api.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc)
            info = self.client.get(self.path).json()
            ids = [item["id"] for item in info["available_schedules"]]
            self.assertEqual(ids, [rule["id"], paused["id"]])
            self.assertNotIn(native_other["id"], ids)
            self.assertEqual(info["available_schedules"][0]["account_count"], 1)
            # Disabled rule cannot be applied
            self.assertEqual(self.client.put(self.path, json={"mode": "rule", "schedule_id": paused["id"]}).status_code, 409)
            # Unknown or native-account rule rejected with 404
            self.assertEqual(self.client.put(self.path, json={"mode": "rule", "schedule_id": native_other["id"]}).status_code, 404)
            # Valid rule joins account 1 and reconciles schedulable
            self.upstream.accounts[0]["schedulable"] = False
            applied = self.client.put(self.path, json={"mode": "rule", "schedule_id": rule["id"]})
            self.assertEqual(applied.status_code, 200, applied.text)
            body = applied.json()
            self.assertTrue(body["read_only"])
            self.assertEqual(body["shared_schedule"]["id"], rule["id"])
            self.assertTrue(body["schedulable"])
            self.assertEqual(self.store.get("schedules", rule["id"])["account_ids"], [2, 1])

    def test_explicit_remove_from_shared_rule_preserves_other_accounts(self):
        shared = self.plan(owner="setup", native_account_id=None, account_ids=[1, 2], name="共享时段", enabled=True)
        self.upstream.accounts[0]["schedulable"] = False
        # Wrong rule ID rejected
        self.assertEqual(
            self.client.put(self.path, json={"mode": "all_day", "remove_from_schedule_id": 9999}).status_code,
            409,
        )
        # Explicit removal removes only account 1 and enables it
        removed = self.client.put(self.path, json={"mode": "all_day", "remove_from_schedule_id": shared["id"]})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertFalse(removed.json()["read_only"])
        self.assertTrue(removed.json()["schedulable"])
        self.assertEqual(self.store.get("schedules", shared["id"])["account_ids"], [2])
        self.assertTrue(self.store.get("schedules", shared["id"])["enabled"])

    def test_removing_last_account_from_shared_rule_clears_account_ids_and_disables(self):
        solo = self.plan(owner="setup", native_account_id=None, account_ids=[1], name="单账号共享时段", enabled=True)
        self.upstream.accounts[0]["schedulable"] = False
        removed = self.client.put(self.path, json={"mode": "all_day", "remove_from_schedule_id": solo["id"]})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertFalse(removed.json()["read_only"])
        self.assertTrue(removed.json()["schedulable"])
        updated = self.store.get("schedules", solo["id"])
        self.assertEqual(updated["account_ids"], [])
        self.assertFalse(updated["enabled"])

    def test_user_get_exposes_saved_email_templates(self):
        tpl = self.store.create("email_templates", {"name": "维护预告", "subject": "你好 {username}", "body": "邮箱 {email}"})
        user = self.client.get("/_helper/api/users/7").json()
        self.assertIn("templates", user)
        custom = [t for t in user["templates"] if not t["builtin"]]
        builtin = [t for t in user["templates"] if t["builtin"]]
        self.assertEqual(len(custom), 1)
        self.assertGreaterEqual(len(builtin), 1)
        self.assertEqual(custom[0]["id"], str(tpl["id"]))
        self.assertEqual(custom[0]["name"], "维护预告")


if __name__ == "__main__":
    unittest.main()
