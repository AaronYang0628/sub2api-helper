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


class NativeSchedule(Input):
    mode: Literal["all_day", "window"]
    name: str | None = Field(default=None, min_length=1, max_length=100)
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
        # all_day has no time window, so equal start/end values are harmless.
        if self.mode == "window":
            Schedule(name=self.name or "native", account_ids=[1], enabled=True,
                     **self.model_dump(exclude={"mode", "name"}))
        elif any(day < 0 or day > 6 for day in self.weekdays) or len(set(self.weekdays)) != len(self.weekdays):
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
        owned, conflicts = [], []
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
                conflicts.append("该账号已由其他定时计划管理，请先在高级设置中停用该计划")
        if len(owned) > 1:
            conflicts.append("该账号存在多个原生定时计划，请先在高级设置中检查")
        return owned[0] if len(owned) == 1 else None, next(iter(conflicts), None)

    def describe(row, plan, conflict):
        config = config_provider()
        return {
            "account_id": row["id"], "account_name": row.get("name", ""),
            "schedulable": row["schedulable"], "mode": "window" if plan and plan.get("enabled") else "all_day",
            "managed": plan is not None, "read_only": bool(conflict), "conflict": conflict,
            "name": (plan or {}).get("name") or f"账号 #{row['id']} 定时调度",
            "start": (plan or {}).get("start", "23:00"), "end": (plan or {}).get("end", "09:00"),
            "timezone": (plan or {}).get("timezone", config.timezone),
            "weekdays": (plan or {}).get("weekdays", list(range(7))),
            "last_error": (plan or {}).get("last_error"),
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
            plan, conflict = ownership(account_id)
            return describe(row, plan, conflict)

    @app.put(prefix + "/accounts/{account_id}/schedule")
    async def put_schedule(value: NativeSchedule, account_id: int = Path(gt=0), actor=Depends(native_admin)):
        async with engine._tick_lock:
            row = await account(account_id)
            plan, conflict = ownership(account_id)
            if conflict:
                raise HTTPException(409, conflict)
            now = datetime.now(timezone.utc)
            if value.mode == "window":
                resolved_name = value.name or (plan or {}).get("name") or f"账号 #{account_id} 定时调度"
                data = value.model_dump(exclude={"mode", "name"}) | {
                    "name": resolved_name, "account_ids": [account_id],
                    "owner": "native-account", "native_account_id": account_id, "enabled": True,
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
            return describe(row, plan, None)

    @app.get(prefix + "/users/{user_id}")
    async def get_user(user_id: int = Path(gt=0), actor=Depends(native_admin)):
        row = await user(user_id)
        return {key: row.get(key) for key in ("id", "username", "email", "status", "role")}

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
