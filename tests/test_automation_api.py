"""Automation HTTP contracts with isolated orchestration, no real upstream writes."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import httpx

from helper.app import create_app
from helper.config import Config
from helper.store import Store
from test_app import FakeMailer, FakeUpstream


class AutomationUpstream(FakeUpstream):
    def __init__(self):
        super().__init__()
        self.groups = [
            {"id": 3, "name": "Subscription", "status": "active", "subscription_type": "subscription", "rate_multiplier": 1,
             "daily_limit_usd": 20, "weekly_limit_usd": 100, "monthly_limit_usd": 200,
             "private_billing_secret": "never-return", "accounts": [{"secret": "never-return"}]},
            {"id": 4, "name": "Second subscription", "status": "active", "subscription_type": "subscription", "rate_multiplier": 1},
        ]

    async def list_groups(self):
        return deepcopy(self.groups)

    async def get_group(self, group_id):
        for group in self.groups:
            if group["id"] == group_id:
                return deepcopy(group)
        return {"id": group_id}


def orchestration():
    return SimpleNamespace(
        preview=AsyncMock(return_value={"actions": [], "skipped": []}),
        run=AsyncMock(return_value={"actions": [], "executed": 1, "errors": []}),
        deactivate=AsyncMock(return_value={"actions": [], "executed": 0, "errors": []}),
    )


def subscription(**changes):
    return {"name": "Near-expiry subsidy", "type": "subscription_multiplier", "enabled": False,
            "group_id": 3, "expires_within_days": 2, "remaining_fraction_gt": .5,
            "quota_period": "month", "multiplier": .5, "scope": "user_group", "restore_on_exit": True} | changes


def rewards(**changes):
    return {"name": "Monthly top-three", "type": "monthly_rewards", "enabled": False,
            "timezone": "Asia/Shanghai", "mode": "automatic",
            "rewards": [{"rank": 1, "amount": "30.25"}, {"rank": 2, "amount": "20"}, {"rank": 3, "amount": "10"}]} | changes


class AutomationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(sub2api_url="https://sub2api.example.test", sub2api_admin_key="upstream-secret",
                             admin_token="helper-automation-token", db_path=self.temp.name + "/state.db")
        self.store = Store(self.config.db_path)
        self.upstream = AutomationUpstream()
        self.app = create_app(self.config, upstream=self.upstream, store=self.store, mailer=FakeMailer(), background=False)
        self.worker = orchestration()
        self.app.state.engine.automation = self.worker
        self.auth = {"Authorization": "Bearer " + self.config.admin_token}
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def create(self, value=None):
        response = self.client.post("/api/automations", headers=self.auth, json=value or subscription())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def update(self, item_id, value):
        return self.client.put(f"/api/automations/{item_id}", headers=self.auth, json=value)

    def test_automation_and_groups_routes_require_management_auth(self):
        saved = self.create()
        for method, path, value in [
            ("GET", "/api/groups", None), ("GET", "/api/automations", None),
            ("POST", "/api/automations", subscription()),
            ("PUT", f"/api/automations/{saved['id']}", subscription()),
            ("DELETE", f"/api/automations/{saved['id']}", None),
            ("POST", f"/api/automations/{saved['id']}/preview", None),
            ("POST", f"/api/automations/{saved['id']}/run", None),
        ]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, path, json=value).status_code, 401)
                self.assertEqual(self.client.request(method, path, headers={"Authorization": "Bearer upstream-secret"}, json=value).status_code, 401)
        self.assertEqual(len(self.store.list("automations")), 1)
        self.worker.preview.assert_not_awaited()
        self.worker.run.assert_not_awaited()
        self.worker.deactivate.assert_not_awaited()

    def test_groups_response_exposes_only_configuration_fields(self):
        response = self.client.get("/api/groups", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        group = response.json()[0]
        self.assertEqual(set(group), {"id", "name", "status", "subscription_type", "rate_multiplier", "daily_limit_usd", "weekly_limit_usd", "monthly_limit_usd"})
        self.assertNotIn("never-return", response.text)

    def test_subscription_requires_an_active_subscription_group(self):
        for change in ({"group_id": None}, {"group_id": 0}, {"group_id": -1}, {"group_id": 999}):
            with self.subTest(change=change):
                response = self.client.post("/api/automations", headers=self.auth, json=subscription(**change))
                self.assertEqual(response.status_code, 422, response.text)
        for group_change in ({"status": "disabled"}, {"subscription_type": "standard"}, {"status": None}):
            with self.subTest(group_change=group_change):
                original = deepcopy(self.upstream.groups[0])
                self.upstream.groups[0].update(group_change)
                response = self.client.post("/api/automations", headers=self.auth, json=subscription())
                self.assertEqual(response.status_code, 422, response.text)
                self.upstream.groups[0] = original
        self.assertEqual(self.store.list("automations"), [])

    def test_subscription_invalid_conditions_never_create_rules(self):
        invalid = [
            {"multiplier": 0}, {"multiplier": -.1}, {"multiplier": 101}, {"multiplier": "NaN"},
            {"multiplier": "Infinity"}, {"expires_within_days": 0}, {"expires_within_days": 366},
            {"remaining_fraction_gt": -.1}, {"remaining_fraction_gt": 1.1},
            {"remaining_fraction_gt": "NaN"}, {"quota_period": "wallet"},
            {"scope": "all_users"}, {"timezone": "Invalid/Time_Zone"},
        ]
        for change in invalid:
            with self.subTest(change=change):
                response = self.client.post("/api/automations", headers=self.auth, json=subscription(**change))
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.store.list("automations"), [])

    def test_reward_ranks_and_money_are_validated_before_persisting(self):
        invalid = [
            [{"rank": 1, "amount": 10}, {"rank": 1, "amount": 5}, {"rank": 3, "amount": 1}],
            [{"rank": 0, "amount": 10}, {"rank": 2, "amount": 5}, {"rank": 3, "amount": 1}],
            [{"rank": 1, "amount": -1}, {"rank": 2, "amount": 5}, {"rank": 3, "amount": 1}],
            [{"rank": 1, "amount": "0.0000001"}, {"rank": 2, "amount": 5}, {"rank": 3, "amount": 1}],
            [{"rank": 1, "amount": "NaN"}, {"rank": 2, "amount": 5}, {"rank": 3, "amount": 1}],
            [{"rank": 1, "amount": "1000001"}, {"rank": 2, "amount": 5}, {"rank": 3, "amount": 1}],
            [{"rank": 1, "amount": 10}, {"rank": 2, "amount": 5}],
        ]
        for value in invalid:
            with self.subTest(value=value):
                response = self.client.post("/api/automations", headers=self.auth, json=rewards(rewards=value))
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.store.list("automations"), [])

    def test_zero_rewards_may_be_draft_but_cannot_be_enabled(self):
        zero = [{"rank": n, "amount": 0} for n in (1, 2, 3)]
        saved = self.create(rewards(rewards=zero))
        response = self.update(saved["id"], rewards(rewards=zero, enabled=True))
        self.assertEqual(response.status_code, 422)
        self.assertFalse(self.store.get("automations", saved["id"])["enabled"])

    def test_enabled_multiplier_rule_conflicts_per_group_in_either_scope(self):
        self.create(subscription(enabled=True))
        for scope in ("user_group", "global_group"):
            with self.subTest(scope=scope):
                response = self.client.post("/api/automations", headers=self.auth, json=subscription(name="conflict", enabled=True, scope=scope))
                self.assertEqual(response.status_code, 409, response.text)
        self.create(subscription(name="other group", enabled=True, group_id=4))
        draft = self.create(subscription(name="draft"))
        response = self.update(draft["id"], subscription(name="draft", enabled=True))
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.store.get("automations", draft["id"])["enabled"])

    def test_only_one_enabled_monthly_settlement_plan_is_allowed(self):
        self.create(rewards(enabled=True))
        for mode in ("automatic", "approval"):
            with self.subTest(mode=mode):
                response = self.client.post("/api/automations", headers=self.auth, json=rewards(enabled=True, mode=mode))
                self.assertEqual(response.status_code, 409)
        self.create(rewards(name="next draft"))

    def test_activation_timestamp_is_renewed_only_on_enable_transition(self):
        first = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        later = datetime(2026, 12, 3, 12, tzinfo=timezone.utc)
        with patch("helper.app.datetime") as clock:
            clock.now.return_value = first
            saved = self.create(subscription(enabled=True))
        enabled_at = saved["enabled_at"]
        with patch("helper.app.datetime") as clock:
            clock.now.return_value = later
            renamed = self.update(saved["id"], subscription(name="renamed", enabled=True))
            self.assertEqual(renamed.status_code, 200, renamed.text)
            self.assertEqual(renamed.json()["enabled_at"], enabled_at)
            disabled = self.update(saved["id"], subscription(enabled=False))
            self.assertEqual(disabled.status_code, 200)
            enabled = self.update(saved["id"], subscription(enabled=True))
            self.assertEqual(enabled.status_code, 200, enabled.text)
            self.assertEqual(enabled.json()["enabled_at"], later.isoformat())
            self.assertNotEqual(enabled.json()["enabled_at"], enabled_at)

    def test_material_monthly_edits_reset_activation_after_settlement_boundary(self):
        """A pre-November draft cannot acquire October backfill rights by editing it in November."""
        original_activation = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        edited_at = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
        material_edits = [
            {"mode": "approval"},
            {"timezone": "UTC"},
            {"rewards": [{"rank": 1, "amount": "100"}, {"rank": 2, "amount": "20"}, {"rank": 3, "amount": "10"}]},
        ]
        for change in material_edits:
            with self.subTest(change=change):
                with patch("helper.app.datetime") as clock:
                    clock.now.return_value = original_activation
                    saved = self.create(rewards(enabled=True))
                self.assertEqual(saved["enabled_at"], original_activation.isoformat())
                with patch("helper.app.datetime") as clock:
                    clock.now.return_value = edited_at
                    response = self.update(saved["id"], rewards(enabled=True, **change))
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["enabled"])
                self.assertEqual(response.json()["enabled_at"], edited_at.isoformat())
                self.assertEqual(self.store.get("automations", saved["id"])["enabled_at"], edited_at.isoformat())
                deleted = self.client.delete(f"/api/automations/{saved['id']}", headers=self.auth)
                self.assertEqual(deleted.status_code, 200, deleted.text)
        self.worker.run.assert_not_awaited()

    def test_name_only_monthly_edit_preserves_existing_activation(self):
        original_activation = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        renamed_at = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
        with patch("helper.app.datetime") as clock:
            clock.now.return_value = original_activation
            saved = self.create(rewards(enabled=True))
        with patch("helper.app.datetime") as clock:
            clock.now.return_value = renamed_at
            response = self.update(saved["id"], rewards(enabled=True, name="Renamed campaign"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["name"], "Renamed campaign")
        self.assertTrue(response.json()["enabled"])
        self.assertEqual(response.json()["enabled_at"], original_activation.isoformat())
        self.worker.deactivate.assert_not_awaited()
        self.worker.run.assert_not_awaited()

    def test_rule_type_is_immutable(self):
        saved = self.create(subscription())
        response = self.update(saved["id"], rewards())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.store.get("automations", saved["id"])["type"], "subscription_multiplier")
        self.worker.deactivate.assert_not_awaited()

    def test_preview_works_for_disabled_rules_without_writes_and_run_is_blocked(self):
        saved = self.create()
        before = deepcopy(self.store.get("automations", saved["id"]))
        self.worker.preview.return_value = {"type": "subscription_multiplier", "actions": [{"user_id": 7, "multiplier": .5}], "skipped": []}
        response = self.client.post(f"/api/automations/{saved['id']}/preview", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["actions"][0]["user_id"], 7)
        self.worker.preview.assert_awaited_once_with(before)
        self.assertEqual(self.store.get("automations", saved["id"]), before)
        response = self.client.post(f"/api/automations/{saved['id']}/run", headers=self.auth)
        self.assertEqual(response.status_code, 409)
        self.worker.run.assert_not_awaited()
        self.assertEqual(self.upstream.writes, [])

    def test_enabled_manual_run_is_explicit_and_audited(self):
        saved = self.create(rewards(enabled=True, mode="approval"))
        response = self.client.post(f"/api/automations/{saved['id']}/run", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.worker.run.assert_awaited_once_with(self.store.get("automations", saved["id"]), manual=True)
        events = [item for item in self.store.audits() if item["action"] == "automation.manual_run"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["target"], str(saved["id"]))

    def test_delete_restores_multiplier_before_removing_rule(self):
        saved = self.create(subscription(enabled=True))

        async def check_still_exists(rule):
            self.assertIsNotNone(self.store.get("automations", saved["id"]))
            return {"actions": [{"action": "restore_user_group_rate"}], "errors": []}

        self.worker.deactivate.side_effect = check_still_exists
        response = self.client.delete(f"/api/automations/{saved['id']}", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.worker.deactivate.assert_awaited_once()
        self.assertIsNone(self.store.get("automations", saved["id"]))

    def test_delete_and_edit_refuse_to_discard_unrestored_multiplier(self):
        saved = self.create(subscription(enabled=True))
        self.worker.deactivate.return_value = {"actions": [], "errors": ["write not confirmed"]}
        before = deepcopy(self.store.get("automations", saved["id"]))
        for operation in ("delete", "edit", "disable"):
            with self.subTest(operation=operation):
                if operation == "delete":
                    response = self.client.delete(f"/api/automations/{saved['id']}", headers=self.auth)
                else:
                    response = self.update(saved["id"], subscription(enabled=operation != "disable", multiplier=.25 if operation == "edit" else .5))
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(self.store.get("automations", saved["id"]), before)

    def test_changing_rate_cleans_up_old_value_before_committing_new_value(self):
        saved = self.create(subscription(enabled=True))

        async def check_original(rule):
            self.assertEqual(rule["multiplier"], .5)
            self.assertEqual(self.store.get("automations", saved["id"])["multiplier"], .5)
            return {"actions": [], "errors": []}

        self.worker.deactivate.side_effect = check_original
        response = self.update(saved["id"], subscription(enabled=True, multiplier=.25))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store.get("automations", saved["id"])["multiplier"], .25)
        self.worker.deactivate.assert_awaited_once()

    def test_monthly_rule_deletion_keeps_durable_award_history(self):
        saved = self.create(rewards())
        self.store.ledger_set("monthly-award:test:2026-10:user:7", {"status": "done", "amount": "30"})
        response = self.client.delete(f"/api/automations/{saved['id']}", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.ledger_get("monthly-award:test:2026-10:user:7")["status"], "done")
        self.worker.deactivate.assert_not_awaited()

    def settlement(self, month="2026-10", **changes):
        snapshot = {"month": month, "start_date": month + "-01", "end_date": month + "-28",
                    "timezone": "Asia/Shanghai", "rule_id": 99,
                    "internal_ledger_key": "do-not-return", "internal_snapshot_version": "do-not-return",
                    "actions": [{"user_id": 7, "rank": 1, "amount": "30.25", "actual_cost": "100.50",
                                 "month": month, "status": "done", "completed_at": "2026-11-01T00:00:00+08:00",
                                 "idempotency_key": "do-not-return", "private_future_field": "do-not-return"}]} | changes
        instance = hashlib.sha256(self.config.sub2api_url.rstrip("/").encode()).hexdigest()[:24]
        self.store.ledger_set(f"automation:monthly_rewards:{instance}:{month}", snapshot)
        return snapshot

    def test_settlement_history_requires_admin_auth_and_rejects_ordinary_portal_session(self):
        self.settlement()
        session = self.client.post("/api/portal/session", json={"token": "user-jwt"})
        self.assertEqual(session.status_code, 200, session.text)
        for headers in ({}, {"Authorization": "Bearer upstream-secret"},
                        {"X-Portal-Session": session.json()["session"]}):
            with self.subTest(headers=list(headers)):
                response = self.client.get("/api/settlements", headers=headers)
                self.assertEqual(response.status_code, 401, response.text)
                self.assertNotIn("30.25", response.text)
        self.assertEqual(self.client.get("/api/settlements", headers=self.auth).status_code, 200)

    def test_settlement_history_is_sorted_instance_scoped_and_filters_internal_fields(self):
        self.settlement("2026-09")
        self.settlement("2026-11")
        self.settlement("2026-10")
        self.store.ledger_set("automation:monthly_rewards:another-instance:2026-12", {"month": "2026-12", "actions": []})
        self.store.ledger_set("automation:rate_ownership:unrelated", {"month": "2027-01", "actions": []})
        self.store.ledger_set("rule:1:user:7:usage:abc", {"status": "done"})
        response = self.client.get("/api/settlements", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()
        self.assertEqual([row["month"] for row in rows], ["2026-11", "2026-10", "2026-09"])
        self.assertEqual(set(rows[0]), {"month", "start_date", "end_date", "timezone", "rule_id", "actions"})
        self.assertEqual(set(rows[0]["actions"][0]), {"user_id", "rank", "amount", "actual_cost", "month", "status", "completed_at"})
        self.assertNotIn("do-not-return", response.text)
        self.assertNotIn("idempotency_key", response.text)
        self.assertNotIn("ledger_key", response.text)
        self.assertNotIn("internal_snapshot_version", response.text)

    def test_settlement_history_keeps_uncertain_payout_visible_after_rule_deletion(self):
        saved = self.create(rewards())
        pending = {"user_id": 7, "rank": 1, "amount": "30.25", "actual_cost": "100.50", "month": "2026-10",
                   "status": "needs_review", "reason": "Outcome uncertain; reconcile before any manual action",
                   "error": "UpstreamError", "attempted_at": "2026-11-01T00:00:00+08:00", "idempotency_key": "do-not-return"}
        self.settlement(rule_id=saved["id"], actions=[pending])
        response = self.client.delete(f"/api/automations/{saved['id']}", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.store.get("automations", saved["id"]))
        response = self.client.get("/api/settlements", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()[0]["rule_id"], saved["id"])
        action = response.json()[0]["actions"][0]
        self.assertEqual(action["status"], "needs_review")
        self.assertEqual(action["attempted_at"], pending["attempted_at"])
        self.assertEqual(action["reason"], pending["reason"])
        self.assertNotIn("do-not-return", response.text)
        self.worker.run.assert_not_awaited()

    def test_empty_settlement_history_ignores_other_ledger_kinds(self):
        self.store.ledger_set("automation:rate_ownership:unrelated", {"status": "active"})
        self.store.ledger_set("automation:monthly_rewards:another-instance:2026-10", {"month": "2026-10", "actions": []})
        response = self.client.get("/api/settlements", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])

    def test_missing_automation_does_not_call_the_worker(self):
        for method, path, value in [
            ("PUT", "/api/automations/999", subscription()), ("DELETE", "/api/automations/999", None),
            ("POST", "/api/automations/999/preview", None), ("POST", "/api/automations/999/run", None),
        ]:
            with self.subTest(path=path):
                response = self.client.request(method, path, headers=self.auth, json=value)
                self.assertEqual(response.status_code, 404, response.text)
        self.worker.run.assert_not_awaited()
        self.worker.preview.assert_not_awaited()
        self.worker.deactivate.assert_not_awaited()


class AutomationApiSerializationTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_and_execution_wait_for_scheduler_mutation_lock(self):
        store = Store(":memory:")
        config = Config(sub2api_url="https://sub2api.example.test", sub2api_admin_key="upstream-secret", admin_token="helper-token", db_path=":memory:")
        app = create_app(config, upstream=AutomationUpstream(), store=store, mailer=FakeMailer(), background=False)
        worker = orchestration()
        app.state.engine.automation = worker
        saved = store.create("automations", subscription(enabled=True))
        auth = {"Authorization": "Bearer " + config.admin_token}
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://helper.test") as client:
                for suffix, method in (("preview", worker.preview), ("run", worker.run)):
                    with self.subTest(suffix=suffix):
                        await app.state.engine._tick_lock.acquire()
                        request = asyncio.create_task(client.post(f"/api/automations/{saved['id']}/{suffix}", headers=auth))
                        try:
                            await asyncio.sleep(.02)
                            self.assertFalse(request.done(), "Financial preview/execution cannot race an in-flight scheduler change")
                            method.assert_not_awaited()
                        finally:
                            app.state.engine._tick_lock.release()
                        response = await asyncio.wait_for(request, 1)
                        self.assertEqual(response.status_code, 200, response.text)
                        method.assert_awaited_once()
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
