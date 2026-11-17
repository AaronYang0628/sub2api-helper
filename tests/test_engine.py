import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helper.config import Config
from helper.engine import Engine, eligible_user, in_window
from helper.mailer import MailDeliveryError
from helper.store import Store


NOW = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)  # Tuesday 23:00 Shanghai
OLD = "2026-10-01T00:00:00Z"


class Upstream:
    def __init__(self):
        self.accounts = [{"id": 1, "schedulable": False}, {"id": 2, "schedulable": False}]
        self.users = [{"id": 7, "username": "user", "email": "user@example.test", "status": "active", "role": "user", "last_used_at": OLD}]
        self.writes = []
        self.disable_failures = 0
        self.account_failures = set()
        self.fresh_usage = None
        self.after_mail_usage = None
        self.reads = 0

    async def list_accounts(self):
        return deepcopy(self.accounts)

    async def set_schedulable(self, account_id, enabled):
        if account_id in self.account_failures:
            raise RuntimeError("upstream failure")
        self.writes.append(("account", account_id, enabled))
        next(a for a in self.accounts if a["id"] == account_id)["schedulable"] = enabled

    async def list_users(self):
        return deepcopy(self.users)

    async def get_user(self, user_id):
        self.reads += 1
        user = deepcopy(next(u for u in self.users if u["id"] == user_id))
        if self.fresh_usage:
            user["last_used_at"] = self.fresh_usage
        if self.after_mail_usage and self.reads >= 2:
            user["last_used_at"] = self.after_mail_usage
        return user

    async def set_user_status(self, user_id, status):
        if self.disable_failures:
            self.disable_failures -= 1
            raise RuntimeError("temporarily unavailable")
        self.writes.append(("user", user_id, status))
        next(u for u in self.users if u["id"] == user_id)["status"] = status


class Mailer:
    configured = True

    def __init__(self):
        self.sent = []
        self.error = None

    async def send(self, to, subject, body):
        if self.error:
            raise self.error
        self.sent.append((to, subject, body))


class WindowTests(unittest.TestCase):
    def schedule(self, **kwargs):
        return {"start": "23:00", "end": "09:00", "timezone": "Asia/Shanghai", "weekdays": [1]} | kwargs

    def test_overnight_boundaries_and_opening_weekday(self):
        schedule = self.schedule()
        self.assertFalse(in_window(schedule, datetime(2026, 11, 17, 14, 59, 59, tzinfo=timezone.utc)))
        self.assertTrue(in_window(schedule, NOW))
        self.assertTrue(in_window(schedule, datetime(2026, 11, 18, 0, 59, 59, tzinfo=timezone.utc)))
        self.assertFalse(in_window(schedule, datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc)))
        self.assertFalse(in_window(schedule, datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)))

    def test_same_day_start_inclusive_end_exclusive(self):
        schedule = self.schedule(start="09:00", end="18:00")
        self.assertTrue(in_window(schedule, datetime(2026, 11, 17, 1, 0, tzinfo=timezone.utc)))
        self.assertFalse(in_window(schedule, datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)))

    def test_dst_uses_local_wall_clock(self):
        schedule = self.schedule(start="01:00", end="03:00", timezone="America/New_York", weekdays=[6])
        self.assertTrue(in_window(schedule, datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)))
        self.assertTrue(in_window(schedule, datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)))
        self.assertFalse(in_window(schedule, datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)))

    def test_naive_time_and_equal_window_rejected(self):
        with self.assertRaises(ValueError):
            in_window(self.schedule(), datetime(2026, 11, 17))
        with self.assertRaises(ValueError):
            in_window(self.schedule(end="23:00"), NOW)

    def test_missing_usage_never_falls_back_to_created_or_panel_activity(self):
        user = {"status": "active", "role": "user", "created_at": OLD, "last_active_at": OLD}
        self.assertIsNone(eligible_user(user, {"days": 30}, NOW))
        user["last_used_at"] = "invalid"
        self.assertIsNone(eligible_user(user, {"days": 30}, NOW))


