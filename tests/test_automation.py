import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from helper.automation import AutomationEngine, settlement_period
from helper.store import Store


NOW = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)


class FakeAutomationUpstream:
    base_url = "https://sub2api.example.test"

    def __init__(self):
        self.groups = {5: {"id": 5, "status": "active", "subscription_type": "subscription", "rate_multiplier": 1.0, "daily_limit_usd": 100, "weekly_limit_usd": 100, "monthly_limit_usd": 100}}
        self.users = {uid: {"id": uid, "status": "active", "role": "user"} for uid in (1, 2, 3, 4, 99)}
        self.users[99]["role"] = "admin"
        self.subscriptions = {10: {"id": 10, "user_id": 1, "group_id": 5, "status": "active", "starts_at": (NOW - timedelta(days=10)).isoformat(), "expires_at": (NOW + timedelta(days=1)).isoformat()}}
        window = {"limit_usd": 100, "used_usd": 20, "remaining_usd": 80, "window_start": (NOW - timedelta(hours=1)).isoformat(), "resets_at": (NOW + timedelta(hours=5)).isoformat()}
        self.progress = {10: {"id": 10, "expires_at": self.subscriptions[10]["expires_at"], "daily": deepcopy(window), "weekly": deepcopy(window), "monthly": deepcopy(window)}}
        self.rates = {(1, 5): None, (1, 8): 1.7}
        self.spending = [{"user_id": uid, "actual_cost": cost, "tokens": 100} for uid, cost in ((1, 40), (2, 30), (3, 20), (4, 10))]
        self.rate_writes = []
        self.credit_writes = []
        self.balances = {}
        self.spending_calls = []
        self.reads = {}
        self.subscription_reads = 0
        self.before_subscription_read = None
        self.before_user_read = None
        self.rate_read_error = None
        self.rate_write_error = None
        self.credit_error = None

    async def get_group(self, group_id):
        return deepcopy(self.groups[group_id])

    async def get_user(self, user_id):
        self.reads[user_id] = self.reads.get(user_id, 0) + 1
        if self.before_user_read:
            self.before_user_read(user_id, self.reads[user_id])
        return deepcopy(self.users[user_id])

    async def list_subscriptions(self, group_id):
        return [deepcopy(s) for s in self.subscriptions.values() if s["group_id"] == group_id]

    async def get_subscription(self, subscription_id):
        self.subscription_reads += 1
        if self.before_subscription_read:
            self.before_subscription_read(subscription_id, self.subscription_reads)
        return deepcopy(self.subscriptions[subscription_id])

    async def subscription_progress(self, subscription_id):
        return deepcopy(self.progress[subscription_id])

    async def user_group_rate(self, user_id, group_id):
        if self.rate_read_error:
            raise self.rate_read_error
        return self.rates.get((user_id, group_id))

    async def set_user_group_rate(self, user_id, group_id, multiplier):
        self.rate_writes.append(("user", user_id, group_id, multiplier))
        self.rates[(user_id, group_id)] = multiplier
        if self.rate_write_error:
            raise self.rate_write_error

    async def set_group_rate(self, group_id, multiplier):
        self.rate_writes.append(("group", group_id, multiplier))
        self.groups[group_id]["rate_multiplier"] = multiplier
        if self.rate_write_error:
            raise self.rate_write_error

    async def monthly_spending(self, start, end, timezone_name):
        self.spending_calls.append((start, end, timezone_name))
        return deepcopy(self.spending)

    async def add_balance(self, user_id, amount, idempotency_key):
        self.credit_writes.append((user_id, amount, idempotency_key))
        self.balances[user_id] = self.balances.get(user_id, 0) + amount
        if self.credit_error:
            raise self.credit_error


class AutomationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "helper.db")
        self.store = Store(self.path)
        self.upstream = FakeAutomationUpstream()
        self.engine = AutomationEngine(self.store, self.upstream)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def multiplier(self, **changes):
        return {"id": 1, "name": "Near expiry", "type": "subscription_multiplier", "enabled": True, "group_id": 5, "expires_within_days": 2, "remaining_fraction_gt": .5, "quota_period": "month", "multiplier": .5, "scope": "user_group", "restore_on_exit": True} | changes

    def rewards(self, **changes):
        return {"id": 2, "name": "Monthly reward", "type": "monthly_rewards", "enabled": True, "enabled_at": "2026-10-01T00:00:00Z", "timezone": "Asia/Shanghai", "mode": "automatic", "rewards": [{"rank": 1, "amount": "30.50"}, {"rank": 2, "amount": "20"}, {"rank": 3, "amount": "10"}]} | changes

    async def test_previews_never_mutate_upstream_or_persistent_state(self):
        rate_plan = await self.engine.preview(self.multiplier(), NOW)
        reward_plan = await self.engine.preview(self.rewards(), NOW)
        self.assertEqual(rate_plan["matched_users"], 1)
        self.assertEqual(len(reward_plan["actions"]), 3)
        self.assertEqual(self.store.ledger_list("automation:"), {})
        self.assertEqual(self.store.audits(), [])
        self.assertEqual(self.upstream.rate_writes, [])
        self.assertEqual(self.upstream.credit_writes, [])

    async def test_disabled_rules_never_execute_even_with_manual_flag(self):
        await self.engine.run(self.multiplier(enabled=False), NOW, manual=True)
        await self.engine.run(self.rewards(enabled=False), NOW, manual=True)
        self.assertEqual(self.upstream.rate_writes, [])
        self.assertEqual(self.upstream.credit_writes, [])
        self.assertEqual(self.store.ledger_list("automation:"), {})

    async def test_personal_rate_restores_missing_override_and_preserves_other_groups(self):
        rule = self.multiplier()
        first = await self.engine.run(rule, NOW)
        self.assertEqual(first["executed"], 1)
        self.assertEqual(self.upstream.rates[(1, 5)], .5)
        self.assertEqual(self.upstream.rates[(1, 8)], 1.7)
        await AutomationEngine(self.store, self.upstream).run(rule, NOW)
        self.assertEqual(len(self.upstream.rate_writes), 1)
        self.upstream.progress[10]["monthly"].update(used_usd=70, remaining_usd=30)
        restored = await self.engine.run(rule, NOW)
        self.assertEqual(restored["executed"], 1)
        self.assertIsNone(self.upstream.rates[(1, 5)])
        self.assertEqual(self.upstream.rates[(1, 8)], 1.7)

    async def test_existing_personal_override_is_restored_exactly(self):
        self.upstream.rates[(1, 5)] = 1.25
        await self.engine.run(self.multiplier(), NOW)
        result = await self.engine.deactivate(self.multiplier(), NOW)
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.upstream.rates[(1, 5)], 1.25)

    async def test_global_scope_is_explicit_and_restores_global_baseline(self):
        rule = self.multiplier(scope="global_group")
        await self.engine.run(rule, NOW)
        self.assertEqual(self.upstream.groups[5]["rate_multiplier"], .5)
        self.assertEqual(self.upstream.rate_writes, [("group", 5, .5)])
        await self.engine.deactivate(rule, NOW)
        self.assertEqual(self.upstream.groups[5]["rate_multiplier"], 1)
        self.assertIsNone(self.upstream.rates[(1, 5)])

    async def test_selected_day_week_month_quota_and_strict_fraction(self):
        self.upstream.progress[10]["daily"].update(used_usd=50, remaining_usd=50)
        day = await self.engine.preview(self.multiplier(quota_period="day"), NOW)
        self.assertEqual(day["actions"], [])
        self.upstream.progress[10]["weekly"].update(used_usd=49, remaining_usd=51)
        week = await self.engine.preview(self.multiplier(quota_period="week"), NOW)
        month = await self.engine.preview(self.multiplier(quota_period="month"), NOW)
        self.assertEqual(len(week["actions"]), 1)
        self.assertEqual(len(month["actions"]), 1)

    async def test_expiry_start_and_horizon_boundaries(self):
        for expiry, starts, expected in ((NOW, NOW - timedelta(days=1), 0), (NOW + timedelta(days=2), NOW - timedelta(days=1), 1), (NOW + timedelta(days=2, seconds=1), NOW - timedelta(days=1), 0), (NOW + timedelta(days=1), NOW + timedelta(seconds=1), 0)):
            with self.subTest(expiry=expiry, starts=starts):
                self.upstream.subscriptions[10].update(expires_at=expiry.isoformat(), starts_at=starts.isoformat())
                self.upstream.progress[10]["expires_at"] = expiry.isoformat()
                plan = await self.engine.preview(self.multiplier(), NOW)
                self.assertEqual(len(plan["actions"]), expected)

    async def test_missing_expired_unlimited_or_inconsistent_quotas_skip_safely(self):
        original = deepcopy(self.upstream.progress[10]["monthly"])
        cases = (None, original | {"resets_at": NOW.isoformat()}, original | {"limit_usd": 0}, original | {"remaining_usd": 95}, original | {"window_start": None}, original | {"used_usd": float("nan")})
        for quota in cases:
            with self.subTest(quota=quota):
                self.upstream.progress[10]["monthly"] = quota
                report = await self.engine.run(self.multiplier(), NOW)
                self.assertEqual(report["executed"], 0)
        self.assertEqual(self.upstream.rate_writes, [])

    async def test_unknown_quota_does_not_restore_an_existing_discount(self):
        await self.engine.run(self.multiplier(), NOW)
        self.upstream.progress[10]["monthly"] = None
        result = await self.engine.run(self.multiplier(), NOW)
        self.assertEqual(result["executed"], 0)
        self.assertEqual(self.upstream.rates[(1, 5)], .5)

    async def test_progress_expiry_and_group_cap_drift_do_not_apply(self):
        self.upstream.progress[10]["expires_at"] = (NOW + timedelta(days=5)).isoformat()
        self.assertEqual((await self.engine.run(self.multiplier(), NOW))["executed"], 0)
        self.upstream.progress[10]["expires_at"] = self.upstream.subscriptions[10]["expires_at"]
        self.upstream.groups[5]["monthly_limit_usd"] = 200
        self.assertEqual((await self.engine.run(self.multiplier(), NOW))["executed"], 0)

    async def test_admin_disabled_and_wrong_identity_profiles_are_not_targets(self):
        for profile in ({"id": 1, "role": "admin", "status": "active"}, {"id": 1, "role": "user", "status": "disabled"}, {"id": 99, "role": "user", "status": "active"}, {"id": True, "role": "user", "status": "active"}):
            with self.subTest(profile=profile):
                self.upstream.users[1] = profile
                self.assertEqual((await self.engine.run(self.multiplier(), NOW))["executed"], 0)
        self.assertEqual(self.upstream.rate_writes, [])

    async def test_subscription_changes_between_plan_and_write_prevent_apply(self):
        def renew(subscription_id, count):
            if count >= 2:
                self.upstream.subscriptions[subscription_id]["expires_at"] = (NOW + timedelta(days=30)).isoformat()
        self.upstream.before_subscription_read = renew
        result = await self.engine.run(self.multiplier(), NOW)
        self.assertEqual(result["executed"], 0)
        self.assertEqual(self.upstream.rate_writes, [])
        self.assertEqual(self.store.ledger_list("automation:"), {})

    async def test_manual_override_is_not_restored_and_does_not_block_deletion(self):
        await self.engine.run(self.multiplier(), NOW)
        self.upstream.rates[(1, 5)] = .8
        result = await self.engine.deactivate(self.multiplier(), NOW)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["executed"], 0)
        self.assertEqual(self.upstream.rates[(1, 5)], .8)
        self.assertTrue(result["skipped"])
        self.assertEqual(next(iter(self.store.ledger_list("automation:").values()))["status"], "restored")

    async def test_no_restore_policy_releases_claim_without_mutating_value(self):
        rule = self.multiplier(restore_on_exit=False)
        await self.engine.run(rule, NOW)
        result = await self.engine.deactivate(rule, NOW)
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.upstream.rates[(1, 5)], .5)
        result = await self.engine.run(self.multiplier(id=77, multiplier=.4), NOW)
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.upstream.rates[(1, 5)], .4)

    async def test_interrupted_apply_is_confirmed_without_duplicate_rate_write(self):
        self.upstream.rate_write_error = RuntimeError("response failed after successful write")
        report = await self.engine.run(self.multiplier(), NOW)
        self.assertTrue(report["errors"])
        self.assertEqual(self.upstream.rates[(1, 5)], .5)
        self.upstream.rate_write_error = None
        result = await AutomationEngine(self.store, self.upstream).run(self.multiplier(), NOW)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(self.upstream.rate_writes), 1)

    async def test_interrupted_restore_then_requalification_can_safely_reapply(self):
        await self.engine.run(self.multiplier(), NOW)
        self.upstream.rate_write_error = RuntimeError("response failed after restore")
        restored = await self.engine.deactivate(self.multiplier(), NOW)
        self.assertTrue(restored["errors"])
        self.assertIsNone(self.upstream.rates[(1, 5)])
        self.upstream.rate_write_error = None
        result = await AutomationEngine(self.store, self.upstream).run(self.multiplier(), NOW)
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.upstream.rates[(1, 5)], .5)

    async def test_unreliable_baseline_read_is_not_treated_as_missing_override(self):
        self.upstream.rate_read_error = RuntimeError("rate storage unavailable")
        result = await self.engine.run(self.multiplier(), NOW)
        self.assertTrue(result["errors"])
        self.assertEqual(self.upstream.rate_writes, [])
        self.assertEqual(self.store.ledger_list("automation:"), {})

    async def test_one_target_read_failure_does_not_block_other_eligible_users(self):
        self.upstream.subscriptions[20] = self.upstream.subscriptions[10] | {"id": 20, "user_id": 2}
        self.upstream.progress[20] = deepcopy(self.upstream.progress[10]) | {"id": 20}
        original = self.upstream.user_group_rate

        async def read_rate(user_id, group_id):
            if user_id == 1:
                raise RuntimeError("first target unavailable")
            return await original(user_id, group_id)

        self.upstream.user_group_rate = read_rate
        result = await self.engine.run(self.multiplier(), NOW)
        self.assertTrue(result["errors"])
        self.assertEqual(self.upstream.rate_writes, [("user", 2, 5, .5)])

    async def test_rate_is_rechecked_after_fresh_eligibility_reads(self):
        def change_rate(subscription_id, count):
            if count >= 2:
                self.upstream.rates[(1, 5)] = .8
        self.upstream.before_subscription_read = change_rate
        result = await self.engine.run(self.multiplier(), NOW)
        self.assertTrue(result["errors"])
        self.assertEqual(self.upstream.rate_writes, [])
        self.assertEqual(self.store.ledger_list("automation:"), {})

    async def test_long_scan_crossing_quota_reset_cannot_apply_using_tick_start_time(self):
        clock = [0]
        reads = 0
        original = self.upstream.subscription_progress

        async def progress(subscription_id):
            nonlocal reads
            reads += 1
            result = await original(subscription_id)
            if reads >= 2:
                clock[0] = 6 * 3600  # The window reset five hours after tick start.
            return result

        self.upstream.subscription_progress = progress
        with patch("helper.automation.time", SimpleNamespace(monotonic=lambda: clock[0])):
            result = await self.engine.run(self.multiplier(), NOW)
        self.assertTrue(result["errors"])
        self.assertEqual(self.upstream.rate_writes, [])

    async def test_disabled_saved_rule_tick_restores_owned_rate(self):
        saved = self.store.create("automations", self.multiplier())
        await self.engine.run(saved, NOW)
        self.store.update("automations", saved["id"], {"enabled": False})
        result = await self.engine.tick(NOW)
        self.assertEqual(result[0]["errors"], [])
        self.assertIsNone(self.upstream.rates[(1, 5)])

    async def test_rewards_use_previous_full_month_and_positive_actual_spend(self):
        self.upstream.spending += [{"user_id": 99, "actual_cost": 999, "tokens": 999}, {"user_id": 4, "actual_cost": 0, "tokens": 0}]
        # Duplicate IDs are rejected, so remove the prior fourth-place row.
        self.upstream.spending = [row for row in self.upstream.spending if row["user_id"] != 4 or row["actual_cost"] == 0]
        report = await self.engine.run(self.rewards(), NOW)
        self.assertEqual(report["executed"], 3)
        self.assertEqual(self.upstream.spending_calls, [("2026-10-01", "2026-10-31", "Asia/Shanghai")])
        self.assertEqual([(uid, amount) for uid, amount, _ in self.upstream.credit_writes], [(1, 30.5), (2, 20), (3, 10)])
        self.assertNotIn(99, self.upstream.balances)

    async def test_monthly_activation_after_boundary_and_missing_enabled_at_do_not_backfill(self):
        for activation in (None, "2026-11-01T00:00:00Z"):
            report = await self.engine.run(self.rewards(enabled_at=activation), NOW)
            self.assertEqual(report["executed"], 0)
        self.assertEqual(self.upstream.spending_calls, [])
        self.assertEqual(self.store.ledger_list("automation:"), {})

    async def test_approval_requires_manual_run_and_manual_can_settle_last_month(self):
        rule = self.rewards(mode="approval", enabled_at=NOW.isoformat())
        self.assertEqual((await self.engine.run(rule, NOW))["executed"], 0)
        self.assertEqual((await self.engine.run(rule, NOW, manual=True))["executed"], 3)

    async def test_global_month_snapshot_prevents_cloned_rule_double_credits_after_restart(self):
        await self.engine.run(self.rewards(), NOW)
        self.store.close()
        self.store = Store(self.path)
        self.engine = AutomationEngine(self.store, self.upstream)
        self.upstream.spending.reverse()
        clone = self.rewards(id=700, rewards=[{"rank": rank, "amount": "999"} for rank in (1, 2, 3)])
        report = await self.engine.run(clone, NOW)
        self.assertEqual(report["executed"], 0)
        self.assertEqual(len(self.upstream.credit_writes), 3)
        self.assertEqual([a["amount"] for a in report["actions"]], ["30.50", "20", "10"])
        self.assertEqual(len(self.upstream.spending_calls), 1)

    async def test_next_calendar_month_has_a_separate_settlement_and_idempotency_keys(self):
        await self.engine.run(self.rewards(), NOW)
        first_keys = {row[2] for row in self.upstream.credit_writes}
        await self.engine.run(self.rewards(), datetime(2026, 12, 1, tzinfo=timezone.utc))
        self.assertEqual(len(self.upstream.credit_writes), 6)
        second_keys = {row[2] for row in self.upstream.credit_writes[3:]}
        self.assertFalse(first_keys.intersection(second_keys))
        self.assertEqual(self.upstream.spending_calls[-1][:2], ("2026-11-01", "2026-11-30"))

    async def test_every_post_intent_credit_error_requires_review_without_replay(self):
        self.upstream.credit_error = RuntimeError("upstream returned HTTP403 after commit")
        first = await self.engine.run(self.rewards(), NOW)
        self.assertEqual(len(self.upstream.credit_writes), 3)
        self.assertTrue(first["errors"])
        self.assertTrue(all(a["status"] == "needs_review" for a in first["actions"]))
        self.upstream.credit_error = None
        second = await AutomationEngine(self.store, self.upstream).run(self.rewards(id=777), NOW, manual=True)
        self.assertEqual(second["executed"], 0)
        self.assertEqual(len(self.upstream.credit_writes), 3)
        self.assertTrue(second["errors"])

    async def test_cancellation_after_credit_leaves_pending_intent_that_is_never_replayed(self):
        self.upstream.credit_error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.engine.run(self.rewards(), NOW)
        self.assertEqual(len(self.upstream.credit_writes), 1)
        self.upstream.credit_error = None
        report = await self.engine.run(self.rewards(), NOW)
        self.assertEqual(len(self.upstream.credit_writes), 3, "Only untouched second and third rewards may execute")
        self.assertEqual(self.upstream.balances[1], 30.5)
        self.assertEqual(report["actions"][0]["status"], "pending")
        self.assertTrue(report["errors"])

    async def test_frozen_winner_becoming_disabled_is_skipped_without_replacement(self):
        def disable(user_id, count):
            if user_id == 1 and count >= 2:
                self.upstream.users[1]["status"] = "disabled"
        self.upstream.before_user_read = disable
        report = await self.engine.run(self.rewards(), NOW)
        self.assertEqual([(uid, amount) for uid, amount, _ in self.upstream.credit_writes], [(2, 20), (3, 10)])
        self.assertEqual(report["actions"][0]["status"], "skipped")
        self.assertNotIn(4, self.upstream.balances)

    async def test_unknown_role_inactive_users_and_wrong_identity_are_excluded(self):
        self.upstream.users[1]["role"] = None
        self.upstream.users[2]["status"] = "disabled"
        self.upstream.users[3]["id"] = 999
        plan = await self.engine.preview(self.rewards(), NOW)
        self.assertEqual([a["user_id"] for a in plan["actions"]], [4])

    async def test_duplicate_or_malformed_spending_aborts_without_snapshot_or_credits(self):
        original = deepcopy(self.upstream.spending)
        for bad in (original + [original[0]], [{"user_id": None, "actual_cost": 10}], [{"user_id": 1, "actual_cost": float("inf")}], [{"user_id": 1, "actual_cost": -10}]):
            self.upstream.spending = bad
            with self.assertRaises(ValueError):
                await self.engine.run(self.rewards(), NOW)
            self.assertEqual(self.store.ledger_list("automation:"), {})
            self.assertEqual(self.upstream.credit_writes, [])

    async def test_zero_rewards_do_not_consume_global_settlement(self):
        report = await self.engine.run(self.rewards(rewards=[{"rank": n, "amount": 0} for n in (1, 2, 3)]), NOW)
        self.assertEqual(report["executed"], 0)
        self.assertEqual(self.store.ledger_list("automation:"), {})
        self.assertEqual(self.upstream.credit_writes, [])

    async def test_zero_first_reward_does_not_promote_other_winners(self):
        rule = self.rewards(rewards=[{"rank": 1, "amount": 0}, {"rank": 2, "amount": 20}, {"rank": 3, "amount": 10}])
        report = await self.engine.run(rule, NOW)
        self.assertEqual(report["executed"], 2)
        self.assertEqual([(uid, amount) for uid, amount, _ in self.upstream.credit_writes], [(2, 20), (3, 10)])

    async def test_ties_follow_actual_cost_then_tokens_then_user_id(self):
        self.upstream.spending = [{"user_id": uid, "actual_cost": 10, "tokens": tokens} for uid, tokens in ((3, 90), (2, 100), (1, 100))]
        plan = await self.engine.preview(self.rewards(), NOW)
        self.assertEqual([a["user_id"] for a in plan["actions"]], [1, 2, 3])

    def test_calendar_month_handles_shanghai_boundary_year_and_leap_day(self):
        self.assertEqual(settlement_period(datetime(2026, 10, 31, 16, tzinfo=timezone.utc))[:3], ("2026-10", "2026-10-01", "2026-10-31"))
        self.assertEqual(settlement_period(datetime(2026, 12, 31, 16, tzinfo=timezone.utc))[:3], ("2026-12", "2026-12-01", "2026-12-31"))
        self.assertEqual(settlement_period(datetime(2024, 3, 1, tzinfo=timezone.utc))[:3], ("2024-02", "2024-02-01", "2024-02-29"))


if __name__ == "__main__":
    unittest.main()
