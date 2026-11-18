"""Durable gallery scheduling and paid-request intent tests without model traffic."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helper.gallery import FAILED, INTERRUPTED, Gallery, GalleryError
from helper.gallery_generation import GalleryGenerationError, UnsupportedReasoningEffort
from helper.store import Store


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


class GalleryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.clock = Clock()
        self.calls = []
        self.result = {"kind": "html", "content": "<h1>作品</h1>", "title": "模型作品", "latency_ms": 321}

        async def generate(job):
            self.calls.append(job)
            return self.result

        self.generate = generate
        self.gallery = Gallery(self.store, generate, clock=self.clock, interval=.001)

    async def asyncTearDown(self):
        await self.gallery.close()
        self.store.close()

    def configure(self, group_id=1, **data):
        return self.gallery.configure(group_id, {"name": "示例分组", "model": "example-model", "key_id": 7, "prompt": "生成星空", **data})

    async def complete(self, group_id=1):
        pending = await self.gallery.start(group_id)
        await asyncio.gather(*list(self.gallery._tasks.values()))
        return self.gallery.work(group_id, pending["id"])

    async def test_default_is_paused_and_next_deadline_is_durable(self):
        view = self.configure()
        self.assertFalse(view["group"]["enabled"])
        self.assertEqual(view["group"]["interval_minutes"], 30)
        self.assertIsNone(view["group"]["next_at"])
        self.assertEqual(view["stats"], {"success_24h": 0, "failed_24h": 0, "total": 0})
        deadline = self.store._db.execute("SELECT next_at FROM gallery_jobs").fetchone()[0]
        self.assertEqual(deadline, (self.clock.now + timedelta(minutes=30)).timestamp())
        self.clock.advance(minutes=30)
        await self.gallery.tick()
        self.assertEqual(self.calls, [])

    async def test_manual_generation_works_while_module_and_job_disabled(self):
        self.configure()
        work = await self.complete()
        self.assertEqual(work["status"], "success")
        self.assertEqual(work["content"], self.result["content"])
        self.assertEqual(work["prompt"], "生成星空")
        self.assertEqual((work["title"], work["latency_ms"]), ("模型作品", 321))
        self.assertEqual(self.calls[0]["key_id"], 7)
        self.assertNotIn("api_key", self.calls[0])
        view = self.gallery.view(1)
        self.assertEqual(view["works"][0]["preview"], self.result["content"])
        self.assertNotIn("prompt", view["works"][0])
        self.assertEqual(view["stats"], {"success_24h": 1, "failed_24h": 0, "total": 1})

    async def test_start_returns_before_model_and_global_slot_blocks_duplicates(self):
        self.configure()
        self.configure(2)
        started, release = asyncio.Event(), asyncio.Event()

        async def generate(job):
            started.set()
            await release.wait()
            return self.result

        self.gallery.generate = generate
        pending = await self.gallery.start(1)
        self.assertEqual(pending["status"], "running")
        self.assertTrue(self.gallery.view(1)["group"]["running"])
        await started.wait()
        for group_id in (1, 2):
            with self.assertRaises(GalleryError) as error:
                await self.gallery.start(group_id)
            self.assertEqual(error.exception.status, 409)
        release.set()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        self.assertEqual(self.gallery.view(1)["stats"]["total"], 1)

    async def test_paid_intent_is_committed_before_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "gallery.db")
            store = Store(path)
            observed = []

            async def generate(job):
                other = Store(path)
                observed.append(other._db.execute("SELECT status FROM gallery_runs WHERE id=?", (job["work_id"],)).fetchone()[0])
                other.close()
                return self.result

            gallery = Gallery(store, generate, clock=self.clock)
            gallery.configure(1, {"model": "m", "key_id": 1})
            await gallery.start(1)
            await asyncio.gather(*list(gallery._tasks.values()))
            self.assertEqual(observed, ["running"])
            await gallery.close()
            store.close()

    async def test_failures_are_generic_counted_and_never_retried(self):
        self.configure()

        async def fail(job):
            self.calls.append(job)
            raise RuntimeError("ordinary-secret-key and private upstream body")

        self.gallery.generate = fail
        work = await self.complete()
        self.assertEqual(work["status"], "failed")
        self.assertEqual(work["error"], FAILED)
        self.assertIsNone(work["content"])
        view = self.gallery.view(1)
        self.assertEqual(view["stats"], {"success_24h": 0, "failed_24h": 1, "total": 0})
        self.assertNotIn("ordinary-secret-key", json.dumps(view))
        await self.gallery.tick()
        self.assertEqual(len(self.calls), 1)

    async def test_invalid_and_oversized_artifacts_fail_without_retaining_payload(self):
        self.configure()
        for invalid in ({"kind": "svg", "content": "x"}, {"kind": "text", "content": ""},
                        {"kind": "text", "content": "x" * (16 * 1024 + 1)},
                        {"kind": "html", "content": "x" * (128 * 1024 + 1)}):
            self.result = invalid
            work = await self.complete()
            self.assertEqual(work["status"], "failed")
            self.assertIsNone(work["content"])
        self.assertEqual(self.gallery.view(1)["stats"]["failed_24h"], 4)

    async def test_image_uri_and_unicode_text_with_bounded_titles(self):
        self.configure()
        self.result = {"kind": "image", "content": "data:image/png;base64,AA==", "mime": "image/png", "title": "unsafe\nheader", "latency_ms": -1}
        work = await self.complete()
        self.assertEqual(work["mime"], "image/png")
        self.assertNotIn("unsafe", work["title"])
        self.assertGreaterEqual(work["latency_ms"], 0)
        self.assertEqual(self.gallery.view(1)["works"][0]["preview"], self.result["content"])
        self.result = {"kind": "text", "content": "中文作品"}
        text = await self.complete()
        self.assertEqual(text["content_bytes"], len("中文作品".encode()))

    async def test_config_validation_no_raw_key_field_and_human_character_limits(self):
        for data in ({"api_key": "must-not-store"}, {"interval_minutes": 4}, {"interval_minutes": 1441},
                     {"enabled": 1}, {"key_id": True}, {"model": "m", "enabled": True},
                     {"name": "x" * 101}, {"prompt": "x" * 8001}, {"api_format": "other"}, {"api_format": []}):
            with self.assertRaises(GalleryError):
                self.gallery.configure(1, data)
        self.assertIsNone(self.store._db.execute("SELECT * FROM gallery_jobs").fetchone())
        saved = self.configure(name="中" * 100, prompt="中" * 8000)
        self.assertEqual(len(saved["group"]["prompt"]), 8000)

    async def test_group_limit_sorted_summaries_and_no_preview_in_catalog(self):
        with patch("helper.gallery.MAX_GROUPS", 2):
            self.configure(name="Zed")
            self.configure(2, name="Alpha")
            with self.assertRaises(GalleryError):
                self.configure(3)
            self.configure(1, name="Beta")
        await self.complete()
        groups = self.gallery.groups()
        self.assertEqual([group["group_id"] for group in groups], [2, 1])
        self.assertNotIn("preview", groups[1]["latest_work"])
        self.assertEqual(groups[1]["latest_work"]["content_bytes"], len(self.result["content"].encode()))

    async def test_group_work_isolation_and_missing_configuration(self):
        with self.assertRaises(GalleryError) as missing:
            await self.gallery.start(1)
        self.assertEqual(missing.exception.status, 404)
        self.configure()
        work = await self.complete()
        with self.assertRaises(GalleryError) as hidden:
            self.gallery.work(2, work["id"])
        self.assertEqual(hidden.exception.status, 404)
        for invalid in (True, -1, "1", 2**63):
            with self.assertRaises(GalleryError):
                self.gallery.view(invalid)

    async def test_schedule_requires_module_and_enabled_and_runs_only_one(self):
        self.configure(enabled=True, interval_minutes=5)
        self.configure(2, enabled=True, interval_minutes=5)
        self.clock.advance(minutes=5)
        await self.gallery.tick()
        self.assertEqual(self.calls, [])
        self.store.set_setting("modules", {"benchmark": True})
        self.clock.advance(minutes=5)
        await self.gallery.tick()
        self.assertEqual(len(self.gallery._tasks), 1)
        await asyncio.gather(*list(self.gallery._tasks.values()))
        self.assertEqual(len(self.calls), 1)
        await self.gallery.tick()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        self.assertEqual(len(self.calls), 2)

    async def test_skips_missed_intervals_without_catchup_burst(self):
        self.configure(enabled=True, interval_minutes=5)
        self.store.set_setting("modules", {"benchmark": True})
        self.clock.advance(hours=2)
        await self.gallery.tick()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.gallery.view(1)["group"]["next_at"], (self.clock.now + timedelta(minutes=5)).isoformat())
        self.clock.advance(minutes=5)
        await self.gallery.tick()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        self.assertEqual(len(self.calls), 1)

    async def test_saving_while_running_keeps_executed_prompt_snapshot(self):
        self.configure()
        started, release = asyncio.Event(), asyncio.Event()

        async def generate(job):
            started.set()
            await release.wait()
            self.calls.append(job)
            return self.result

        self.gallery.generate = generate
        pending = await self.gallery.start(1)
        await started.wait()
        self.configure(prompt="新的提示词", model="new-model", enabled=False)
        release.set()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        work = self.gallery.work(1, pending["id"])
        self.assertEqual((work["prompt"], work["model"]), ("生成星空", "example-model"))
        self.assertEqual(self.calls[0]["prompt"], "生成星空")
        self.assertEqual(self.gallery.view(1)["group"]["prompt"], "新的提示词")

    async def test_close_before_task_enters_records_interrupted_attempt_once(self):
        self.configure()
        pending = await self.gallery.start(1)
        await self.gallery.close()
        work = self.gallery.work(1, pending["id"])
        self.assertEqual((work["status"], work["error"]), ("failed", INTERRUPTED))
        self.assertFalse(self.gallery.view(1)["group"]["running"])
        self.assertEqual(self.calls, [])
        await self.gallery.close()
        self.assertEqual(self.gallery.view(1)["stats"]["failed_24h"], 1)
        with self.assertRaises(GalleryError) as closed:
            await self.gallery.start(1)
        self.assertEqual(closed.exception.status, 503)

    async def test_close_cancels_inflight_without_retry_and_run_loop_stops(self):
        self.configure()
        entered = asyncio.Event()

        async def blocked(job):
            self.calls.append(job)
            entered.set()
            await asyncio.Event().wait()

        self.gallery.generate = blocked
        pending = await self.gallery.start(1)
        await entered.wait()
        runner = asyncio.create_task(self.gallery.run())
        await asyncio.sleep(0)
        await self.gallery.close()
        await asyncio.wait_for(runner, 1)
        self.assertEqual(self.gallery.work(1, pending["id"])["status"], "failed")
        self.assertEqual(len(self.calls), 1)

    async def test_restart_marks_durable_incomplete_intent_failed_without_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "gallery.db")
            store = Store(path)
            original = Gallery(store, self.generate, clock=self.clock)
            original.configure(1, {"model": "m", "key_id": 1, "enabled": True, "interval_minutes": 5})
            pending = await original.start(1)
            task = original._tasks[1]
            task.cancel()  # Simulate shutdown before worker entry, without graceful close.
            await asyncio.gather(task, return_exceptions=True)
            store.close()
            self.clock.advance(hours=1)
            reopened = Store(path)
            restored = Gallery(reopened, self.generate, clock=self.clock)
            self.assertEqual(restored.work(1, pending["id"])["error"], INTERRUPTED)
            self.assertEqual(restored.view(1)["stats"]["failed_24h"], 1)
            self.assertEqual(self.calls, [])
            self.assertGreater(datetime.fromisoformat(restored.view(1)["group"]["next_at"]), self.clock.now)
            await restored.close()
            again = Gallery(reopened, self.generate, clock=self.clock)
            self.assertEqual(again.view(1)["stats"]["failed_24h"], 1)
            await again.close()
            reopened.close()

    async def test_retention_caps_history_but_preserves_24_hour_counts(self):
        self.configure(prompt="p")
        with patch("helper.gallery.MAX_WORKS_PER_GROUP", 3):
            for _ in range(7):
                await self.complete()
        view = self.gallery.view(1)
        self.assertEqual(len(view["works"]), 3)
        self.assertEqual(view["stats"], {"success_24h": 7, "failed_24h": 0, "total": 3})
        self.result = {"kind": "text", "content": "0123456789"}
        with patch("helper.gallery.MAX_HISTORY_BYTES", 25):
            await self.complete()
            await self.complete()
            used = self.store._db.execute("SELECT COALESCE(SUM(storage_bytes),0) FROM gallery_runs").fetchone()[0]
            self.assertLessEqual(used, 25)
        self.assertEqual(self.gallery.view(1)["stats"]["success_24h"], 9)

    async def test_24_hour_counts_expire_at_second_boundary(self):
        self.configure()
        await self.complete()
        self.clock.advance(seconds=1)
        self.result = {"kind": "text", "content": ""}
        await self.complete()
        self.clock.advance(hours=24)
        stats = self.gallery.view(1)["stats"]
        self.assertEqual((stats["success_24h"], stats["failed_24h"]), (0, 1))
        self.clock.advance(seconds=1)
        self.assertEqual(self.gallery.view(1)["stats"]["failed_24h"], 0)

    async def test_latest_twelve_include_errors_and_running_with_bounded_previews(self):
        self.configure()
        for _ in range(13):
            await self.complete()
        with patch("helper.gallery.MAX_PREVIEW_BYTES", len(self.result["content"].encode()) * 2):
            view = self.gallery.view(1)
        self.assertEqual(len(view["works"]), 12)
        self.assertEqual(sum("preview" in item for item in view["works"]), 2)
        self.assertEqual([item["id"] for item in view["works"]], sorted([item["id"] for item in view["works"]], reverse=True))
        self.result = {"kind": "text", "content": ""}
        await self.complete()
        pending = await self.gallery.start(1)
        self.assertEqual([item["status"] for item in self.gallery.view(1)["works"][:2]], ["running", "failed"])
        self.assertEqual(self.gallery.view(1)["works"][0]["id"], pending["id"])

    async def test_comparison_config_defaults_validation_and_member_timer_suppression(self):
        self.assertEqual(self.gallery.comparison(), {"config": None, "round": None})
        self.configure(enabled=True)
        self.configure(2, enabled=True)
        for invalid in ({"group_ids": [1]}, {"group_ids": [1, 1]}, {"group_ids": [1, True]},
                        {"group_ids": [1, 3]}, {"group_ids": [1, 2], "prompt": " "},
                        {"group_ids": [1, 2], "interval_minutes": 4}, {"group_ids": [1, 2], "api_key": "secret"}):
            with self.assertRaises(GalleryError):
                self.gallery.configure_comparison(invalid)
        configured = self.gallery.configure_comparison({"group_ids": [2, 1], "prompt": "同一道题"})
        self.assertFalse(configured["config"]["enabled"])
        self.assertEqual(configured["config"]["interval_minutes"], 30)
        self.assertIsNone(configured["config"]["next_at"])
        self.assertTrue(self.gallery.view(1)["group"]["comparison_member"])
        self.assertIsNone(self.gallery.view(1)["group"]["next_at"])
        self.assertTrue(all(group["comparison_member"] for group in self.gallery.groups()))
        for group_id in (1, 2):
            with self.assertRaises(GalleryError) as member:
                await self.gallery.start(group_id)
            self.assertEqual(member.exception.status, 409)

    async def test_same_round_uses_shared_prompt_and_frozen_model_key_per_group(self):
        self.configure(key_id=11, model="anthropic", reasoning_effort="low", prompt="本地 Prompt A")
        self.configure(2, key_id=22, model="kimi", api_format="responses", reasoning_effort="high", prompt="本地 Prompt B")
        self.gallery.configure_comparison({"group_ids": [1, 2], "prompt": "生成相同的海洋世界"})
        entered, release = asyncio.Event(), asyncio.Event()

        async def generate(job):
            self.calls.append(job)
            if job["group_id"] == 1:
                entered.set()
                await release.wait()
            return self.result

        self.gallery.generate = generate
        initial = await self.gallery.start_comparison()
        round_id = initial["round"]["id"]
        self.assertEqual([item["status"] for item in initial["round"]["items"]], ["queued", "queued"])
        await entered.wait()
        self.configure(2, key_id=99, model="changed", api_format="chat", reasoning_effort="none", prompt="新的本地 Prompt")
        self.gallery.configure_comparison({"prompt": "下一轮的题目"})
        release.set()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        current = self.gallery.comparison()
        self.assertEqual(current["round"]["status"], "success")
        self.assertEqual(current["round"]["prompt"], "生成相同的海洋世界")
        self.assertEqual([(job["model"], job["key_id"], job["api_format"]) for job in self.calls], [("anthropic", 11, "chat"), ("kimi", 22, "responses")])
        self.assertEqual([job["reasoning_effort"] for job in self.calls], ["low", "high"])
        self.assertEqual({job["prompt"] for job in self.calls}, {"生成相同的海洋世界"})
        for item in current["round"]["items"]:
            work = self.gallery.work(item["group_id"], item["work"]["id"])
            self.assertEqual((work["round_id"], work["prompt"]), (round_id, current["round"]["prompt"]))
            self.assertEqual(work["reasoning_effort"], item["reasoning_effort"])
            self.assertNotIn("key_id", item)
            self.assertNotIn("config", item)
        self.assertEqual(current["config"]["prompt"], "下一轮的题目")

    async def test_effort_validation_and_work_snapshot_survive_group_edit(self):
        for invalid in (None, True, [], "", "HIGH", "ultra"):
            with self.assertRaises(GalleryError):
                self.configure(reasoning_effort=invalid)
        self.configure(reasoning_effort="xhigh")
        work = await self.complete()
        self.configure(reasoning_effort="none")
        self.assertEqual(self.gallery.view(1)["group"]["reasoning_effort"], "none")
        self.assertEqual(self.gallery.work(1, work["id"])["reasoning_effort"], "xhigh")
        self.assertEqual(self.gallery.groups()[0]["latest_work"]["reasoning_effort"], "xhigh")

    async def test_old_database_defaults_effort_without_replaying_or_changing_schedule(self):
        self.configure(enabled=True)
        self.configure(2)
        self.gallery.configure_comparison({"group_ids": [1, 2]})
        await self.gallery.start_comparison()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        old_work = self.gallery.view(1)["works"][0]
        deadline = self.store._db.execute("SELECT next_at FROM gallery_jobs WHERE group_id=1").fetchone()[0]
        await self.gallery.close()
        with self.store._lock, self.store._db:
            self.store._db.execute("ALTER TABLE gallery_runs DROP COLUMN reasoning_effort")
            for row in self.store._db.execute("SELECT group_id,config FROM gallery_jobs").fetchall():
                cfg = json.loads(row["config"])
                cfg.pop("reasoning_effort")
                self.store._db.execute("UPDATE gallery_jobs SET config=? WHERE group_id=?", (json.dumps(cfg), row["group_id"]))
            row = self.store._db.execute("SELECT items FROM gallery_rounds").fetchone()
            items = json.loads(row[0])
            for item in items:
                item.pop("reasoning_effort")
                item["config"].pop("reasoning_effort")
            self.store._db.execute("UPDATE gallery_rounds SET items=?", (json.dumps(items),))
        self.gallery = Gallery(self.store, self.generate, clock=self.clock)
        self.assertEqual(self.gallery.view(1)["group"]["reasoning_effort"], "default")
        self.assertEqual(self.gallery.work(1, old_work["id"])["reasoning_effort"], "default")
        self.assertTrue(all(item["reasoning_effort"] == "default" for item in self.gallery.comparison()["round"]["items"]))
        self.assertEqual(self.store._db.execute("SELECT next_at FROM gallery_jobs WHERE group_id=1").fetchone()[0], deadline)
        self.assertEqual(len(self.calls), 2)

    async def test_only_fixed_effort_rejection_is_public_and_round_continues(self):
        self.configure(reasoning_effort="high")
        self.configure(2, reasoning_effort="default")
        self.gallery.configure_comparison({"group_ids": [1, 2]})

        async def generate(job):
            self.calls.append(job)
            if job["group_id"] == 1:
                raise UnsupportedReasoningEffort()
            raise GalleryGenerationError("secret model key from an arbitrary error")

        self.gallery.generate = generate
        await self.gallery.start_comparison()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        first, second = self.gallery.comparison()["round"]["items"]
        self.assertEqual(first["work"]["error"], str(UnsupportedReasoningEffort()))
        self.assertEqual(second["work"]["error"], FAILED)
        self.assertNotIn("secret model key", json.dumps(self.gallery.comparison()))
        self.assertEqual(len(self.calls), 2)

    async def test_comparison_failure_continues_next_and_all_failures_are_visible(self):
        self.configure()
        self.configure(2)
        self.configure(3)
        self.gallery.configure_comparison({"group_ids": [1, 2, 3], "prompt": "共同题目"})

        async def generate(job):
            self.calls.append(job)
            if job["group_id"] != 2:
                raise RuntimeError("secret upstream details")
            return self.result

        self.gallery.generate = generate
        await self.gallery.start_comparison()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        current = self.gallery.comparison()
        self.assertEqual(current["round"]["status"], "partial")
        self.assertEqual([item["status"] for item in current["round"]["items"]], ["failed", "success", "failed"])
        self.assertEqual([job["group_id"] for job in self.calls], [1, 2, 3])
        self.assertNotIn("secret upstream", json.dumps(current))
        self.assertEqual(self.gallery.view(1)["stats"]["failed_24h"], 1)
        self.result = {"kind": "text", "content": ""}
        self.gallery.generate = self.generate
        await self.gallery.start_comparison()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        self.assertEqual(self.gallery.comparison()["round"]["status"], "failed")

    async def test_comparison_has_one_global_slot_and_no_overlapping_manual_work(self):
        for group_id in (1, 2, 3):
            self.configure(group_id)
        self.gallery.configure_comparison({"group_ids": [1, 2]})
        entered, release = asyncio.Event(), asyncio.Event()
        active = maximum = 0

        async def generate(job):
            nonlocal active, maximum
            self.calls.append(job)
            active += 1
            maximum = max(maximum, active)
            entered.set()
            await release.wait()
            active -= 1
            return self.result

        self.gallery.generate = generate
        await self.gallery.start_comparison()
        await entered.wait()
        for operation in (self.gallery.start_comparison(), self.gallery.start(3)):
            with self.assertRaises(GalleryError) as busy:
                await operation
            self.assertEqual(busy.exception.status, 409)
        release.set()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        self.assertEqual((maximum, len(self.calls)), (1, 2))
        await self.complete(3)
        self.assertEqual(len(self.calls), 3)

    async def test_shared_timer_is_prioritized_and_member_timers_never_double_bill(self):
        for group_id in (1, 2, 3):
            self.configure(group_id, enabled=True, interval_minutes=5)
        self.store.set_setting("modules", {"benchmark": True})
        self.gallery.configure_comparison({"group_ids": [1, 2], "prompt": "定时统一题目", "enabled": True, "interval_minutes": 5})
        self.clock.advance(minutes=5)
        await self.gallery.tick()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        self.assertEqual([job["group_id"] for job in self.calls], [1, 2])
        self.assertEqual({job["prompt"] for job in self.calls}, {"定时统一题目"})
        await self.gallery.tick()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        self.assertEqual([job["group_id"] for job in self.calls], [1, 2, 3])
        for _ in range(3):
            await self.gallery.tick()
        self.assertEqual(len(self.calls), 3)
        self.gallery.pause_comparison()
        self.clock.advance(minutes=5)
        await self.gallery.tick()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        self.assertEqual([job["group_id"] for job in self.calls[-1:]], [3])

    async def test_comparison_timer_requires_module_and_skips_offline_periods(self):
        self.configure()
        self.configure(2)
        self.gallery.configure_comparison({"group_ids": [1, 2], "enabled": True, "interval_minutes": 5})
        self.clock.advance(minutes=5)
        await self.gallery.tick()
        self.assertEqual(self.calls, [])
        self.store.set_setting("modules", {"benchmark": True})
        self.clock.advance(hours=3)
        await self.gallery.tick()
        self.assertEqual(self.calls, [])
        self.clock.advance(minutes=5)
        await self.gallery.tick()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        self.assertEqual(len(self.calls), 2)

    async def test_leaving_comparison_rearms_individual_timer_and_latest_round_only(self):
        for group_id in (1, 2, 3):
            self.configure(group_id, enabled=True, interval_minutes=5)
        self.gallery.configure_comparison({"group_ids": [1, 2]})
        first = await self.gallery.start_comparison()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        self.clock.advance(minutes=2)
        self.gallery.configure_comparison({"group_ids": [2, 3]})
        group = self.gallery.view(1)["group"]
        self.assertFalse(group["comparison_member"])
        self.assertEqual(group["next_at"], (self.clock.now + timedelta(minutes=5)).isoformat())
        second = await self.gallery.start_comparison()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        self.assertGreater(second["round"]["id"], first["round"]["id"])
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM gallery_rounds").fetchone()[0], 1)
        self.assertEqual(self.gallery.view(1)["works"][0]["round_id"], first["round"]["id"])

    async def test_comparison_shutdown_interrupts_pending_and_running_without_next_charge(self):
        self.configure()
        self.configure(2)
        self.gallery.configure_comparison({"group_ids": [1, 2]})
        entered = asyncio.Event()

        async def blocked(job):
            self.calls.append(job)
            entered.set()
            await asyncio.Event().wait()

        self.gallery.generate = blocked
        await self.gallery.start_comparison()
        await entered.wait()
        await self.gallery.close()
        current = self.gallery.comparison()
        self.assertEqual(current["round"]["status"], "interrupted")
        self.assertEqual([item["status"] for item in current["round"]["items"]], ["interrupted", "interrupted"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.gallery.view(1)["stats"]["failed_24h"], 1)
        self.assertEqual(self.gallery.view(2)["stats"]["failed_24h"], 0)

    async def test_comparison_recovery_preserves_success_and_never_resumes_queued_work(self):
        for group_id in (1, 2, 3):
            self.configure(group_id)
        self.gallery.configure_comparison({"group_ids": [1, 2, 3], "prompt": "已冻结的题目", "enabled": True})
        initial = await self.gallery.start_comparison()
        task = self.gallery._tasks["comparison"]
        task.cancel()  # Simulate a crash before the round worker enters.
        await asyncio.gather(task, return_exceptions=True)
        self.gallery._tasks.clear()
        with self.store._lock, self.store._db:
            row = self.store._db.execute("SELECT * FROM gallery_rounds").fetchone()
            items = json.loads(row["items"])
            first_cfg = items[0]["config"] | {"prompt": row["prompt"]}
            first_id = self.gallery._begin_work(1, first_cfg, self.clock.now, row["id"])
            self.gallery._finish(1, first_id, self.clock.now, status="success", result=self.result)
            items[0].update(status="running", work_id=first_id)  # Crash after success, before progress update.
            second_cfg = items[1]["config"] | {"prompt": row["prompt"]}
            second_id = self.gallery._begin_work(2, second_cfg, self.clock.now, row["id"])
            items[1].update(status="running", work_id=second_id)
            self.store._db.execute("UPDATE gallery_rounds SET items=?", (json.dumps(items),))
        recovered = Gallery(self.store, self.generate, clock=self.clock)
        current = recovered.comparison()
        self.assertEqual(current["round"]["id"], initial["round"]["id"])
        self.assertEqual(current["round"]["status"], "interrupted")
        self.assertEqual([item["status"] for item in current["round"]["items"]], ["success", "interrupted", "interrupted"])
        self.assertEqual(recovered.work(2, second_id)["error"], INTERRUPTED)
        self.assertEqual(self.calls, [])
        self.store.set_setting("modules", {"benchmark": True})
        await recovered.tick()
        self.assertEqual(self.calls, [])
        await recovered.close()

    async def test_comparison_intent_commits_before_each_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "gallery.db")
            store = Store(path)
            observed = []

            async def generate(job):
                other = Store(path)
                round_row = other._db.execute("SELECT * FROM gallery_rounds WHERE id=?", (job["round_id"],)).fetchone()
                work_row = other._db.execute("SELECT * FROM gallery_runs WHERE id=?", (job["work_id"],)).fetchone()
                item = next(item for item in json.loads(round_row["items"]) if item["group_id"] == job["group_id"])
                observed.append((item["status"], item["work_id"], work_row["status"]))
                other.close()
                return self.result

            gallery = Gallery(store, generate, clock=self.clock)
            for group_id in (1, 2):
                gallery.configure(group_id, {"model": "m", "key_id": group_id})
            gallery.configure_comparison({"group_ids": [1, 2]})
            await gallery.start_comparison()
            await asyncio.gather(*list(gallery._tasks.values()))
            self.assertEqual([(status, work_status) for status, _, work_status in observed], [("running", "running"), ("running", "running")])
            self.assertEqual(len({work_id for _, work_id, _ in observed}), 2)
            await gallery.close()
            store.close()

    async def test_comparison_deadline_stays_anchored_to_start_and_clear_releases_members(self):
        self.configure(enabled=True, interval_minutes=5)
        self.configure(2, enabled=True, interval_minutes=5)
        self.gallery.configure_comparison({"group_ids": [1, 2], "enabled": True, "interval_minutes": 30})

        async def generate(job):
            self.calls.append(job)
            self.clock.advance(minutes=1)
            return self.result

        self.gallery.generate = generate
        initial = await self.gallery.start_comparison()
        deadline = initial["config"]["next_at"]
        await asyncio.gather(*list(self.gallery._tasks.values()))
        self.assertEqual(self.gallery.comparison()["config"]["next_at"], deadline)
        group = self.gallery.view(1)["group"]
        self.assertTrue(group["comparison_enabled"])
        self.assertEqual(group["comparison_next_at"], deadline)
        cleared = self.gallery.clear_comparison()
        self.assertIsNone(cleared["config"])
        self.assertEqual(cleared["round"]["status"], "success")
        for group_id in (1, 2):
            group = self.gallery.view(group_id)["group"]
            self.assertFalse(group["comparison_member"])
            self.assertEqual(group["next_at"], (self.clock.now + timedelta(minutes=5)).isoformat())
            self.assertEqual(len(self.gallery.view(group_id)["works"]), 1)

    async def test_clearing_comparison_during_round_preserves_snapshot_and_finishes(self):
        self.configure()
        self.configure(2)
        self.gallery.configure_comparison({"group_ids": [1, 2], "prompt": "清除前的题目"})
        entered, release = asyncio.Event(), asyncio.Event()

        async def generate(job):
            self.calls.append(job)
            if job["group_id"] == 1:
                entered.set()
                await release.wait()
            return self.result

        self.gallery.generate = generate
        await self.gallery.start_comparison()
        await entered.wait()
        self.gallery.clear_comparison()
        release.set()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        self.assertIsNone(self.gallery.comparison()["config"])
        self.assertEqual(self.gallery.comparison()["round"]["status"], "success")
        self.assertEqual([job["prompt"] for job in self.calls], ["清除前的题目", "清除前的题目"])

    async def test_latest_comparison_round_is_kept_as_a_unit_during_retention(self):
        for group_id in (1, 2, 3):
            self.configure(group_id, prompt="p")
        self.result = {"kind": "text", "content": "x" * 150}
        previous = await self.complete(3)
        self.gallery.configure_comparison({"group_ids": [1, 2], "prompt": "p"})
        self.result = {"kind": "text", "content": "x" * 90}
        with patch("helper.gallery.MAX_HISTORY_BYTES", 280):
            await self.gallery.start_comparison()
            await asyncio.gather(*list(self.gallery._tasks.values()))
            await self.complete(3)
            current = self.gallery.comparison()
            self.assertEqual(current["round"]["status"], "success")
            self.assertTrue(all(item.get("work", {}).get("status") == "success" for item in current["round"]["items"]))
            self.assertLessEqual(self.store._db.execute("SELECT SUM(storage_bytes) FROM gallery_runs").fetchone()[0], 280)
            with self.assertRaises(GalleryError):
                self.gallery.work(3, previous["id"])
        self.gallery.clear_comparison()
        with patch("helper.gallery.MAX_WORKS_PER_GROUP", 2):
            for _ in range(4):
                await self.complete(1)
        latest = self.gallery.comparison()["round"]["items"][0]
        self.assertEqual(latest["work"]["status"], "success")
        self.assertEqual(self.gallery.view(1)["stats"]["total"], 2)

    async def test_large_image_preview_is_omitted_without_losing_metadata_or_detail(self):
        self.configure()
        self.configure(2)
        self.result = {"kind": "image", "content": "data:image/png;base64," + "A" * (200 * 1024), "mime": "image/png"}
        self.gallery.configure_comparison({"group_ids": [1, 2]})
        await self.gallery.start_comparison()
        await asyncio.gather(*list(self.gallery._tasks.values()))
        for item in self.gallery.comparison()["round"]["items"]:
            self.assertNotIn("preview", item["work"])
            self.assertEqual(item["work"]["content_bytes"], len(self.result["content"]))
            group_work = self.gallery.view(item["group_id"])["works"][0]
            self.assertNotIn("preview", group_work)
            self.assertEqual(group_work["content_bytes"], len(self.result["content"]))
            self.assertEqual(self.gallery.work(item["group_id"], item["work"]["id"])["content"], self.result["content"])


if __name__ == "__main__":
    unittest.main()
