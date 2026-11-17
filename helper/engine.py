"""Restart-safe daily scheduling and conservative inactive-user maintenance."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
from zoneinfo import ZoneInfo


def in_window(schedule, now):
    """Opening day owns overnight hours; start included, end excluded.

    weekdays uses Python's Monday=0 convention. Friday 23:00-Saturday 09:00
    belongs to Friday even when Saturday is absent from weekdays.
    """
    if now.tzinfo is None:
        raise ValueError("A timezone-aware current time is required")
    local = now.astimezone(ZoneInfo(schedule.get("timezone", "Asia/Shanghai")))
    start = datetime.strptime(schedule["start"], "%H:%M").time()
    end = datetime.strptime(schedule["end"], "%H:%M").time()
    weekdays = set(schedule.get("weekdays", range(7)))
    clock = local.time().replace(tzinfo=None)
    if start == end:
        raise ValueError("Schedule start and end must differ")
    if start < end:
        return local.weekday() in weekdays and start <= clock < end
    if clock >= start:
        return local.weekday() in weekdays
    if clock < end:
        return (local - timedelta(days=1)).weekday() in weekdays
    return False


def parse_time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def eligible_user(user, rule, now):
    """Only confirmed model usage timestamps may trigger automatic maintenance.

    Null last_used_at is ambiguous upstream (never used OR usage lookup failed).
    Neither creation time nor panel-login activity safely establishes inactivity.
    """
    used = parse_time(user.get("last_used_at"))
    if user.get("status") != "active" or user.get("role") != "user" or used is None:
        return None
    days = int(rule.get("days", rule.get("inactivity_days", 30)))
    if days < 1 or used > now or now - used < timedelta(days=days):
        return None
    return used.isoformat()


def render(template, user, rule):
    values = {
        "username": user.get("username") or user.get("email", ""),
        "email": user.get("email", ""),
        "days": str(rule.get("days", rule.get("inactivity_days", 30))),
        "last_used_at": user.get("last_used_at", ""),
    }
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


class Engine:
    def __init__(self, store, upstream, mailer, interval=30):
        from .automation import AutomationEngine
        self.store = store
        self.upstream = upstream
        self.mailer = mailer
        self.automation = AutomationEngine(store, upstream)
        self.interval = interval
        self._stop = asyncio.Event()
        self._tick_lock = asyncio.Lock()
        self.status = {"running": False, "last_tick_at": None, "last_error": None}

    def stop(self):
        self._stop.set()

    async def run(self):
        self.status["running"] = True
        try:
            while not self._stop.is_set():
                try:
                    await self.tick()
                except Exception as exc:
                    self.status["last_error"] = type(exc).__name__
                    self.store.audit("engine", "tick", "error", type(exc).__name__)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
                except TimeoutError:
                    pass
        finally:
            self.status["running"] = False

    async def tick(self, now=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("A timezone-aware current time is required")
        now = now.astimezone(timezone.utc)
        async with self._tick_lock:
            self.status["last_error"] = None
            for phase in (self._schedules, self._rules, self._automations):
                try:
                    await phase(now)
                except Exception as exc:
                    self.status["last_error"] = type(exc).__name__
                    self.store.audit(phase.__name__.strip("_"), "upstream", "error", type(exc).__name__)
            self.status["last_tick_at"] = now.isoformat()
        return self.status.copy()

    async def _automations(self, now):
        reports = await self.automation.tick(now)
        if any(report.get("errors") for report in reports):
            self.status["last_error"] = "AutomationError: 请查看自动化规则执行记录"

    def _error(self, kind, item, now, error):
        detail = str(error) if isinstance(error, str) else type(error).__name__
        self.status["last_error"] = detail
        self.store.update(kind, item["id"], {"last_error": detail, "last_run_at": now.isoformat()})
        self.store.audit(kind, item["id"], "error", detail)

    async def _schedules(self, now):
        schedules = [s for s in self.store.list("schedules") if s.get("enabled", False)]
        if not schedules:
            return
        accounts = {int(a["id"]): a for a in await self.upstream.list_accounts()}
        # API enforces one enabled schedule per account; fail closed on corrupt data.
        owners = {}
        conflicts = set()
        for schedule in schedules:
            for account_id in schedule.get("account_ids", []):
                if account_id in owners:
                    conflicts.add(account_id)
                owners[account_id] = schedule["id"]
        for schedule in schedules:
            errors = []
            try:
                desired = in_window(schedule, now)
            except Exception as exc:
                self._error("schedules", schedule, now, exc)
                continue
            for account_id in schedule.get("account_ids", []):
                try:
                    if account_id in conflicts:
                        raise ValueError("Account has conflicting schedules")
                    account = accounts.get(account_id)
                    if account is None:
                        raise ValueError("Scheduled account was not returned by upstream")
                    if type(account.get("schedulable")) is not bool:
                        raise ValueError("Upstream schedulable field is missing or invalid")
                    if account["schedulable"] != desired:
                        await self.upstream.set_schedulable(account_id, desired)
                        accounts[account_id]["schedulable"] = desired
                        self.store.audit("schedule.set_schedulable", account_id, "ok", f"schedule={schedule['id']} schedulable={desired}")
                except Exception as exc:
                    errors.append(f"account {account_id}: {type(exc).__name__}")
            if errors:
                self._error("schedules", schedule, now, "; ".join(errors))
            else:
                self.store.update("schedules", schedule["id"], {"last_run_at": now.isoformat(), "last_error": None})

    async def _rules(self, now):
        rules = [r for r in self.store.list("rules") if r.get("enabled", False)]
        if not rules:
            return
        if not self.mailer.configured:
            for rule in rules:
                self._error("rules", rule, now, "SMTP is not configured; no users were changed")
            return
        users = await self.upstream.list_users()
        for rule in rules:
            errors = []
            for user in users:
                try:
                    activity = eligible_user(user, rule, now)
                    if activity is None:
                        continue
                    await self._maintain_user(rule, user, activity, now)
                except Exception as exc:
                    errors.append(f"user {user.get('id', '?')}: {type(exc).__name__}")
                    self.store.audit("rule.user", user.get("id", "?"), "error", f"rule={rule['id']} {type(exc).__name__}")
            if errors:
                self._error("rules", rule, now, "; ".join(errors))
            else:
                self.store.update("rules", rule["id"], {"last_run_at": now.isoformat(), "last_error": None})

    async def _maintain_user(self, rule, user, activity, now):
        user_id = int(user["id"])
        activity_hash = hashlib.sha256(activity.encode()).hexdigest()[:24]
        key = f"rule:{rule['id']}:user:{user_id}:usage:{activity_hash}"
        state = self.store.ledger_get(key) or {}
        if state.get("status") == "done":
            return
        if state.get("status") in ("sending", "needs_review"):
            # A process crash during SMTP is ambiguous, so neither resend nor disable.
            self.store.audit("rule.mail_review", user_id, "warning", f"rule={rule['id']} delivery needs manual review; no user change")
            raise RuntimeError("Prior mail delivery needs manual review")
        next_attempt = parse_time(state.get("next_attempt_at"))
        if next_attempt and now < next_attempt:
            return
        # Fresh upstream check protects against users active since the list snapshot.
        fresh = await self.upstream.get_user(user_id)
        if eligible_user(fresh, rule, now) != activity:
            return
        if not state.get("email_sent"):
            recipient = fresh.get("email", "")
            if not recipient:
                raise ValueError("User has no email address")
            self.store.ledger_set(key, {"status": "sending", "at": now.isoformat()})
            try:
                await self.mailer.send(
                    recipient,
                    render(rule.get("subject", "账号长期未使用提醒"), fresh, rule),
                    render(rule.get("body", "您好 {username}，您的账号已超过 {days} 天未使用，请联系管理员。"), fresh, rule),
                )
            except Exception as exc:
                retryable = getattr(exc, "retryable", False)
                self.store.ledger_set(key, {
                    "status": "mail_failed" if retryable else "needs_review",
                    "at": now.isoformat(),
                    "next_attempt_at": (now + timedelta(minutes=15)).isoformat() if retryable else None,
                })
                raise
            state = {"status": "sent", "email_sent": True, "at": now.isoformat()}
            self.store.ledger_set(key, state)
            self.store.audit("rule.email", user_id, "ok", f"rule={rule['id']}")
        if rule.get("disable_after_email", False):
            # Sending may take time. Re-read immediately before the destructive step.
            fresh = await self.upstream.get_user(user_id)
            if eligible_user(fresh, rule, now) != activity:
                self.store.ledger_set(key, state | {"status": "done", "disable_skipped": "usage or user status changed"})
                self.store.audit("rule.disable_skipped", user_id, "ok", f"rule={rule['id']} usage or status changed")
                return
            await self.upstream.set_user_status(user_id, "disabled")
            self.store.audit("rule.disable", user_id, "ok", f"rule={rule['id']}")
        self.store.ledger_set(key, state | {"status": "done", "finished_at": now.isoformat()})
