"""Small durable SQLite store. Calls are protected across HTTP and scheduler threads."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class Store:
    kinds = frozenset(("schedules", "rules", "automations"))

    def __init__(self, path):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS objects (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                  action TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ledger (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    def _kind(self, kind):
        if kind not in self.kinds:
            raise ValueError("Unknown object kind")

    def list(self, kind):
        self._kind(kind)
        with self._lock:
            return [json.loads(r["value"]) | {"id": r["id"]} for r in self._db.execute(
                "SELECT id,value FROM objects WHERE kind=? ORDER BY id", (kind,))]

    def get(self, kind, object_id):
        self._kind(kind)
        with self._lock:
            row = self._db.execute("SELECT id,value FROM objects WHERE kind=? AND id=?", (kind, object_id)).fetchone()
            return json.loads(row["value"]) | {"id": row["id"]} if row else None

    def create(self, kind, value):
        self._kind(kind)
        data = {k: v for k, v in value.items() if k != "id"}
        data["created_at"] = data["updated_at"] = utcnow()
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO objects(kind,value) VALUES (?,?)", (kind, json.dumps(data)))
            return data | {"id": cur.lastrowid}

    def update(self, kind, object_id, value):
        self._kind(kind)
        with self._lock, self._db:
            old = self.get(kind, object_id)
            if old is None:
                return None
            data = {k: v for k, v in (old | value).items() if k != "id"}
            data["created_at"] = old["created_at"]
            data["updated_at"] = utcnow()
            self._db.execute("UPDATE objects SET value=? WHERE kind=? AND id=?", (json.dumps(data), kind, object_id))
            return data | {"id": object_id}

    def delete(self, kind, object_id):
        self._kind(kind)
        with self._lock, self._db:
            return self._db.execute("DELETE FROM objects WHERE kind=? AND id=?", (kind, object_id)).rowcount > 0

    def get_setting(self, key, default=None):
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row["value"]) if row else default

    def set_setting(self, key, value):
        with self._lock, self._db:
            self._db.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def audit(self, action, target, status="ok", detail=""):
        with self._lock, self._db:
            self._db.execute("INSERT INTO audit(at,action,target,status,detail) VALUES (?,?,?,?,?)", (utcnow(), action, str(target), status, str(detail)[:2000]))
            # Keep local history bounded without affecting operation idempotency.
            self._db.execute("DELETE FROM audit WHERE id <= (SELECT COALESCE(MAX(id),0)-10000 FROM audit)")

    def audits(self, limit=100):
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (min(max(int(limit), 1), 1000),))]

    def ledger_get(self, key):
        with self._lock:
            row = self._db.execute("SELECT value FROM ledger WHERE key=?", (key,)).fetchone()
            return json.loads(row["value"]) if row else None

    def ledger_set(self, key, value):
        with self._lock, self._db:
            self._db.execute("INSERT INTO ledger(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def ledger_list(self, prefix):
        """Read an exact prefix without treating '%' or '_' as SQL wildcards."""
        with self._lock:
            return {row["key"]: json.loads(row["value"]) for row in self._db.execute(
                "SELECT key,value FROM ledger WHERE substr(key,1,?)=? ORDER BY key", (len(prefix), prefix))}

    def close(self):
        with self._lock:
            self._db.close()