class ConfigTests(unittest.TestCase):
    def config(self, **changes):
        environment = {
            "HELPER_ADMIN_TOKEN": "local-admin-token-very-secret",
            "SUB2API_ADMIN_KEY": "upstream-secret-key",
            "SUB2API_URL": "https://sub2api.example.test",
            "TZ": "Asia/Shanghai",
        } | changes
        with patch.dict("os.environ", environment, clear=True):
            return Config.from_env()

    def test_environment_validated_without_exposing_credentials(self):
        cfg = self.config(SMTP_PASSWORD="smtp-secret-password")
        self.assertEqual(cfg.timezone, "Asia/Shanghai")
        self.assertEqual(cfg.smtp_tls, "starttls")
        for secret in ("local-admin-token-very-secret", "upstream-secret-key", "smtp-secret-password"):
            self.assertNotIn(secret, repr(cfg))

    def test_plaintext_smtp_and_credential_urls_rejected(self):
        for changes in (
            {"SUB2API_URL": "https://user:password@example.test"},
            {"SUB2API_URL": "https://example.test?apikey=secret"},
            {"SMTP_TLS": "none"},
            {"SMTP_HOST": "smtp.example.test"},
            {"TZ": "made-up/timezone"},
            {"HELPER_ADMIN_TOKEN": "short"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.config(**changes)


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "helper.db")
        self.store = Store(self.path)
        self.upstream = Upstream()
        self.mailer = Mailer()
        self.engine = Engine(self.store, self.upstream, self.mailer)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def schedule(self, **kwargs):
        return self.store.create("schedules", {
            "enabled": True, "account_ids": [1], "start": "23:00", "end": "09:00",
            "timezone": "Asia/Shanghai", "weekdays": list(range(7)),
        } | kwargs)

    def rule(self, **kwargs):
        return self.store.create("rules", {
            "enabled": True, "days": 30, "disable_after_email": True,
            "subject": "Hello {username}", "body": "{email} inactive for {days} days; last used {last_used_at}",
        } | kwargs)

    async def test_reconciles_on_restart_and_avoids_duplicate_writes(self):
        self.schedule()
        await self.engine.tick(NOW)
        await Engine(self.store, self.upstream, self.mailer).tick(NOW)
        self.assertEqual(self.upstream.writes, [("account", 1, True)])
        await self.engine.tick(datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc))
        self.assertEqual(self.upstream.writes[-1], ("account", 1, False))

    async def test_disabled_schedule_stops_managing(self):
        self.schedule(enabled=False)
        await self.engine.tick(NOW)
        self.assertEqual(self.upstream.writes, [])

    async def test_account_failure_does_not_block_other_accounts(self):
        schedule = self.schedule(account_ids=[1, 2])
        self.upstream.account_failures.add(1)
        await self.engine.tick(NOW)
        self.assertEqual(self.upstream.writes, [("account", 2, True)])
        self.assertIsNotNone(self.store.get("schedules", schedule["id"])["last_error"])

    async def test_conflicting_saved_schedules_fail_closed(self):
        self.schedule()
        self.schedule()
        await self.engine.tick(NOW)
        self.assertEqual(self.upstream.writes, [])

    async def test_email_then_disable_once_with_persistent_ledger(self):
        self.rule()
        await self.engine.tick(NOW)
        self.assertEqual(len(self.mailer.sent), 1)
        self.assertIn("30 days", self.mailer.sent[0][2])
        self.assertEqual(self.upstream.writes, [("user", 7, "disabled")])
        # Emulate restoring stale upstream state after a process restart.
        self.upstream.users[0]["status"] = "active"
        self.store.close()
        self.store = Store(self.path)
        await Engine(self.store, self.upstream, self.mailer).tick(NOW)
        self.assertEqual(len(self.mailer.sent), 1)
        self.assertEqual(len(self.upstream.writes), 1)

    async def test_disable_retry_does_not_resend_mail(self):
        self.rule()
        self.upstream.disable_failures = 1
        await self.engine.tick(NOW)
        self.assertEqual(self.upstream.writes, [])
        self.assertEqual(len(self.mailer.sent), 1)
        await self.engine.tick(NOW)
        self.assertEqual(len(self.mailer.sent), 1)
        self.assertEqual(self.upstream.writes, [("user", 7, "disabled")])

    async def test_ambiguous_email_failure_never_disables_or_resends(self):
        self.rule()
        self.mailer.error = RuntimeError("connection broke after DATA")
        await self.engine.tick(NOW)
        self.mailer.error = None
        await Engine(self.store, self.upstream, self.mailer).tick(NOW)
        self.assertEqual(self.mailer.sent, [])
        self.assertEqual(self.upstream.writes, [])
        self.assertTrue(any(a["action"] == "rule.mail_review" for a in self.store.audits()))

    async def test_definitively_failed_email_retries_after_backoff(self):
        self.rule()
        self.mailer.error = MailDeliveryError(retryable=True)
        await self.engine.tick(NOW)
        self.assertEqual(self.upstream.writes, [])
        self.mailer.error = None
        await self.engine.tick(NOW)
        self.assertEqual(self.mailer.sent, [])
        await self.engine.tick(datetime(2026, 11, 17, 15, 15, tzinfo=timezone.utc))
        self.assertEqual(len(self.mailer.sent), 1)
        self.assertEqual(self.upstream.writes, [("user", 7, "disabled")])

    async def test_usage_change_after_list_prevents_mail_and_disable(self):
        self.rule()
        self.upstream.fresh_usage = NOW.isoformat()
        await self.engine.tick(NOW)
        self.assertEqual(self.mailer.sent, [])
        self.assertEqual(self.upstream.writes, [])

    async def test_usage_change_during_mail_prevents_disable(self):
        self.rule()
        self.upstream.after_mail_usage = NOW.isoformat()
        await self.engine.tick(NOW)
        self.assertEqual(len(self.mailer.sent), 1)
        self.assertEqual(self.upstream.writes, [])

    async def test_only_active_non_admin_users_with_confirmed_usage(self):
        self.rule()
        base = self.upstream.users[0]
        self.upstream.users = [
            base | {"id": 1, "role": "admin"},
            base | {"id": 2, "status": "disabled"},
            base | {"id": 3, "last_used_at": None, "created_at": OLD},
            base | {"id": 4, "last_used_at": "not a date"},
            base | {"id": 5, "last_used_at": "2027-01-01T00:00:00Z"},
            base | {"id": 6, "role": None},
        ]
        await self.engine.tick(NOW)
        self.assertEqual(self.mailer.sent, [])
        self.assertEqual(self.upstream.writes, [])

    async def test_smtp_missing_never_disables(self):
        self.rule()
        self.mailer.configured = False
        await self.engine.tick(NOW)
        self.assertEqual(self.upstream.writes, [])
        self.assertEqual(self.mailer.sent, [])

    async def test_run_stop(self):
        task = asyncio.create_task(self.engine.run())
        await asyncio.sleep(0)
        self.engine.stop()
        await asyncio.wait_for(task, 1)
        self.assertFalse(self.engine.status["running"])
        self.assertIsNotNone(self.engine.status["last_tick_at"])


if __name__ == "__main__":
    unittest.main()
