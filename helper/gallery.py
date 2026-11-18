"""Durable, opt-in model artwork generation, separate from account maintenance."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import time


MAX_GROUPS = 32
MAX_WORKS_PER_GROUP = 500
CONTENT_LIMITS = {"html": 128 * 1024, "text": 16 * 1024, "image": 3 * 1024 * 1024}
MAX_HISTORY_BYTES = 64 * 1024 * 1024
MAX_PREVIEW_BYTES = 1024 * 1024
FAILED = "生成失败，请检查模型、Key 权限或服务连接"
INTERRUPTED = "生成已中断，结果未确认，本次请求不会自动重试"
DEFAULT_PROMPT = "请生成一个精美、有创意、可交互的独立 HTML 或 SVG 作品。只输出完整代码，不使用外部资源。"
FIELDS = {"name", "model", "key_id", "api_format", "prompt", "enabled", "interval_minutes"}
METADATA_COLUMNS = "id,group_id,title,model,created_at,finished_at,latency_ms,status,kind,mime,error,storage_bytes-length(CAST(prompt AS BLOB)) AS content_bytes"


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
            """)
            now = self._now()
            interrupted = self._db.execute("SELECT * FROM gallery_runs WHERE status='running'").fetchall()
            for run in interrupted:
                self._finish(run["group_id"], run["id"], now, status="failed", error=INTERRUPTED)
            # Expired offline periods are skipped, never replayed as paid calls.
            for row in self._db.execute("SELECT * FROM gallery_jobs").fetchall():
                if row["next_at"] <= now.timestamp() or row["running_id"] is not None:
                    cfg = json.loads(row["config"])
                    self._db.execute("UPDATE gallery_jobs SET running_id=NULL,next_at=? WHERE group_id=?",
                                     ((now + timedelta(minutes=cfg["interval_minutes"])).timestamp(), row["group_id"]))
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
               "prompt": DEFAULT_PROMPT, "enabled": False, "interval_minutes": 30} | (previous or {}) | data
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
        return {key: row[key] for key in ("id", "group_id", "title", "model", "created_at", "finished_at", "latency_ms", "status", "kind", "mime", "error")} | {"content_bytes": content_bytes}

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
            group = cfg | {"group_id": group_id, "next_at": datetime.fromtimestamp(row["next_at"], timezone.utc).isoformat() if cfg["enabled"] else None,
                           "running": row["running_id"] is not None, "last_error": row["last_error"]}
            works, preview_bytes = [], 0
            for work in self._db.execute("SELECT * FROM gallery_runs WHERE group_id=? ORDER BY id DESC LIMIT 12", (group_id,)):
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
            for row in self._db.execute("SELECT * FROM gallery_jobs"):
                cfg = json.loads(row["config"])
                latest = self._db.execute(f"SELECT {METADATA_COLUMNS} FROM gallery_runs WHERE group_id=? ORDER BY id DESC LIMIT 1", (row["group_id"],)).fetchone()
                # Catalog polling must not load stored artwork bodies.
                summaries.append(cfg | {"group_id": row["group_id"], "next_at": datetime.fromtimestamp(row["next_at"], timezone.utc).isoformat() if cfg["enabled"] else None,
                                       "running": row["running_id"] is not None, "last_error": row["last_error"],
                                       "stats": self._stats(row["group_id"], now), "latest_work": self._metadata(latest) if latest else None})
        return sorted(summaries, key=lambda item: (item["name"].casefold(), item["group_id"]))

    def work(self, group_id, work_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM gallery_runs WHERE group_id=? AND id=?", (_id(group_id), _id(work_id))).fetchone()
            if row is None:
                raise GalleryError("找不到该分组的作品", 404)
            return self._metadata(row) | {"content": row["content"], "prompt": row["prompt"]}

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
        for row in self._db.execute("SELECT group_id FROM gallery_jobs").fetchall():
            self._db.execute("DELETE FROM gallery_runs WHERE group_id=? AND status!='running' AND id NOT IN (SELECT id FROM gallery_runs WHERE group_id=? ORDER BY id DESC LIMIT ?)",
                             (row[0], row[0], MAX_WORKS_PER_GROUP))
        used = self._db.execute("SELECT COALESCE(SUM(storage_bytes),0) FROM gallery_runs").fetchone()[0]
        if used > MAX_HISTORY_BYTES:
            for row in self._db.execute("SELECT id,storage_bytes FROM gallery_runs WHERE status!='running' ORDER BY id").fetchall():
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

    async def start(self, group_id, now=None):
        async with self._start_lock:
            if self._closed:
                raise GalleryError("生成服务正在停止", 503)
            group_id, now = _id(group_id), self._now(now)
            with self._lock, self._db:
                row = self._job(group_id)
                cfg = json.loads(row["config"])
                self._ready(cfg)
                if self._tasks or self._db.execute("SELECT 1 FROM gallery_runs WHERE status='running' LIMIT 1").fetchone():
                    raise GalleryError("已有作品正在生成，请稍后再试", 409)
                self._prune(now)
                title = cfg["name"] + " · 作品"
                cur = self._db.execute("INSERT INTO gallery_runs(group_id,title,model,prompt,created_at,status,storage_bytes) VALUES (?,?,?,?,?,'running',?)",
                                       (group_id, title, cfg["model"], cfg["prompt"], now.isoformat(), len(cfg["prompt"].encode("utf-8"))))
                work_id = cur.lastrowid
                self._db.execute("UPDATE gallery_runs SET title=? WHERE id=?", (f"{title} #{work_id}", work_id))
                self._db.execute("UPDATE gallery_jobs SET running_id=?,last_error=NULL,next_at=? WHERE group_id=?", (work_id, (now + timedelta(minutes=cfg["interval_minutes"])).timestamp(), group_id))
                self._prune(now)
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
        except Exception:
            with self._lock, self._db:
                self._finish(group_id, work_id, self._now(), status="failed", error=FAILED, latency_ms=max(0, round((time.monotonic() - started) * 1000)))
        finally:
            self._tasks.pop(group_id, None)

    async def tick(self, now=None):
        now = self._now(now)
        if self._closed or self._tasks:
            return
        module = self.store.get_setting("modules", {}).get("benchmark") is True
        with self._lock, self._db:
            rows = self._db.execute("SELECT * FROM gallery_jobs WHERE next_at<=? ORDER BY next_at,group_id", (now.timestamp(),)).fetchall()
            due = []
            for row in rows:
                cfg = json.loads(row["config"])
                if not module or not cfg["enabled"] or now.timestamp() - row["next_at"] >= cfg["interval_minutes"] * 60:
                    self._db.execute("UPDATE gallery_jobs SET next_at=? WHERE group_id=?", ((now + timedelta(minutes=cfg["interval_minutes"])).timestamp(), row["group_id"]))
                elif cfg["enabled"]:
                    due.append(row["group_id"])
            self._prune(now)
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
        self._tasks.clear()
