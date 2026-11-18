"""Durable gallery scheduling and paid-request intent tests without model traffic."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helper.gallery import FAILED, INTERRUPTED, Gallery, GalleryError
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


if __name__ == "__main__":
    unittest.main()
