"""Durable, opt-in model artwork generation, separate from account maintenance."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import time

from .gallery_generation import REASONING_EFFORTS, UnsupportedReasoningEffort


MAX_GROUPS = 32
MAX_WORKS_PER_GROUP = 500
CONTENT_LIMITS = {"html": 128 * 1024, "text": 16 * 1024, "image": 3 * 1024 * 1024}
# A complete latest round (32 images of at most 3 MiB) remains retainable.
MAX_HISTORY_BYTES = 128 * 1024 * 1024
MAX_PREVIEW_BYTES = 1024 * 1024
FAILED = "生成失败，请检查模型、Key 权限或服务连接"
INTERRUPTED = "生成已中断，结果未确认，本次请求不会自动重试"
DEFAULT_PROMPT = "请生成一个精美、有创意、可交互的独立 HTML 或 SVG 作品。只输出完整代码，不使用外部资源。"
FIELDS = {"name", "model", "key_id", "api_format", "reasoning_effort", "prompt", "enabled", "interval_minutes"}
METADATA_COLUMNS = "id,group_id,round_id,title,model,reasoning_effort,created_at,finished_at,latency_ms,status,kind,mime,error,storage_bytes-length(CAST(prompt AS BLOB)) AS content_bytes"
PREVIEW_COLUMNS = METADATA_COLUMNS + ",CASE WHEN storage_bytes-length(CAST(prompt AS BLOB))<=131072 THEN content ELSE NULL END AS content"
VERDICT_ORDER = {"regressed": 0, "failed": 1, "improved": 2, "stable": 2, "no_baseline": 3}


def _benchmark_verdict(summary, gallery_status):
    latest = summary["latest"] if summary else None
    graded = summary["graded"] if summary else []
    previous = graded[1] if len(graded) >= 2 else None
    if latest and (
        latest.get("status") == "failed"
        or (latest.get("graded", 0) == 0 and latest.get("error_count", 0) > 0)
        or (latest.get("graded", 0) > 0 and latest.get("passed", 0) == 0)
    ):
        verdict = "failed"
    elif gallery_status in {"failed", "interrupted"}:
        verdict = "failed"
    elif len(graded) >= 2:
        cur, prev = graded[0], graded[1]
        left = cur["passed"] * prev["graded"]
        right = prev["passed"] * cur["graded"]
        if left < right:
            verdict = "regressed"
        elif left > right:
            verdict = "improved"
        else:
            verdict = "stable"
        latest = cur
    else:
        verdict = "no_baseline"
        if graded:
            latest = graded[0]
    return {"verdict": verdict, "latest": latest, "previous": previous}


class GalleryError(RuntimeError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def _id(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise GalleryError("分组或作品 ID 无效")
    return value


class Gallery:
    def __init__(self, store, generate, interval=15, clock=None):
        self.store, self.generate, self.interval = store, generate, interval
        self._db, self._lock = store._db, store._lock
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._tasks = {}
        self._start_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._closed = False
        with self._lock, self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS gallery_jobs (
                  group_id INTEGER PRIMARY KEY, config TEXT NOT NULL,
                  next_at REAL NOT NULL, running_id INTEGER, last_error TEXT,
                  updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS gallery_runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL,
                  title TEXT NOT NULL, model TEXT NOT NULL, prompt TEXT NOT NULL,
                  created_at TEXT NOT NULL, finished_at TEXT, latency_ms INTEGER NOT NULL DEFAULT 0,
                  status TEXT NOT NULL, kind TEXT, mime TEXT, content TEXT,
                  error TEXT, storage_bytes INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS gallery_group_history ON gallery_runs(group_id,id DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS gallery_single_running ON gallery_runs((1)) WHERE status='running';
                CREATE TABLE IF NOT EXISTS gallery_stats (
                  group_id INTEGER NOT NULL, bucket INTEGER NOT NULL, counts TEXT NOT NULL,
                  PRIMARY KEY(group_id,bucket));
                CREATE TABLE IF NOT EXISTS gallery_comparison (
                  id INTEGER PRIMARY KEY CHECK(id=1), config TEXT NOT NULL, next_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS gallery_rounds (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, prompt TEXT NOT NULL,
                  items TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT);
            """)
            if "round_id" not in {row["name"] for row in self._db.execute("PRAGMA table_info(gallery_runs)")}:
                self._db.execute("ALTER TABLE gallery_runs ADD COLUMN round_id INTEGER")
            if "reasoning_effort" not in {row["name"] for row in self._db.execute("PRAGMA table_info(gallery_runs)")}:
                self._db.execute("ALTER TABLE gallery_runs ADD COLUMN reasoning_effort TEXT NOT NULL DEFAULT 'default'")
            now = self._now()
            interrupted = self._db.execute("SELECT * FROM gallery_runs WHERE status='running'").fetchall()
            for run in interrupted:
                self._finish(run["group_id"], run["id"], now, status="failed", error=INTERRUPTED)
            # Expired offline periods are skipped, never replayed as paid calls.
            for row in self._db.execute("SELECT * FROM gallery_jobs").fetchall():
                cfg = json.loads(row["config"])
                if "reasoning_effort" not in cfg:
                    cfg["reasoning_effort"] = "default"
                    self._db.execute("UPDATE gallery_jobs SET config=? WHERE group_id=?", (json.dumps(cfg), row["group_id"]))
                if row["next_at"] <= now.timestamp() or row["running_id"] is not None:
                    self._db.execute("UPDATE gallery_jobs SET running_id=NULL,next_at=? WHERE group_id=?",
                                     ((now + timedelta(minutes=cfg["interval_minutes"])).timestamp(), row["group_id"]))
            self._settle_comparison(now, interrupt=True)
            comparison = self._db.execute("SELECT * FROM gallery_comparison WHERE id=1").fetchone()
            if comparison and comparison["next_at"] <= now.timestamp():
                cfg = json.loads(comparison["config"])
                self._db.execute("UPDATE gallery_comparison SET next_at=? WHERE id=1", ((now + timedelta(minutes=cfg["interval_minutes"])).timestamp(),))
            self._prune(now)

    def _now(self, now=None):
        now = now or self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise GalleryError("生成任务需要带时区的时间")
        return now.astimezone(timezone.utc)

    def _job(self, group_id):
        row = self._db.execute("SELECT * FROM gallery_jobs WHERE group_id=?", (_id(group_id),)).fetchone()
        if row is None:
            raise GalleryError("请先保存该分组的生成配置", 404)
        return row

    def _config(self, group_id, data, previous=None):
        if not isinstance(data, dict) or set(data) - FIELDS:
            raise GalleryError("生成配置包含不支持的字段")
        cfg = {"name": f"分组 #{group_id}", "model": "", "key_id": None, "api_format": "chat",
               "reasoning_effort": "default", "prompt": DEFAULT_PROMPT, "enabled": False, "interval_minutes": 30} | (previous or {}) | data
        for field, limit in (("name", 100), ("model", 150), ("prompt", 8000)):
            if not isinstance(cfg[field], str) or len(cfg[field]) > limit:
                raise GalleryError("生成配置文本无效或过长")
            cfg[field] = cfg[field].strip()
        if not cfg["name"] or not cfg["prompt"] or type(cfg["enabled"]) is not bool:
            raise GalleryError("分组名称、提示词或开关配置无效")
        if type(cfg["interval_minutes"]) is not int or not 5 <= cfg["interval_minutes"] <= 1440:
            raise GalleryError("自动生成间隔应为 5 至 1440 分钟")
        if not isinstance(cfg["api_format"], str) or cfg["api_format"] not in {"chat", "responses"}:
            raise GalleryError("模型接口格式无效")
        if not isinstance(cfg["reasoning_effort"], str) or cfg["reasoning_effort"] not in REASONING_EFFORTS:
            raise GalleryError("思考力度配置无效")
        if cfg["key_id"] is not None:
            _id(cfg["key_id"])
        if cfg["enabled"]:
            self._ready(cfg)
        return cfg

    def _ready(self, cfg):
        if not cfg["model"] or cfg["key_id"] is None:
            raise GalleryError("请先选择模型和对应分组的 API Key", 409)

    def configure(self, group_id, data):
        if self._closed:
            raise GalleryError("生成服务正在停止", 503)
        group_id, now = _id(group_id), self._now()
        with self._lock, self._db:
            previous = self._db.execute("SELECT config FROM gallery_jobs WHERE group_id=?", (group_id,)).fetchone()
            if previous is None and self._db.execute("SELECT COUNT(*) FROM gallery_jobs").fetchone()[0] >= MAX_GROUPS:
                raise GalleryError("最多配置 32 个生成分组", 409)
            cfg = self._config(group_id, data, json.loads(previous["config"]) if previous else None)
            self._db.execute("""INSERT INTO gallery_jobs(group_id,config,next_at,updated_at) VALUES (?,?,?,?)
                ON CONFLICT(group_id) DO UPDATE SET config=excluded.config,next_at=excluded.next_at,updated_at=excluded.updated_at""",
                (group_id, json.dumps(cfg), (now + timedelta(minutes=cfg["interval_minutes"])).timestamp(), now.isoformat()))
            self._prune(now)
        return self.view(group_id)

    def _metadata(self, row):
        content_bytes = row["content_bytes"] if "content_bytes" in row.keys() else len((row["content"] or "").encode("utf-8"))
        return {key: row[key] for key in ("id", "group_id", "round_id", "title", "model", "reasoning_effort", "created_at", "finished_at", "latency_ms", "status", "kind", "mime", "error")} | {"content_bytes": content_bytes}

    def _stats(self, group_id, now):
        cutoff = int(now.timestamp()) - 24 * 3600
        success = failed = 0
        for row in self._db.execute("SELECT bucket,counts FROM gallery_stats WHERE group_id=? AND bucket>=?", (group_id, cutoff // 60 * 60)):
            for second, counts in json.loads(row["counts"]).items():
                if cutoff <= row["bucket"] + int(second) <= int(now.timestamp()):
                    success += counts[0]
                    failed += counts[1]
        total = self._db.execute("SELECT COUNT(*) FROM gallery_runs WHERE group_id=? AND status='success'", (group_id,)).fetchone()[0]
        return {"success_24h": success, "failed_24h": failed, "total": total}

    def view(self, group_id, now=None):
        now = self._now(now)
        with self._lock:
            row = self._job(group_id)
            cfg = json.loads(row["config"])
            shared = self._comparison_fields(group_id, self._comparison_schedule())
            group = cfg | shared | {"group_id": group_id, "next_at": datetime.fromtimestamp(row["next_at"], timezone.utc).isoformat() if cfg["enabled"] and not shared["comparison_member"] else None,
                           "running": row["running_id"] is not None, "last_error": row["last_error"]}
            works, preview_bytes = [], 0
            for work in self._db.execute(f"SELECT {PREVIEW_COLUMNS} FROM gallery_runs WHERE group_id=? ORDER BY id DESC LIMIT 12", (group_id,)):
                item = self._metadata(work)
                content = work["content"]
                if work["status"] == "success" and work["kind"] in {"html", "text", "image"} and content:
                    preview = content[:2000] if work["kind"] == "text" else content
                    length = len(preview.encode("utf-8"))
                    if length <= 128 * 1024 and preview_bytes + length <= MAX_PREVIEW_BYTES:
                        item["preview"] = preview
                        preview_bytes += length
                works.append(item)
            return {"group": group, "stats": self._stats(group_id, now), "works": works}

    def groups(self):
        with self._lock:
            summaries = []
            now = self._now()
            shared = self._comparison_schedule()
            for row in self._db.execute("SELECT * FROM gallery_jobs"):
                cfg = json.loads(row["config"])
                latest = self._db.execute(f"SELECT {METADATA_COLUMNS} FROM gallery_runs WHERE group_id=? ORDER BY id DESC LIMIT 1", (row["group_id"],)).fetchone()
                # Catalog polling must not load stored artwork bodies.
                comparison_fields = self._comparison_fields(row["group_id"], shared)
                summaries.append(cfg | comparison_fields | {"group_id": row["group_id"], "next_at": datetime.fromtimestamp(row["next_at"], timezone.utc).isoformat() if cfg["enabled"] and not comparison_fields["comparison_member"] else None,
                                       "running": row["running_id"] is not None, "last_error": row["last_error"],
                                       "stats": self._stats(row["group_id"], now), "latest_work": self._metadata(latest) if latest else None})
        return sorted(summaries, key=lambda item: (item["name"].casefold(), item["group_id"]))

    def work(self, group_id, work_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM gallery_runs WHERE group_id=? AND id=?", (_id(group_id), _id(work_id))).fetchone()
            if row is None:
                raise GalleryError("找不到该分组的作品", 404)
            return self._metadata(row) | {"content": row["content"], "prompt": row["prompt"]}

    def feed(self, allowed=None):
        """One local snapshot, latest per participant first, without credentials."""
        if allowed is not None:
            allowed = {value for value in allowed if type(value) is int and 0 < value < 2**63}
            if not allowed:
                return {"items": [], "next_at": None}
        with self._lock:
            # A read savepoint also keeps separate SQLite connections from
            # changing the visible round halfway through this response.
            self._db.execute("SAVEPOINT gallery_feed")
            try:
                return self._feed_snapshot(allowed, self._now())
            finally:
                self._db.execute("RELEASE gallery_feed")

    def _feed_snapshot(self, allowed, now):
        ids = {row[0] for row in self._db.execute("SELECT group_id FROM gallery_jobs")}
        if allowed is not None:
            ids.intersection_update(allowed)
        if not ids:
            return {"items": [], "next_at": None}
        placeholders = ",".join("?" for _ in ids)
        jobs = {row["group_id"]: (json.loads(row["config"]), row["next_at"]) for row in self._db.execute(
            f"SELECT group_id,config,next_at FROM gallery_jobs WHERE group_id IN ({placeholders})", tuple(ids))}
        # Rank IDs before projecting metadata, so old prompts and large artwork
        # bodies never cross SQLite/Python just to select twelve recent rows.
        rows = self._db.execute(f"""WITH recent AS (
            SELECT id AS work_id,ROW_NUMBER() OVER(PARTITION BY group_id ORDER BY id DESC) AS position
            FROM gallery_runs WHERE group_id IN ({placeholders}))
            SELECT {METADATA_COLUMNS} FROM gallery_runs JOIN recent ON id=recent.work_id WHERE recent.position<=12""", tuple(ids)).fetchall()
        current = self._db.execute("SELECT id,items,created_at FROM gallery_rounds ORDER BY id DESC LIMIT 1").fetchone()
        round_items = [item for item in json.loads(current["items"]) if item["group_id"] in ids] if current else []
        by_work = {item["work_id"]: item for item in round_items if item.get("work_id") is not None}
        candidates, seen = [], set()
        for row in rows:
            shared = by_work.get(row["id"], {}) if current and row["round_id"] == current["id"] else {}
            item = {"group_id": row["group_id"], "name": shared.get("name", jobs[row["group_id"]][0]["name"]),
                    "model": row["model"], "reasoning_effort": row["reasoning_effort"], "status": row["status"],
                    "created_at": row["created_at"], "round_id": row["round_id"], "work": self._metadata(row)}
            if shared.get("status") == "interrupted" and row["error"] == INTERRUPTED:
                item["status"] = "interrupted"
            candidates.append(item)
            seen.add(row["id"])
        for item in round_items:
            if item.get("work_id") in seen or item["status"] == "success":
                continue
            candidates.append({"group_id": item["group_id"], "name": item["name"], "model": item["model"],
                               "reasoning_effort": item.get("reasoning_effort", item.get("config", {}).get("reasoning_effort", "default")),
                               "status": item["status"], "created_at": current["created_at"], "round_id": current["id"]})
        candidates.sort(key=lambda item: (item["created_at"], "work" not in item, item.get("work", {}).get("id", 0), item["group_id"]), reverse=True)
        latest, history_by_group, latest_model, participants = [], {}, {}, set()
        for item in candidates:
            gid = item["group_id"]
            if gid in participants:
                bucket = history_by_group.setdefault(gid, [])
                work = item.get("work")
                if len(bucket) < 4 and isinstance(work, dict) and work.get("model") == latest_model[gid]:
                    bucket.append(work)
            else:
                latest.append(item)
                latest_model[gid] = item.get("model")
                participants.add(gid)
        benchmark_map = self.store.benchmark_summaries(ids)
        for item in latest:
            item["history"] = history_by_group.get(item["group_id"], [])
            item["benchmark"] = _benchmark_verdict(
                benchmark_map.get((item["group_id"], item.get("model", ""))),
                item.get("status"),
            )
        latest.sort(key=lambda item: VERDICT_ORDER.get(item["benchmark"]["verdict"], 3))
        preview_ids, budget = [], MAX_PREVIEW_BYTES
        preview_targets = [item.get("work") for item in latest]
        for item in latest:
            preview_targets.extend(item["history"])
        for work in preview_targets:
            if not work or work["status"] != "success" or work["kind"] not in {"html", "text", "image"}:
                continue
            # SQLite substr counts characters; four bytes per codepoint bounds
            # the existing 2000-character text preview without loading it yet.
            size = min(work["content_bytes"], 8000) if work["kind"] == "text" else work["content_bytes"]
            if 0 < size <= min(128 * 1024, budget):
                preview_ids.append(work["id"])
                budget -= size
        if preview_ids:
            marks = ",".join("?" for _ in preview_ids)
            previews = {row["id"]: row["preview"] for row in self._db.execute(
                f"SELECT id,CASE WHEN kind='text' THEN substr(content,1,2000) ELSE content END AS preview FROM gallery_runs WHERE id IN ({marks})", preview_ids)}
            for work in preview_targets:
                if work and work["id"] in previews:
                    work["preview"] = previews[work["id"]]
        items = latest
        deadlines = []
        if self.store.get_setting("modules", {}).get("benchmark") is True:
            shared = self._comparison_schedule()
            members = set(shared["group_ids"]) if shared else set()
            if shared and shared["enabled"] and ids.intersection(members) and shared["next_at"] > now.timestamp():
                deadlines.append(shared["next_at"])
            deadlines.extend(deadline for group_id, (cfg, deadline) in jobs.items() if cfg["enabled"] and group_id not in members and deadline > now.timestamp())
        return {"items": items, "next_at": datetime.fromtimestamp(min(deadlines), timezone.utc).isoformat() if deadlines else None}

    def _record_stat(self, group_id, now, status):
        second = int(now.timestamp())
        bucket, offset = second // 60 * 60, str(second % 60)
        previous = self._db.execute("SELECT counts FROM gallery_stats WHERE group_id=? AND bucket=?", (group_id, bucket)).fetchone()
        counts = json.loads(previous[0]) if previous else {}
        pair = counts.setdefault(offset, [0, 0])
        pair[0 if status == "success" else 1] += 1
        self._db.execute("INSERT INTO gallery_stats(group_id,bucket,counts) VALUES (?,?,?) ON CONFLICT(group_id,bucket) DO UPDATE SET counts=excluded.counts",
                         (group_id, bucket, json.dumps(counts, separators=(",", ":"))))

    def _prune(self, now):
        self._db.execute("DELETE FROM gallery_stats WHERE bucket<?", ((int(now.timestamp()) - 24 * 3600) // 60 * 60,))
        latest_round = self._db.execute("SELECT COALESCE(MAX(id),0) FROM gallery_rounds").fetchone()[0]
        for row in self._db.execute("SELECT group_id FROM gallery_jobs").fetchall():
            protected = self._db.execute("SELECT COUNT(*) FROM gallery_runs WHERE group_id=? AND (status='running' OR round_id=?)", (row[0], latest_round)).fetchone()[0]
            remaining = max(0, MAX_WORKS_PER_GROUP - protected)
            self._db.execute("DELETE FROM gallery_runs WHERE group_id=? AND status!='running' AND COALESCE(round_id,-1)!=? AND id NOT IN (SELECT id FROM gallery_runs WHERE group_id=? AND status!='running' AND COALESCE(round_id,-1)!=? ORDER BY id DESC LIMIT ?)",
                             (row[0], latest_round, row[0], latest_round, remaining))
        used = self._db.execute("SELECT COALESCE(SUM(storage_bytes),0) FROM gallery_runs").fetchone()[0]
        if used > MAX_HISTORY_BYTES:
            for row in self._db.execute("SELECT id,storage_bytes FROM gallery_runs WHERE status!='running' AND COALESCE(round_id,-1)!=? ORDER BY id", (latest_round,)).fetchall():
                self._db.execute("DELETE FROM gallery_runs WHERE id=?", (row["id"],))
                used -= row["storage_bytes"]
                if used <= MAX_HISTORY_BYTES:
                    break

    def _finish(self, group_id, work_id, now, *, status, result=None, error=None, latency_ms=0):
        row = self._db.execute("SELECT * FROM gallery_runs WHERE id=? AND group_id=? AND status='running'", (work_id, group_id)).fetchone()
        if row is None:
            return
        result = result or {}
        content = result.get("content")
        size = len(row["prompt"].encode("utf-8")) + (len(content.encode("utf-8")) if content else 0)
        self._db.execute("UPDATE gallery_runs SET status=?,kind=?,mime=?,content=?,error=?,finished_at=?,latency_ms=?,storage_bytes=?,title=? WHERE id=? AND status='running'",
                         (status, result.get("kind"), result.get("mime"), content, error, now.isoformat(), latency_ms, size, result.get("title") or row["title"], work_id))
        self._record_stat(group_id, now, status)
        job = self._job(group_id)
        cfg = json.loads(job["config"])
        next_at = job["next_at"]
        if next_at <= now.timestamp():
            next_at = (now + timedelta(minutes=cfg["interval_minutes"])).timestamp()
        self._db.execute("UPDATE gallery_jobs SET running_id=NULL,last_error=?,next_at=? WHERE group_id=? AND running_id=?", (error, next_at, group_id, work_id))
        self._prune(now)

    def _begin_work(self, group_id, cfg, now, round_id=None):
        self._prune(now)
        title = cfg["name"] + " · 作品"
        cur = self._db.execute("INSERT INTO gallery_runs(group_id,round_id,title,model,reasoning_effort,prompt,created_at,status,storage_bytes) VALUES (?,?,?,?,?,?,?,'running',?)",
                               (group_id, round_id, title, cfg["model"], cfg.get("reasoning_effort", "default"), cfg["prompt"], now.isoformat(), len(cfg["prompt"].encode("utf-8"))))
        work_id = cur.lastrowid
        self._db.execute("UPDATE gallery_runs SET title=? WHERE id=?", (f"{title} #{work_id}", work_id))
        self._db.execute("UPDATE gallery_jobs SET running_id=?,last_error=NULL,next_at=? WHERE group_id=?", (work_id, (now + timedelta(minutes=cfg["interval_minutes"])).timestamp(), group_id))
        self._prune(now)
        return work_id

    async def start(self, group_id, now=None):
        async with self._start_lock:
            if self._closed:
                raise GalleryError("生成服务正在停止", 503)
            group_id, now = _id(group_id), self._now(now)
            with self._lock, self._db:
                if group_id in self._comparison_members():
                    raise GalleryError("该分组参与共享 Prompt 对比，请从对比面板生成整轮作品", 409)
                row = self._job(group_id)
                cfg = json.loads(row["config"])
                self._ready(cfg)
                if self._tasks or self._db.execute("SELECT 1 FROM gallery_runs WHERE status='running' LIMIT 1").fetchone():
                    raise GalleryError("已有作品正在生成，请稍后再试", 409)
                work_id = self._begin_work(group_id, cfg, now)
            task = asyncio.create_task(self._generate(group_id, work_id, cfg | {"group_id": group_id, "work_id": work_id}))
            self._tasks[group_id] = task
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            return {key: value for key, value in self.work(group_id, work_id).items() if key not in {"content", "prompt"}}

    async def _generate(self, group_id, work_id, job):
        started = time.monotonic()
        try:
            result = await self.generate(dict(job))
            if (not isinstance(result, dict) or result.get("kind") not in {"html", "text", "image"}
                    or not isinstance(result.get("content"), str) or not result["content"].strip()
                    or len(result["content"].encode("utf-8")) > CONTENT_LIMITS[result["kind"]]
                    or (result.get("mime") is not None and (not isinstance(result["mime"], str) or len(result["mime"]) > 100))):
                raise ValueError("Invalid generated artwork")
            title = result.get("title")
            if not isinstance(title, str) or not title.strip() or len(title) > 100 or any(ord(char) < 32 for char in title):
                title = None
            latency = result.get("latency_ms")
            if type(latency) is not int or not 0 <= latency <= 24 * 3600 * 1000:
                latency = max(0, round((time.monotonic() - started) * 1000))
            result = {key: result.get(key) for key in ("kind", "content", "mime")} | {"title": title}
            with self._lock, self._db:
                self._finish(group_id, work_id, self._now(), status="success", result=result, latency_ms=latency)
        except asyncio.CancelledError:
            with self._lock, self._db:
                self._finish(group_id, work_id, self._now(), status="failed", error=INTERRUPTED, latency_ms=max(0, round((time.monotonic() - started) * 1000)))
            raise
        except Exception as exc:
            with self._lock, self._db:
                error = str(exc) if isinstance(exc, UnsupportedReasoningEffort) else FAILED
                self._finish(group_id, work_id, self._now(), status="failed", error=error, latency_ms=max(0, round((time.monotonic() - started) * 1000)))
        finally:
            self._tasks.pop(group_id, None)

    def _comparison_members(self):
        row = self._db.execute("SELECT config FROM gallery_comparison WHERE id=1").fetchone()
        return set(json.loads(row[0])["group_ids"]) if row else set()

    def _comparison_schedule(self):
        row = self._db.execute("SELECT * FROM gallery_comparison WHERE id=1").fetchone()
        return json.loads(row["config"]) | {"next_at": row["next_at"]} if row else None

    @staticmethod
    def _comparison_fields(group_id, shared):
        member = bool(shared and group_id in shared["group_ids"])
        enabled = bool(member and shared["enabled"])
        return {"comparison_member": member, "comparison_enabled": enabled,
                "comparison_next_at": datetime.fromtimestamp(shared["next_at"], timezone.utc).isoformat() if enabled else None}

    def configure_comparison(self, data):
        if self._closed:
            raise GalleryError("生成服务正在停止", 503)
        if not isinstance(data, dict) or set(data) - {"group_ids", "prompt", "enabled", "interval_minutes"}:
            raise GalleryError("共享 Prompt 配置包含不支持的字段")
        now = self._now()
        with self._lock, self._db:
            previous = self._db.execute("SELECT config FROM gallery_comparison WHERE id=1").fetchone()
            cfg = {"group_ids": [], "prompt": DEFAULT_PROMPT, "enabled": False, "interval_minutes": 30} | (json.loads(previous[0]) if previous else {}) | data
            ids = cfg["group_ids"]
            if not isinstance(ids, list) or not 2 <= len(ids) <= MAX_GROUPS:
                raise GalleryError("请选择 2 至 32 个已配置的分组")
            for group_id in ids:
                _id(group_id)
            if len(set(ids)) != len(ids):
                raise GalleryError("对比分组不能重复")
            if not isinstance(cfg["prompt"], str) or not cfg["prompt"].strip() or len(cfg["prompt"]) > 8000:
                raise GalleryError("共享 Prompt 应为 1 至 8000 个字符")
            cfg["prompt"] = cfg["prompt"].strip()
            if type(cfg["enabled"]) is not bool or type(cfg["interval_minutes"]) is not int or not 5 <= cfg["interval_minutes"] <= 1440:
                raise GalleryError("自动生成开关或间隔无效")
            for group_id in ids:
                group_cfg = json.loads(self._job(group_id)["config"])
                if cfg["enabled"]:
                    self._ready(group_cfg)
            previous_ids = set(json.loads(previous[0])["group_ids"]) if previous else set()
            self._db.execute("INSERT INTO gallery_comparison(id,config,next_at) VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config,next_at=excluded.next_at",
                             (json.dumps(cfg), (now + timedelta(minutes=cfg["interval_minutes"])).timestamp()))
            self._rearm_groups(previous_ids | set(ids), now)
        return self.comparison()

    def pause_comparison(self):
        with self._lock, self._db:
            row = self._db.execute("SELECT config FROM gallery_comparison WHERE id=1").fetchone()
            if row:
                cfg = json.loads(row[0]) | {"enabled": False}
                self._db.execute("UPDATE gallery_comparison SET config=?,next_at=? WHERE id=1", (json.dumps(cfg), (self._now() + timedelta(minutes=cfg["interval_minutes"])).timestamp()))
        return self.comparison()

    def clear_comparison(self):
        with self._lock, self._db:
            former = self._comparison_members()
            self._db.execute("DELETE FROM gallery_comparison WHERE id=1")
            self._rearm_groups(former, self._now())
        return self.comparison()

    def _rearm_groups(self, group_ids, now):
        for group_id in group_ids:
            row = self._db.execute("SELECT config FROM gallery_jobs WHERE group_id=?", (group_id,)).fetchone()
            if row:
                cfg = json.loads(row[0])
                self._db.execute("UPDATE gallery_jobs SET next_at=? WHERE group_id=?", ((now + timedelta(minutes=cfg["interval_minutes"])).timestamp(), group_id))

    def comparison(self):
        with self._lock:
            config_row = self._db.execute("SELECT * FROM gallery_comparison WHERE id=1").fetchone()
            round_row = self._db.execute("SELECT * FROM gallery_rounds ORDER BY id DESC LIMIT 1").fetchone()
            config = None
            if config_row:
                cfg = json.loads(config_row["config"])
                config = cfg | {"next_at": datetime.fromtimestamp(config_row["next_at"], timezone.utc).isoformat() if cfg["enabled"] else None,
                                "running": bool(round_row and round_row["status"] == "running")}
            current = None
            if round_row:
                current = {key: round_row[key] for key in ("id", "prompt", "status", "created_at", "finished_at")}
                current["items"], preview_bytes = [], 0
                for item in json.loads(round_row["items"]):
                    public = {key: item[key] for key in ("group_id", "name", "model", "status")}
                    public["reasoning_effort"] = item.get("reasoning_effort", item.get("config", {}).get("reasoning_effort", "default"))
                    work = self._db.execute(f"SELECT {PREVIEW_COLUMNS} FROM gallery_runs WHERE id=? AND group_id=? AND round_id=?", (item.get("work_id"), item["group_id"], round_row["id"])).fetchone()
                    if work:
                        public["work"] = self._metadata(work)
                        content = work["content"]
                        if work["status"] == "success" and content:
                            preview = content[:2000] if work["kind"] == "text" else content
                            length = len(preview.encode("utf-8"))
                            if length <= 128 * 1024 and preview_bytes + length <= MAX_PREVIEW_BYTES:
                                public["work"]["preview"] = preview
                                preview_bytes += length
                    current["items"].append(public)
            return {"config": config, "round": current}

    async def start_comparison(self, now=None):
        async with self._start_lock:
            if self._closed:
                raise GalleryError("生成服务正在停止", 503)
            now = self._now(now)
            with self._lock, self._db:
                row = self._db.execute("SELECT * FROM gallery_comparison WHERE id=1").fetchone()
                if row is None:
                    raise GalleryError("请先保存共享 Prompt 与对比分组", 404)
                if self._tasks or self._db.execute("SELECT 1 FROM gallery_runs WHERE status='running'").fetchone() or self._db.execute("SELECT 1 FROM gallery_rounds WHERE status='running'").fetchone():
                    raise GalleryError("已有作品或对比轮次正在生成，请稍后再试", 409)
                cfg, items = json.loads(row["config"]), []
                for group_id in cfg["group_ids"]:
                    snapshot = json.loads(self._job(group_id)["config"])
                    self._ready(snapshot)
                    snapshot.pop("prompt", None)
                    items.append({"group_id": group_id, "name": snapshot["name"], "model": snapshot["model"], "reasoning_effort": snapshot.get("reasoning_effort", "default"), "status": "queued", "config": snapshot})
                self._db.execute("DELETE FROM gallery_rounds")
                cur = self._db.execute("INSERT INTO gallery_rounds(prompt,items,status,created_at) VALUES (?,?,'running',?)", (cfg["prompt"], json.dumps(items), now.isoformat()))
                round_id = cur.lastrowid
                self._db.execute("UPDATE gallery_comparison SET next_at=? WHERE id=1", ((now + timedelta(minutes=cfg["interval_minutes"])).timestamp(),))
                self._rearm_groups(cfg["group_ids"], now)
            task = asyncio.create_task(self._generate_comparison(round_id))
            self._tasks["comparison"] = task
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            return self.comparison()

    def _settle_comparison(self, now, *, interrupt):
        row = self._db.execute("SELECT * FROM gallery_rounds WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return
        items = json.loads(row["items"])
        for item in items:
            work = self._db.execute("SELECT * FROM gallery_runs WHERE id=? AND round_id=?", (item.get("work_id"), row["id"])).fetchone()
            if work and work["status"] == "running" and interrupt:
                self._finish(item["group_id"], work["id"], now, status="failed", error=INTERRUPTED)
                item["status"] = "interrupted"
            elif work and work["status"] != "running":
                item["status"] = "interrupted" if work["error"] == INTERRUPTED else work["status"]
            elif interrupt and item["status"] in {"queued", "running"}:
                item["status"] = "interrupted"
        statuses = [item["status"] for item in items]
        if "interrupted" in statuses:
            status = "interrupted"
        elif all(value == "success" for value in statuses):
            status = "success"
        elif all(value == "failed" for value in statuses):
            status = "failed"
        elif any(value in {"queued", "running"} for value in statuses):
            status = "running"
        else:
            status = "partial"
        self._db.execute("UPDATE gallery_rounds SET items=?,status=?,finished_at=? WHERE id=?", (json.dumps(items), status, None if status == "running" else now.isoformat(), row["id"]))
        if status != "running":
            self._rearm_groups([item["group_id"] for item in items], now)
            cfg = self._db.execute("SELECT * FROM gallery_comparison WHERE id=1").fetchone()
            if cfg and (cfg["next_at"] <= now.timestamp() or status == "interrupted"):
                minutes = json.loads(cfg["config"])["interval_minutes"]
                self._db.execute("UPDATE gallery_comparison SET next_at=? WHERE id=1", ((now + timedelta(minutes=minutes)).timestamp(),))

    async def _generate_comparison(self, round_id):
        try:
            with self._lock:
                row = self._db.execute("SELECT items FROM gallery_rounds WHERE id=?", (round_id,)).fetchone()
            for index in range(len(json.loads(row[0]))):
                with self._lock, self._db:
                    row = self._db.execute("SELECT * FROM gallery_rounds WHERE id=? AND status='running'", (round_id,)).fetchone()
                    if row is None or self._closed:
                        break
                    items = json.loads(row["items"])
                    item = items[index]
                    cfg = item["config"] | {"prompt": row["prompt"]}
                    work_id = self._begin_work(item["group_id"], cfg, self._now(), round_id)
                    item.update(status="running", work_id=work_id)
                    self._db.execute("UPDATE gallery_rounds SET items=? WHERE id=?", (json.dumps(items), round_id))
                await self._generate(item["group_id"], work_id, cfg | {"group_id": item["group_id"], "work_id": work_id, "round_id": round_id})
                with self._lock, self._db:
                    work = self._db.execute("SELECT status,error FROM gallery_runs WHERE id=?", (work_id,)).fetchone()
                    item["status"] = "interrupted" if work and work["error"] == INTERRUPTED else work["status"] if work else "failed"
                    self._db.execute("UPDATE gallery_rounds SET items=? WHERE id=?", (json.dumps(items), round_id))
        finally:
            try:
                with self._lock, self._db:
                    self._settle_comparison(self._now(), interrupt=True)
            finally:
                self._tasks.pop("comparison", None)

    async def tick(self, now=None):
        now = self._now(now)
        if self._closed or self._tasks:
            return
        module = self.store.get_setting("modules", {}).get("benchmark") is True
        with self._lock, self._db:
            members = self._comparison_members()
            shared_due = False
            shared = self._db.execute("SELECT * FROM gallery_comparison WHERE id=1").fetchone()
            if shared and shared["next_at"] <= now.timestamp():
                cfg = json.loads(shared["config"])
                shared_minutes = cfg["interval_minutes"]
                shared_due = module and cfg["enabled"] and now.timestamp() - shared["next_at"] < cfg["interval_minutes"] * 60
                if not shared_due:
                    self._db.execute("UPDATE gallery_comparison SET next_at=? WHERE id=1", ((now + timedelta(minutes=cfg["interval_minutes"])).timestamp(),))
            rows = self._db.execute("SELECT * FROM gallery_jobs WHERE next_at<=? ORDER BY next_at,group_id", (now.timestamp(),)).fetchall()
            due = []
            for row in rows:
                cfg = json.loads(row["config"])
                if row["group_id"] in members or not module or not cfg["enabled"] or now.timestamp() - row["next_at"] >= cfg["interval_minutes"] * 60:
                    self._db.execute("UPDATE gallery_jobs SET next_at=? WHERE group_id=?", ((now + timedelta(minutes=cfg["interval_minutes"])).timestamp(), row["group_id"]))
                elif cfg["enabled"]:
                    due.append(row["group_id"])
            self._prune(now)
        if shared_due:
            try:
                await self.start_comparison(now=now)
            except GalleryError:
                with self._lock, self._db:
                    self._db.execute("UPDATE gallery_comparison SET next_at=? WHERE id=1", ((now + timedelta(minutes=shared_minutes)).timestamp(),))
            return
        if due:
            try:
                await self.start(due[0], now=now)
            except GalleryError:
                pass

    async def run(self):
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:
                # No failed scheduler tick is turned into an immediate paid retry.
                self.store.audit("gallery.scheduler", "gallery", "error", "生成调度暂时失败")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except TimeoutError:
                pass

    async def close(self):
        self._closed = True
        self._stop.set()
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # A newly created task can be cancelled before its coroutine enters.
        with self._lock, self._db:
            for row in self._db.execute("SELECT id,group_id FROM gallery_runs WHERE status='running'").fetchall():
                self._finish(row["group_id"], row["id"], self._now(), status="failed", error=INTERRUPTED)
            self._settle_comparison(self._now(), interrupt=True)
        self._tasks.clear()
