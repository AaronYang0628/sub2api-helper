"""Small row actions for the upstream admin UI.

The upstream JWT is validated by the application's ``native_admin`` dependency.
Only the server's bound management credential reaches the upstream admin API.
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, HTTPException, Path, Request
from pydantic import Field, field_validator, model_validator

from .engine import in_window
from .schemas import Email, Input, Schedule


BUILTIN_EMAIL_TEMPLATES = (
    {
        "id": "builtin:inactive",
        "name": "长期未使用提醒",
        "subject": "账户长期未使用提醒",
        "body": "您好 {username}，您的账户近期未使用（最近使用：{last_used_at}），请及时登录查看。如有疑问请联系管理员。",
        "builtin": True,
    },
    {
        "id": "builtin:maintenance",
        "name": "服务维护通知",
        "subject": "服务维护通知",
        "body": "您好 {username}，服务将进行维护，请留意管理员后续通知。",
        "builtin": True,
    },
)


class NativeSchedule(Input):
    mode: Literal["all_day", "window", "rule"]
    name: str | None = Field(default=None, min_length=1, max_length=100)
    schedule_id: int | None = Field(default=None, gt=0)
    remove_from_schedule_id: int | None = Field(default=None, gt=0)
    start: str = "23:00"
    end: str = "09:00"
    timezone: str = "Asia/Shanghai"
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("start", "end")
    @classmethod
    def valid_time(cls, value):
        return Schedule.valid_time(value)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        return Schedule.valid_zone(value)

    @model_validator(mode="after")
    def valid_window(self):
        # Reuse the scheduler's exact weekday and overnight-window validation.
        # all_day and rule have no inline time window, so equal start/end values are harmless.
        if self.mode == "window":
            if self.schedule_id is not None or self.remove_from_schedule_id is not None:
                raise ValueError("自定义时段模式不能指定规则 ID")
            Schedule(name=self.name or "native", account_ids=[1], enabled=True,
                     start=self.start, end=self.end, timezone=self.timezone, weekdays=self.weekdays)
        elif self.mode == "rule":
            if self.schedule_id is None:
                raise ValueError("请选择要应用的时段规则")
            if self.remove_from_schedule_id is not None:
                raise ValueError("应用规则模式不能同时指定移出规则 ID")
        else:
            if self.schedule_id is not None:
                raise ValueError("全天模式不能指定应用规则 ID")
            if any(day < 0 or day > 6 for day in self.weekdays) or len(set(self.weekdays)) != len(self.weekdays):
                raise ValueError("星期取值为 0 至 6，不能重复")
        return self


def install_native_api(app, store, upstream, mailer, engine, config_provider, native_admin, limit):
    """Install authenticated native row actions without creating default rules."""
    prefix = "/_helper/api"

    async def account(account_id):
        matches = [row for row in await upstream.list_accounts()
                   if isinstance(row, dict) and type(row.get("id")) is int and row["id"] == account_id]
        if not matches:
            raise HTTPException(404, "账号不存在或管理员 Key 无权访问")
        if len(matches) != 1 or type(matches[0].get("schedulable")) is not bool:
            raise HTTPException(502, "上游账号状态不完整，已停止操作")
        return matches[0]

    def ownership(account_id):
        owned, shared, conflicts = [], [], []
        for plan in store.list("schedules"):
            ids = plan.get("account_ids")
            touches = isinstance(ids, list) and account_id in ids
            claims = plan.get("owner") == "native-account" and plan.get("native_account_id") == account_id
            if not touches and not claims:
                continue
            valid = (plan.get("owner") == "native-account"
                     and type(plan.get("native_account_id")) is int
                     and plan["native_account_id"] == account_id and ids == [account_id]
                     and type(ids[0]) is int)
            if valid:
                owned.append(plan)
            elif claims or (touches and plan.get("owner") == "native-account"):
                conflicts.append("账号的原生定时计划数据异常，请先在高级设置中检查")
            elif plan.get("enabled"):
                shared.append(plan)
        if len(owned) > 1:
            conflicts.append("该账号存在多个原生定时计划，请先在高级设置中检查")
        if shared and (owned or conflicts or len(shared) > 1):
            conflicts.append("该账号已由多个定时计划管理，请先在规则页中检查")
            return owned[0] if len(owned) == 1 else None, next(iter(conflicts), None), None
        if len(shared) == 1:
            plan = shared[0]
            return None, f"该账号已由定时规则「{plan.get('name') or plan['id']}」管理，请先移出或在规则页调整", plan
        return owned[0] if len(owned) == 1 else None, next(iter(conflicts), None), None

    def available_schedules(account_id, now):
        items = []
        for plan in store.list("schedules"):
            if plan.get("owner") == "native-account":
                continue
            ids = plan.get("account_ids") if isinstance(plan.get("account_ids"), list) else []
            items.append({
                "id": plan["id"],
                "name": plan.get("name") or f"规则 #{plan['id']}",
                "start": plan.get("start", "23:00"),
                "end": plan.get("end", "09:00"),
                "timezone": plan.get("timezone", config_provider().timezone),
                "weekdays": list(plan.get("weekdays", list(range(7)))),
                "account_count": len(ids),
                "enabled": bool(plan.get("enabled")),
                "includes_account": account_id in ids,
                "in_window_now": in_window(plan, now),
            })
        return items

    def describe(row, plan, conflict, shared_plan=None, now=None):
        config = config_provider()
        now = now or datetime.now(timezone.utc)
        shared_info = None
        if shared_plan:
            ids = shared_plan.get("account_ids") if isinstance(shared_plan.get("account_ids"), list) else []
            shared_info = {
                "id": shared_plan["id"],
                "name": shared_plan.get("name") or f"规则 #{shared_plan['id']}",
                "start": shared_plan.get("start", "23:00"),
                "end": shared_plan.get("end", "09:00"),
                "timezone": shared_plan.get("timezone", config.timezone),
                "weekdays": list(shared_plan.get("weekdays", list(range(7)))),
                "account_count": len(ids),
                "in_window_now": in_window(shared_plan, now),
                "other_account_count": max(0, len([i for i in ids if i != row["id"]])),
            }
        active_plan = plan or shared_plan
        return {
            "account_id": row["id"], "account_name": row.get("name", ""),
            "schedulable": row["schedulable"],
            "mode": "rule" if shared_plan else ("window" if plan and plan.get("enabled") else "all_day"),
            "managed": active_plan is not None, "read_only": bool(conflict), "conflict": conflict,
            "name": (active_plan or {}).get("name") or f"账号 #{row['id']} 定时调度",
            "start": (active_plan or {}).get("start", "23:00"), "end": (active_plan or {}).get("end", "09:00"),
            "timezone": (active_plan or {}).get("timezone", config.timezone),
            "weekdays": (active_plan or {}).get("weekdays", list(range(7))),
            "last_error": (active_plan or {}).get("last_error"),
            "shared_schedule": shared_info,
            "available_schedules": available_schedules(row["id"], now),
        }

    async def user(user_id):
        row = await upstream.get_user(user_id)
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] != user_id:
            raise HTTPException(502, "上游用户身份响应不完整，已停止操作")
        return row

    @app.get(prefix + "/status")
    async def native_status(actor=Depends(native_admin)):
        return {"timezone": config_provider().timezone, "mail_configured": mailer.configured}

    @app.get(prefix + "/accounts/{account_id}/schedule")
    async def get_schedule(account_id: int = Path(gt=0), actor=Depends(native_admin)):
        async with engine._tick_lock:
            row = await account(account_id)
            plan, conflict, shared_plan = ownership(account_id)
            return describe(row, plan, conflict, shared_plan)

    @app.put(prefix + "/accounts/{account_id}/schedule")
    async def put_schedule(value: NativeSchedule, account_id: int = Path(gt=0), actor=Depends(native_admin)):
        async with engine._tick_lock:
            row = await account(account_id)
            plan, conflict, shared_plan = ownership(account_id)
            now = datetime.now(timezone.utc)
            if value.mode == "all_day" and value.remove_from_schedule_id is not None:
                if not shared_plan or shared_plan["id"] != value.remove_from_schedule_id:
                    raise HTTPException(409, conflict or "当前账号不属于指定的定时规则，已取消操作")
                try:
                    if not row["schedulable"]:
                        await upstream.set_schedulable(account_id, True)
                    row = await account(account_id)
                    if not row["schedulable"]:
                        raise ValueError("schedulable readback differs")
                except Exception:
                    store.audit("native.schedule", account_id, "error", f"actor={actor['id']} remove-from-rule state unconfirmed")
                    raise HTTPException(502, "全天调度状态未确认，原规则未修改；请刷新检查") from None
                remaining = [i for i in shared_plan["account_ids"] if i != account_id]
                if remaining:
                    store.update("schedules", shared_plan["id"], {"account_ids": remaining})
                else:
                    store.update("schedules", shared_plan["id"], {"account_ids": [], "enabled": False})
                store.audit("native.schedule", account_id, detail=f"actor={actor['id']} mode=all_day removed_from={shared_plan['id']}")
                return describe(row, None, None, None, now)
            if conflict:
                raise HTTPException(409, conflict)
            if value.mode == "rule":
                target = store.get("schedules", value.schedule_id)
                if not target or target.get("owner") == "native-account":
                    raise HTTPException(404, "找不到所选的时段规则")
                if not target.get("enabled"):
                    raise HTTPException(409, "所选规则当前未启用，请先在规则页确认并启用该规则")
                ids = list(target.get("account_ids") or [])
                if account_id not in ids:
                    ids.append(account_id)
                    target = store.update("schedules", target["id"], {"account_ids": ids})
                if plan:
                    store.delete("schedules", plan["id"])
                    plan = None
                desired = in_window(target, now)
                try:
                    if row["schedulable"] != desired:
                        await upstream.set_schedulable(account_id, desired)
                    row = await account(account_id)
                    if row["schedulable"] != desired:
                        raise ValueError("schedulable readback differs")
                except Exception as exc:
                    store.update("schedules", target["id"], {"last_error": type(exc).__name__, "last_run_at": now.isoformat()})
                    store.audit("native.schedule", account_id, "error", f"actor={actor['id']} rule immediate reconciliation failed")
                    raise HTTPException(502, "账号已加入所选定时规则，但即时调度状态未确认；请刷新检查执行结果") from None
                target = store.update("schedules", target["id"], {"last_error": None, "last_run_at": now.isoformat()})
                store.audit("native.schedule", account_id, detail=f"actor={actor['id']} mode=rule schedule={target['id']}")
                plan, conflict, shared_plan = ownership(account_id)
                return describe(row, plan, conflict, shared_plan, now)
            if value.mode == "window":
                resolved_name = value.name or (plan or {}).get("name") or f"账号 #{account_id} 定时调度"
                data = {
                    "start": value.start, "end": value.end, "timezone": value.timezone,
                    "weekdays": list(value.weekdays), "name": resolved_name,
                    "account_ids": [account_id], "owner": "native-account",
                    "native_account_id": account_id, "enabled": True,
                }
                plan = store.update("schedules", plan["id"], data) if plan else store.create("schedules", data)
                desired = in_window(plan, now)
                try:
                    if row["schedulable"] != desired:
                        await upstream.set_schedulable(account_id, desired)
                    row = await account(account_id)
                    if row["schedulable"] != desired:
                        raise ValueError("schedulable readback differs")
                except Exception as exc:
                    store.update("schedules", plan["id"], {"last_error": type(exc).__name__, "last_run_at": now.isoformat()})
                    store.audit("native.schedule", account_id, "error", f"actor={actor['id']} immediate reconciliation failed")
                    raise HTTPException(502, "定时计划已保存，但即时调度状态未确认；请刷新检查执行结果") from None
                plan = store.update("schedules", plan["id"], {"last_error": None, "last_run_at": now.isoformat()})
            else:
                # Reading an unmanaged account never enables it. Only this explicit
                # save can enable scheduling once and relinquish our own plan.
                try:
                    if not row["schedulable"]:
                        await upstream.set_schedulable(account_id, True)
                    row = await account(account_id)
                    if not row["schedulable"]:
                        raise ValueError("schedulable readback differs")
                except Exception:
                    store.audit("native.schedule", account_id, "error", f"actor={actor['id']} all-day state unconfirmed; ownership retained")
                    raise HTTPException(502, "全天调度状态未确认，原有定时计划已保留；请刷新检查") from None
                if plan:
                    store.delete("schedules", plan["id"])
                plan = None
            store.audit("native.schedule", account_id, detail=f"actor={actor['id']} mode={value.mode}")
            return describe(row, plan, None, None, now)

    @app.get(prefix + "/users/{user_id}")
    async def get_user(user_id: int = Path(gt=0), actor=Depends(native_admin)):
        row = await user(user_id)
        saved = [
            {"id": str(item["id"]), "name": item["name"], "subject": item["subject"],
             "body": item["body"], "builtin": False}
            for item in store.list("email_templates")
        ]
        result = {key: row.get(key) for key in ("id", "username", "email", "status", "role", "last_used_at")}
        result["templates"] = saved + list(BUILTIN_EMAIL_TEMPLATES)
        return result

    @app.post(prefix + "/users/{user_id}/email")
    async def send_email(value: Email, request: Request, user_id: int = Path(gt=0), actor=Depends(native_admin)):
        limit(request, "native.email", 10)
        if not mailer.configured:
            raise HTTPException(409, "请先配置 SMTP 环境变量")
        row = await user(user_id)
        if not isinstance(row.get("email"), str) or not row["email"]:
            raise HTTPException(409, "该用户尚未设置邮箱")
        try:
            await mailer.send(row["email"], value.subject, value.body)
        except Exception:
            store.audit("native.user.email", user_id, "error", f"actor={actor['id']} SMTP delivery failed")
            raise HTTPException(502, "SMTP 发送失败，请检查配置；未更改用户状态") from None
        store.audit("native.user.email", user_id, detail=f"actor={actor['id']}")
        return {"ok": True}
