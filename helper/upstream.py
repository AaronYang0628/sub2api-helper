"""Small, explicit adapter for the audited sub2api HTTP API.

Keep all upstream paths here so contract drift can be reviewed in one place.
The management credential is never attached to user or model gateway requests.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
import math
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx


class UpstreamError(RuntimeError):
    """An upstream failure whose detail is safe to show in the helper UI."""

    def __init__(self, detail: str, status: int = 502):
        super().__init__(detail)
        self.detail = detail
        self.status = status
        self.status_code = status


def _setting(config: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        value = config.get(name) if isinstance(config, dict) else getattr(config, name, None)
        if value is not None:
            return value.get_secret_value() if hasattr(value, "get_secret_value") else value
    return default


class Upstream:
    def __init__(self, config: Any, client: httpx.AsyncClient | None = None):
        self.base_url = str(_setting(config, "upstream_url", "sub2api_url", "base_url", default="")).rstrip("/")
        self.admin_key = str(_setting(config, "admin_key", "sub2api_admin_key", default=""))
        self.timezone = str(_setting(config, "timezone", default="Asia/Shanghai"))
        parsed = urlsplit(self.base_url)
        if self.base_url and (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("sub2api 地址必须是没有用户名、密码或查询参数的 HTTP(S) 地址")
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False)

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: Any = None,
        token: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        parsed = urlsplit(path)
        if not self.base_url:
            raise UpstreamError("请先在顶部连接 sub2api 服务", 503)
        if not path.startswith("/") or path.startswith("//") or parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or "\\" in path or "%" in path or ".." in path.split("/"):
            raise ValueError("上游请求必须使用固定的本地 API 路径")
        admin_request = path.startswith("/api/v1/admin/")
        if admin_request and token is not None:
            raise ValueError("管理员 API 与用户凭证不能混用")
        if admin_request and not self.admin_key:
            raise UpstreamError("尚未配置 sub2api 管理员 API Key", 503)
        if token is not None and not token.strip():
            raise UpstreamError("缺少用户凭证", 401)
        if headers:
            if not admin_request or set(name.lower() for name in headers) != {"idempotency-key"} or len(headers) != 1:
                raise ValueError("仅允许管理员请求显式设置 Idempotency-Key")
            for value in headers.values():
                self._idempotency_key(value)
        try:
            # A supplied client may have default credentials/cookies. Strip them
            # from the built request rather than relying on header merging.
            request = self.client.build_request(method, self.base_url + path, params=params, json=json)
            for header in ("x-api-key", "authorization", "cookie", "x-goog-api-key", "proxy-authorization", "idempotency-key"):
                request.headers.pop(header, None)
            if admin_request:
                request.headers["x-api-key"] = self.admin_key
            elif token is not None:
                request.headers["authorization"] = "Bearer " + token
            if headers:
                request.headers.update(headers)
            request.headers["accept"] = "application/json"
            response = await self.client.send(request, auth=None, follow_redirects=False)
        except httpx.HTTPError:
            # Exception strings can contain URLs or credentials. Do not echo.
            raise UpstreamError("无法连接 sub2api，请检查服务地址、网络及证书") from None
        if not 200 <= response.status_code < 300:
            messages = {
                401: "sub2api 拒绝认证，请检查凭证是否有效",
                403: "sub2api API Key 权限不足，无法执行此操作",
                404: "sub2api 接口或对象不存在，请检查版本兼容性",
                423: "sub2api 管理员尚未完成部署与运营确认，请在上游管理界面完成确认",
                429: "sub2api 请求过于频繁，请稍后重试",
            }
            raise UpstreamError(messages.get(response.status_code, f"sub2api 请求失败（HTTP {response.status_code}）"), response.status_code)
        try:
            payload = response.json()
        except ValueError:
            raise UpstreamError("sub2api 返回了非 JSON 响应，请检查反向代理与接口版本") from None
        if not isinstance(payload, dict):
            raise UpstreamError("sub2api 响应结构不兼容")
        if path.startswith("/api/v1/"):
            if type(payload.get("code")) is not int or payload["code"] != 0 or "data" not in payload:
                raise UpstreamError("sub2api 响应包络不兼容或操作未成功")
            return payload["data"]
        return payload

    async def _list(self, path: str, *, token: str | None = None, **filters: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[int] = set()
        expected_total: int | None = None
        for page in range(1, 1001):
            payload = await self.request("GET", path, params={"page": page, "page_size": 100, **filters}, token=token)
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list) or type(payload.get("total")) is not int or payload["total"] < 0:
                raise UpstreamError("sub2api 分页响应不兼容，已停止操作以避免遗漏对象")
            if expected_total is None:
                expected_total = payload["total"]
            if expected_total != payload["total"]:
                raise UpstreamError("sub2api 列表在读取期间发生变化，请重试")
            if "page" in payload and payload["page"] != page:
                raise UpstreamError("sub2api 返回了错误的页码，已停止操作")
            batch = payload["items"]
            for item in batch:
                if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] <= 0 or item["id"] in seen:
                    raise UpstreamError("sub2api 列表对象缺少唯一 ID 或分页出现重复，请重试")
                seen.add(item["id"])
                items.append(item)
            if len(items) > expected_total:
                raise UpstreamError("sub2api 分页对象数与总数不一致，请重试")
            if len(items) == expected_total:
                return items
            if not batch:
                raise UpstreamError("sub2api 分页提前结束，已停止操作以避免遗漏对象")
        raise UpstreamError("sub2api 分页超过安全上限，未返回截断列表")

    async def list_accounts(self) -> list[dict[str, Any]]:
        return await self._list("/api/v1/admin/accounts", lite="true")

    async def list_users(self) -> list[dict[str, Any]]:
        return await self._list("/api/v1/admin/users", include_subscriptions="false")

    async def list_groups(self) -> list[dict[str, Any]]:
        return await self._list("/api/v1/admin/groups")

    async def get_group(self, group_id: int) -> dict[str, Any]:
        payload = await self.request("GET", f"/api/v1/admin/groups/{self._id(group_id)}")
        if not isinstance(payload, dict) or payload.get("id") != group_id:
            raise UpstreamError("sub2api 分组响应不兼容")
        return payload

    async def list_subscriptions(self, group_id: int) -> list[dict[str, Any]]:
        return await self._list("/api/v1/admin/subscriptions", group_id=self._id(group_id), status="active")

    async def get_subscription(self, subscription_id: int) -> dict[str, Any]:
        payload = await self.request("GET", f"/api/v1/admin/subscriptions/{self._id(subscription_id)}")
        if not isinstance(payload, dict) or payload.get("id") != subscription_id or type(payload.get("group_id")) is not int or payload["group_id"] <= 0:
            raise UpstreamError("sub2api 订阅响应不兼容")
        # Group limits in a list/join response may be stale after editing a plan.
        return {**payload, "group": await self.get_group(payload["group_id"])}

    async def subscription_progress(self, subscription_id: int) -> dict[str, Any]:
        payload = await self.request("GET", f"/api/v1/admin/subscriptions/{self._id(subscription_id)}/progress")
        if not isinstance(payload, dict) or payload.get("id") != subscription_id:
            raise UpstreamError("sub2api 订阅额度进度响应不兼容")
        # GET is read-only: stale or unactivated windows are not reset here.
        # The rule engine must verify resets_at and numeric fields before use.
        return payload

    async def set_user_group_rate(self, user_id: int, group_id: int, rate: float | None) -> Any:
        self._id(user_id)
        self._id(group_id)
        value = self._positive_number(rate, "倍率") if rate is not None else None
        result = await self.request("PUT", f"/api/v1/admin/users/{user_id}", json={"group_rates": {str(group_id): value}})
        # User updates can log a rate-write failure yet return HTTP 200. This
        # group-side read propagates storage errors instead of hiding them.
        observed = await self.user_group_rate(user_id, group_id)
        if (value is None and observed is not None) or (value is not None and not self._numbers_equal(value, observed)):
            raise UpstreamError("sub2api 未应用所选用户的分组倍率，请检查上游存储及权限")
        return result

    async def user_group_rate(self, user_id: int, group_id: int) -> float | None:
        self._id(user_id)
        self._id(group_id)
        entries = await self.request("GET", f"/api/v1/admin/groups/{group_id}/rate-multipliers")
        if not isinstance(entries, list) or any(not isinstance(entry, dict) or type(entry.get("user_id")) is not int for entry in entries):
            raise UpstreamError("sub2api 用户分组倍率验证响应不兼容")
        matching = [entry for entry in entries if entry["user_id"] == user_id]
        if len(matching) > 1:
            raise UpstreamError("sub2api 返回了重复的用户分组倍率，无法确认操作结果")
        observed = matching[0].get("rate_multiplier") if matching else None
        if observed is not None and (not self._finite_number(observed) or observed <= 0):
            raise UpstreamError("sub2api 返回了无效的用户分组倍率，已停止操作")
        return float(observed) if observed is not None else None

    async def set_group_rate(self, group_id: int, rate: float) -> dict[str, Any]:
        value = self._positive_number(rate, "倍率")
        await self.request("PUT", f"/api/v1/admin/groups/{self._id(group_id)}", json={"rate_multiplier": value})
        result = await self.get_group(group_id)
        if not self._numbers_equal(value, result.get("rate_multiplier")):
            raise UpstreamError("sub2api 未应用分组默认倍率，请检查上游存储及权限")
        return result

    async def monthly_spending(self, start_date: str, end_date: str, timezone: str) -> list[dict[str, Any]]:
        """Complete current eligible-user ranking, not the truncated top-50 API.

        One aggregate request per existing active ordinary user, with at most
        four requests in flight. Any incomplete response aborts the snapshot.
        """
        params = {**self._dates(start_date, end_date), "timezone": timezone, "nocache": "true"}
        ZoneInfo(timezone)
        users = await self.list_users()
        if any(user.get("role") not in {"admin", "user"} or user.get("status") not in {"active", "disabled"} for user in users):
            raise UpstreamError("sub2api 用户资格字段缺失或不兼容，已停止月度结算")
        users = [user for user in users if user.get("role") == "user" and user.get("status") == "active"]
        semaphore = asyncio.Semaphore(4)

        async def aggregate(user: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                stats = await self.request("GET", "/api/v1/admin/usage/stats", params={**params, "user_id": user["id"]})
            if not isinstance(stats, dict):
                raise UpstreamError("sub2api 用户月消费汇总响应不兼容")
            cost = stats.get("total_actual_cost")
            requests, tokens = stats.get("total_requests"), stats.get("total_tokens")
            if not self._finite_number(cost) or cost < 0 or type(requests) is not int or requests < 0 or type(tokens) is not int or tokens < 0:
                raise UpstreamError("sub2api 用户月消费汇总缺少有效金额或用量，已停止结算")
            return {"user_id": user["id"], "username": user.get("username", ""), "email": user.get("email", ""), "actual_cost": cost, "requests": requests, "tokens": tokens}

        outcomes = await asyncio.gather(*(aggregate(user) for user in users), return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        return [row for row in outcomes if row["actual_cost"] > 0]

    async def add_balance(self, user_id: int, amount: float, idempotency_key: str) -> Any:
        """Issue one credit attempt. Callers must persist an intent BEFORE use.

        sub2api's idempotency record and credit are separate transactions. This
        adapter must never retry an uncertain response, even with the same key.
        """
        value = self._positive_number(amount, "奖励金额")
        key = self._idempotency_key(idempotency_key)
        return await self.request("POST", f"/api/v1/admin/users/{self._id(user_id)}/balance", json={"balance": value, "operation": "add", "notes": "Helper monthly reward " + key}, headers={"Idempotency-Key": key})

    @staticmethod
    def _idempotency_key(value: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 128 or any(ord(character) < 33 or ord(character) > 126 for character in value):
            raise ValueError("Idempotency-Key 必须为 1 至 128 个可打印 ASCII 字符，且不含空格")
        return value

    @staticmethod
    def _finite_number(value: Any) -> bool:
        return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) and math.isfinite(value)

    @classmethod
    def _positive_number(cls, value: Any, label: str) -> float:
        if not cls._finite_number(value) or value <= 0:
            raise ValueError(f"{label}必须为有限正数")
        return float(value)

    @classmethod
    def _numbers_equal(cls, expected: float, observed: Any) -> bool:
        return cls._finite_number(observed) and math.isclose(expected, observed, rel_tol=1e-10, abs_tol=1e-10)

    async def get_user(self, user_id: int) -> dict[str, Any]:
        return await self.request("GET", f"/api/v1/admin/users/{self._id(user_id)}")

    async def set_schedulable(self, account_id: int, enabled: bool) -> dict[str, Any]:
        if type(enabled) is not bool:
            raise ValueError("schedulable 必须为布尔值")
        return await self.request("POST", f"/api/v1/admin/accounts/{self._id(account_id)}/schedulable", json={"schedulable": enabled})

    async def set_user_status(self, user_id: int, status: str) -> dict[str, Any]:
        if status not in {"active", "disabled"}:
            raise ValueError("用户状态必须是 active 或 disabled")
        return await self.request("PUT", f"/api/v1/admin/users/{self._id(user_id)}", json={"status": status})

    async def settings(self) -> dict[str, Any]:
        payload = await self.request("GET", "/api/v1/admin/settings")
        if not isinstance(payload, dict):
            raise UpstreamError("sub2api 设置响应不兼容")
        return payload

    async def update_menus(self, menu_items: list[dict[str, Any]]) -> Any:
        # The audited upstream supports omittedSettingKeys. Older servers must
        # not use this operation until their partial-settings semantics pass CI.
        return await self.request("PUT", "/api/v1/admin/settings", json={"custom_menu_items": menu_items})

    def _dates(self, start_date: str, end_date: str) -> dict[str, str]:
        if date.fromisoformat(start_date) > date.fromisoformat(end_date):
            raise ValueError("开始日期不得晚于结束日期")
        return {"start_date": start_date, "end_date": end_date, "timezone": self.timezone}

    async def models(self, start_date: str, end_date: str, token: str | None = None, group_id: int | None = None) -> dict[str, Any]:
        path = "/api/v1/usage/dashboard/models" if token is not None else "/api/v1/admin/dashboard/models"
        params = self._dates(start_date, end_date)
        if group_id is not None:
            params["group_id"] = self._id(group_id)
        return await self.request("GET", path, params=params, token=token)

    async def ranking(self, start_date: str, end_date: str, limit: int = 50) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("排名数量必须在 1 至 100 之间")
        return await self.request("GET", "/api/v1/admin/dashboard/users-ranking", params={**self._dates(start_date, end_date), "limit": limit})

    async def profile(self, token: str) -> dict[str, Any]:
        return await self.request("GET", "/api/v1/user/profile", token=token)

    async def available_group_rows(self, token: str) -> list[dict[str, Any]]:
        rows = await self.request("GET", "/api/v1/groups/available", token=token)
        if not isinstance(rows, list) or any(not isinstance(row, dict) or type(row.get("id")) is not int
                                             or row["id"] <= 0 for row in rows):
            raise UpstreamError("上游可用分组响应不兼容")
        return [{key: row.get(key) for key in ("id", "name", "platform", "status", "rate_multiplier")} for row in rows]

    async def available_groups(self, token: str) -> set[int]:
        return {row["id"] for row in await self.available_group_rows(token) if row.get("status") == "active"}

    async def benchmark_completion(self, api_key: str, model: str, prompt: str) -> dict[str, Any]:
        """One bounded OpenAI-compatible request, authenticated by a user key."""
        return await self.request("POST", "/v1/chat/completions", token=api_key, json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 256, "stream": False})

    async def _benchmark_keys(self, admin_user_id: int | None = None) -> list[dict[str, Any]]:
        # The compliance status belongs to the authenticated key's actual admin
        # actor. Do not guess an administrator from a user list or supplied ID.
        status = await self.request("GET", "/api/v1/admin/compliance")
        if not isinstance(status, dict) or status.get("required") is not False:
            raise UpstreamError("请先在 sub2api 管理界面完成部署与运营确认，再读取管理员的模型密钥", 423)
        acknowledgement = status.get("acknowledgement")
        owner_id = acknowledgement.get("admin_user_id") if isinstance(acknowledgement, dict) else None
        if type(owner_id) is not int or owner_id <= 0:
            raise UpstreamError("无法确认管理凭证对应的管理员身份，已停止模型检测")
        if admin_user_id is not None and admin_user_id != owner_id:
            raise UpstreamError("所选管理员与管理凭证对应身份不一致", 403)
        owner = await self.get_user(owner_id)
        if not isinstance(owner, dict) or owner.get("id") != owner_id or owner.get("role") != "admin" or owner.get("status") != "active":
            raise UpstreamError("管理凭证对应用户不是有效管理员，已停止模型检测", 403)
        keys = await self._list(f"/api/v1/admin/users/{owner_id}/api-keys")
        return self._usable_benchmark_keys(keys, owner_id)

    @staticmethod
    def _usable_benchmark_keys(keys: list[dict[str, Any]], owner_id: int) -> list[dict[str, Any]]:
        usable = []
        now = datetime.now(timezone.utc)
        for key in keys:
            if key.get("user_id") != owner_id or key.get("status") != "active" or not isinstance(key.get("key"), str) or not key["key"].strip():
                continue
            group_id = key.get("group_id")
            if type(group_id) is not int or group_id <= 0:
                continue  # a caller must select the exact billed group
            group = key.get("group")
            if group is not None and (not isinstance(group, dict) or group.get("id") != group_id or group.get("status") not in {None, "active"}):
                continue
            if key.get("expires_at"):
                try:
                    expiry = datetime.fromisoformat(key["expires_at"].replace("Z", "+00:00"))
                    if expiry.tzinfo is None or expiry <= now:
                        continue
                except (ValueError, TypeError, AttributeError):
                    continue
            try:
                quota = float(key.get("quota", 0))
                quota_used = float(key.get("quota_used", 0))
                if quota > 0 and quota_used >= quota:
                    continue
            except (ValueError, TypeError):
                continue
            usable.append(key)
        return usable

    async def benchmark_keys(self, admin_user_id: int | None = None) -> list[dict[str, Any]]:
        """Return selectable key/group metadata. Never return raw credentials."""
        keys = await self._benchmark_keys(admin_user_id)
        return self._benchmark_options(keys)

    async def benchmark_key(self, key_id: int, group_id: int, admin_user_id: int | None = None) -> str:
        """Resolve a selected, owned, active ordinary key for server-side use."""
        self._id(key_id)
        self._id(group_id)
        keys = await self._benchmark_keys(admin_user_id)
        for key in keys:
            if key["id"] == key_id and key["group_id"] == group_id:
                return key["key"]
        raise UpstreamError("所选分组没有匹配的有效管理员模型密钥，请在 sub2api 创建或启用后重试", 404)

    async def _user_benchmark_keys(self, token: str) -> list[dict[str, Any]]:
        owner = await self.profile(token)
        owner_id = owner.get("id") if isinstance(owner, dict) else None
        if type(owner_id) is not int or owner_id <= 0 or owner.get("status") != "active":
            raise UpstreamError("当前用户身份无效或已被禁用，已停止模型检测", 401)
        keys = await self._list("/api/v1/keys", token=token)
        return self._usable_benchmark_keys(keys, owner_id)

    async def user_benchmark_keys(self, token: str) -> list[dict[str, Any]]:
        """Selectable ordinary keys belonging to the verified embedded user."""
        return self._benchmark_options(await self._user_benchmark_keys(token))

    async def user_benchmark_key(self, token: str, key_id: int, group_id: int) -> str:
        """Resolve only this user's exact selected key and billed group."""
        self._id(key_id)
        self._id(group_id)
        for key in await self._user_benchmark_keys(token):
            if key["id"] == key_id and key["group_id"] == group_id:
                return key["key"]
        raise UpstreamError("所选分组没有匹配的本人有效模型密钥，请在 sub2api 创建或启用后重试", 404)

    @staticmethod
    def _benchmark_options(keys: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"id": key["id"], "name": key.get("name", ""), "group_id": key["group_id"], "group_name": (key.get("group") or {}).get("name", "")} for key in keys]

    @staticmethod
    def _id(value: int) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("ID 必须是正整数")
        return value

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
