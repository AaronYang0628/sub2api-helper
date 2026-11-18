import asyncio
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta, timezone
from dataclasses import replace
import hmac
import hashlib
import json
import re
from pathlib import Path
import secrets
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Path as ApiPath
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
import httpx

from . import __version__
from .benchmark import run_benchmark
from .config import Config
from .credentials import CredentialVault
from .engine import Engine
from .gallery import Gallery, GalleryError
from .gallery_api import install_gallery_api
from .gallery_generation import generate_artwork
from .mailer import Mailer
from .native_proxy import NativeProxy, is_document_request
from .native_api import install_native_api
from .schemas import Automation, Benchmark, BenchmarkSelection, Connection, Email, Modules, Rule, Schedule, Session, Toggle, UserStatus
from .store import Store
from .upstream import Upstream, UpstreamError

BASELINE = json.loads((Path(__file__).resolve().parent.parent / "docs/upstream-baseline.json").read_text())["reviewed_commit"]
STATIC = Path(__file__).parent / "static"
MODULE_NAMES = {"benchmark": "智商检测", "pricing": "模型价格统计", "ranking": "用户用量排名"}
ACCOUNT_FIELDS = {"id", "name", "platform", "type", "status", "schedulable", "group_ids"}
USER_FIELDS = {"id", "username", "email", "role", "status", "last_used_at", "last_active_at", "created_at"}
HELPER_API_ROOTS = {"status", "connection", "accounts", "schedules", "users", "rules", "modules", "audit", "groups",
                    "automations", "settlements", "analytics", "benchmark", "gallery", "portal", "compatibility"}


def create_app(config=None, upstream=None, store=None, mailer=None, background=True, connector=None, native_proxy=None):
    config = config or Config.from_env()
    store = store or Store(config.db_path)
    vault = CredentialVault(store, config.db_path)
    binding = vault.read()
    if not config.public_url:
        config = replace(config, public_url=store.get_setting("public_url", ""))
    if binding:
        if config.sub2api_url and config.sub2api_url != binding["url"]:
            raise RuntimeError("SUB2API_URL 与已绑定实例不符，请保留原地址或使用新的数据卷")
        config = replace(config, sub2api_url=binding["url"],
                         sub2api_admin_key=config.sub2api_admin_key or binding["admin_key"])
    upstream = upstream or Upstream(config)
    mailer = mailer or Mailer(config)
    engine = Engine(store, upstream, mailer)
    native_proxy = native_proxy or NativeProxy(lambda: config.sub2api_url,
                                              public_url_provider=lambda: config.sub2api_public_url or config.public_url)
    sessions = {}
    admin_sessions = {}
    native_sessions = {}
    last_admin_verification = [0.0]
    requests_by_ip = defaultdict(deque)
    mutations = engine._tick_lock
    benchmark_slots = asyncio.Semaphore(2)
    benchmark_users = set()

    async def generate_gallery_work(job):
        api_key = await upstream.benchmark_key(job["key_id"], job["group_id"])
        selection = SimpleNamespace(model=job["model"], api_format=job["api_format"],
                                    prompt=job["prompt"], api_key=api_key)
        return await generate_artwork(config, selection)

    gallery = Gallery(store, generate_gallery_work)

    @asynccontextmanager
    async def lifespan(app):
        tasks = []
        if background:
            tasks = [asyncio.create_task(engine.run()), asyncio.create_task(compatibility_loop()),
                     asyncio.create_task(gallery.run())]
        yield
        engine.stop()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await gallery.close()
        await upstream.close()
        await native_proxy.close()
        store.close()

    app = FastAPI(title="sub2api helper", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine
    app.state.store = store
    app.state.gallery = gallery
    admin_api = APIRouter()
    portal_api = APIRouter()

    @app.exception_handler(UpstreamError)
    async def upstream_error(request, exc):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(GalleryError)
    async def gallery_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic errors can echo the original input, including admin keys/JWTs.
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()]})

    @app.middleware("http")
    async def security_headers(request, call_next):
        path_parts = request.url.path.split("/")
        helper_path = (request.url.path.startswith("/_helper/api/") or
                       (config.legacy_routes and len(path_parts) >= 3
                        and path_parts[1] == "api" and path_parts[2] in HELPER_API_ROOTS))
        if helper_path and request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            try:
                if int(request.headers.get("content-length", "0")) > 65536:
                    return JSONResponse(status_code=413, content={"detail": "请求过大"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "无效请求长度"})
            chunks, length = [], 0
            async for chunk in request.stream():
                length += len(chunk)
                if length > 65536:
                    return JSONResponse(status_code=413, content={"detail": "请求过大"})
                chunks.append(chunk)
            # Starlette's wrapped request replays a cached body to the route.
            # Business requests are never relayed through this application.
            request._body = b"".join(chunks)
        response = await call_next(request)
        if getattr(request.state, "helper_proxied", False):
            return response
        source = urlsplit(config.sub2api_public_url or config.sub2api_url)
        ancestor = f"{source.scheme}://{source.netloc}" if source.hostname else "http: https:"
        benchmark_page = request.url.path in {"/_helper/setup", "/_helper/console", "/_helper/features/benchmark"}
        if config.legacy_routes and request.url.path in {"/", "/features/benchmark"}:
            benchmark_page = True
        image_sources = "'self' data: https:" if benchmark_page else "'self' data:"
        # A sandboxed srcdoc inherits its parent CSP. Permit artwork CSS here;
        # the child also supplies its own policy blocking all external loads.
        styles = "'self' 'unsafe-inline'" if benchmark_page else "'self'"
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; script-src 'self'; style-src {styles}; "
            f"img-src {image_sources}; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
            f"frame-ancestors 'self' {ancestor}"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    async def admin(request: Request, authorization: str = Header(default="")):
        expected = "Bearer " + config.admin_token
        if hmac.compare_digest(authorization.encode(), expected.encode()):
            return
        token = authorization.removeprefix("Bearer ")
        native_entry = native_sessions.get(token)
        if native_entry:
            if (native_entry["expires"] <= time.monotonic() or
                    native_entry["user_agent"] != request.headers.get("user-agent", "")):
                native_sessions.pop(token, None)
                raise HTTPException(401, "请重新打开原生列表中的助手操作")
            try:
                profile = await verify_native(native_entry["jwt"], native_entry["user_agent"])
                if profile["id"] != native_entry["actor"]:
                    raise HTTPException(401, "管理员身份已改变，请重新打开操作")
            except (HTTPException, UpstreamError):
                native_sessions.pop(token, None)
                raise
            return
        if admin_sessions.get(token, 0) > time.monotonic():
            if time.monotonic() - last_admin_verification[0] >= 30:
                try:
                    await verify_connection(upstream)
                except UpstreamError as exc:
                    if exc.status in (401, 403):
                        admin_sessions.clear()
                    raise
                last_admin_verification[0] = time.monotonic()
            return
        admin_sessions.pop(token, None)
        raise HTTPException(401, "请在顶部填写有效的管理员 Key 并连接服务")

    def limit(request, category, count=30, seconds=60):
        now = time.monotonic()
        # Ignore X-Forwarded-For: only trust the actual peer, not arbitrary client input.
        key = (request.client.host if request.client else "unknown", category)
        if len(requests_by_ip) > 4096:
            for old_key in list(requests_by_ip):
                if not requests_by_ip[old_key] or now - requests_by_ip[old_key][-1] > seconds:
                    del requests_by_ip[old_key]
        queue = requests_by_ip[key]
        while queue and now - queue[0] > seconds:
            queue.popleft()
        if len(queue) >= count:
            raise HTTPException(429, "请求过于频繁，请稍后再试")
        queue.append(now)

    def validate_profile(profile):
        if (not isinstance(profile, dict) or type(profile.get("id")) is not int
                or profile["id"] <= 0 or profile.get("role") not in {"user", "admin"}
                or profile.get("status") not in {"active", "disabled"}):
            raise HTTPException(502, "上游用户身份响应不完整，已停止验证")
        if profile["status"] != "active":
            raise HTTPException(403, "用户已停用")
        return profile

    async def verify_native(token, user_agent):
        if not config.native_ui:
            raise HTTPException(404, "原生列表增强未启用")
        if not config.sub2api_url or not config.sub2api_admin_key:
            raise HTTPException(503, "请先在 /_helper/setup 绑定服务地址和管理员 Key")
        if not token or len(token) > 8192:
            raise HTTPException(401, "请先登录 sub2api 管理员账号")
        # A profile request from another IP can revoke a bound upstream session.
        # Require an explicit false before attempting to use the browser JWT.
        settings = await upstream.settings()
        if settings.get("session_binding_enabled") is not False:
            raise HTTPException(409, "上游会话绑定已启用或状态未知，原生助手操作暂不可用")
        try:
            profile = validate_profile(await native_proxy.profile(token, user_agent))
        except UpstreamError as exc:
            if exc.status in (401, 403):
                raise HTTPException(exc.status, "sub2api 登录已失效或没有管理权限") from None
            raise
        if profile["role"] != "admin":
            raise HTTPException(403, "仅 sub2api 管理员可以使用此操作")
        return profile

    async def native_admin(request: Request, authorization: str = Header(default="")):
        limit(request, "native.auth", 300)
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "请先登录 sub2api 管理员账号")
        return await verify_native(authorization[7:], request.headers.get("user-agent", ""))

    async def portal(x_portal_session: str = Header(default="")):
        entry = sessions.get(x_portal_session)
        if not entry or entry["expires"] < time.monotonic():
            sessions.pop(x_portal_session, None)
            raise HTTPException(401, "请从 sub2api 自定义菜单重新打开此页面")
        # Check session-binding before profile: changing IP/UA could revoke a bound JWT.
        settings = await upstream.settings()
        if settings.get("session_binding_enabled"):
            raise HTTPException(409, "上游启用了会话绑定，暂不支持通过助手验证用户身份")
        profile = validate_profile(await upstream.profile(entry["token"]))
        return {"profile": profile, "token": entry["token"]}

    def enabled(module):
        if not store.get_setting("modules", {}).get(module, False):
            raise HTTPException(404, "该功能尚未上线")

    def dates(start_date=None, end_date=None):
        today = datetime.now(ZoneInfo(config.timezone)).date()
        try:
            start = date.fromisoformat(start_date) if start_date else today - timedelta(days=29)
            end = date.fromisoformat(end_date) if end_date else today
        except ValueError:
            raise HTTPException(422, "日期格式必须为 YYYY-MM-DD")
        if start > end or (end - start).days > 366:
            raise HTTPException(422, "日期范围需在 367 天内且开始不能晚于结束")
        return start.isoformat(), end.isoformat()

    def check_schedule(value, exclude=None):
        if not value.enabled:
            return
        ids = set(value.account_ids)
        for existing in store.list("schedules"):
            if str(existing["id"]) != str(exclude) and existing.get("enabled") and ids.intersection(existing["account_ids"]):
                raise HTTPException(409, f"账号已由启用中的计划「{existing['name']}」管理，请先停用该计划")

    async def validate_accounts(value):
        found = {a["id"] for a in await upstream.list_accounts()}
        if not set(value.account_ids).issubset(found):
            raise HTTPException(422, "部分账号不存在或当前管理 Key 无权访问")

    def existing(kind, item_id):
        item = store.get(kind, item_id)
        if item is None:
            raise HTTPException(404, "记录不存在")
        return item

    @app.get("/_helper/healthz")
    async def health():
        # Liveness only; detailed upstream/worker state remains authenticated.
        return {"status": "ok", "version": __version__}

    @app.get("/_helper/setup")
    async def native_setup():
        return FileResponse(STATIC / "index.html")

    @app.get("/_helper/console")
    async def native_console():
        html = (STATIC / "index.html").read_text()
        html = html.replace('</head>', '<link rel="stylesheet" href="/_helper/console.css"></head>')
        return HTMLResponse(html)

    @app.get("/_helper/console.css")
    async def native_console_css():
        return FileResponse(STATIC / "native-console.css")

    @app.get("/_helper/native.js")
    async def native_script():
        return FileResponse(STATIC / "native.js", media_type="application/javascript")

    @app.get("/_helper/native.css")
    async def native_styles():
        return FileResponse(STATIC / "native.css", media_type="text/css")

    @app.post("/_helper/api/session")
    async def native_session(request: Request, profile=Depends(native_admin)):
        limit(request, "native.session", 20)
        now = time.monotonic()
        for old in list(native_sessions):
            if native_sessions[old]["expires"] <= now:
                del native_sessions[old]
        if len(native_sessions) >= 256:
            native_sessions.pop(next(iter(native_sessions)))
        token = secrets.token_urlsafe(32)
        native_sessions[token] = {"jwt": request.headers["authorization"][7:],
                                  "user_agent": request.headers.get("user-agent", ""),
                                  "actor": profile["id"], "expires": now + 15 * 60}
        return {"session": token}

    @app.post("/_helper/api/session/revoke")
    async def native_session_revoke(request: Request, authorization: str = Header(default="")):
        # Possession can revoke only matching local sessions. Never validate this
        # logout hint upstream, and never reveal whether a cached token existed.
        limit(request, "native.session.revoke", 60)
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "请提供需要退出的会话凭证")
        token = authorization[7:]
        if not token or len(token.encode("utf-8")) > 8192:
            raise HTTPException(401, "会话凭证无效")
        native_sessions.pop(token, None)
        for cached, entry in list(native_sessions.items()):
            if hmac.compare_digest(entry["jwt"].encode(), token.encode()):
                native_sessions.pop(cached, None)
        return {"ok": True}

    @app.get("/_helper/features/{module}")
    async def feature(module: str):
        if module not in MODULE_NAMES:
            raise HTTPException(404)
        enabled(module)
        return FileResponse(STATIC / "feature.html")

    @admin_api.get("/status", dependencies=[Depends(admin)])
    async def status():
        return {"upstream_url": config.sub2api_url, "timezone": config.timezone,
                "mail_configured": mailer.configured, "public_url": config.public_url,
                "version": __version__, "engine": engine.status}

    @admin_api.get("/connection")
    async def connection_info():
        return {"upstream_url": config.sub2api_url, "configured": bool(config.sub2api_admin_key),
                "public_url": config.public_url, "timezone": config.timezone}

    async def verify_connection(candidate):
        try:
            result = await candidate.request("GET", "/api/v1/admin/compliance")
            if not isinstance(result, dict) or type(result.get("required")) is not bool:
                raise HTTPException(502, "上游管理员认证响应不兼容")
            return "请先在 sub2api 管理界面完成部署与运营确认" if result["required"] else None
        except UpstreamError as exc:
            if exc.status != 403:
                raise
            for path in ("/api/v1/admin/accounts", "/api/v1/admin/users", "/api/v1/admin/dashboard/stats"):
                try:
                    result = await candidate.request("GET", path, params={"page": 1, "page_size": 1, "lite": "true"})
                    if isinstance(result, dict):
                        return "当前 Key 为受限权限，部分功能可能不可用"
                except UpstreamError as probe_error:
                    if probe_error.status not in (403, 423):
                        raise
            raise UpstreamError("管理员 Key 缺少助手所需的读取权限，或尚未完成上游运营确认", 403)

    @admin_api.post("/connection")
    async def connection(value: Connection, request: Request):
        nonlocal config
        limit(request, "connection", 10)
        async with mutations:
            # A supplied URL is only a suggestion until authenticated. Once bound,
            # never allow an unrelated server to mint local management sessions.
            if config.sub2api_url and value.url != config.sub2api_url:
                raise HTTPException(409, "此助手已限定到一个 sub2api 实例，不能通过登录切换服务地址")
            newconfig = replace(config, sub2api_url=value.url, sub2api_admin_key=value.admin_key,
                                public_url=config.public_url or value.helper_url)
            candidate = (connector or Upstream)(newconfig)
            try:
                warning = await verify_connection(candidate)
                vault.write(value.url, value.admin_key)
                if newconfig.public_url:
                    store.set_setting("public_url", newconfig.public_url)
                if config.sub2api_admin_key != value.admin_key:
                    admin_sessions.clear()
                    native_sessions.clear()
                    sessions.clear()
                upstream.base_url = value.url
                upstream.admin_key = value.admin_key
                config = newconfig
                now = time.monotonic()
                for old in list(admin_sessions):
                    if admin_sessions[old] <= now:
                        del admin_sessions[old]
                if len(admin_sessions) >= 256:
                    admin_sessions.pop(next(iter(admin_sessions)))
                token = secrets.token_urlsafe(32)
                admin_sessions[token] = now + 8 * 3600
                last_admin_verification[0] = now
                store.audit("connection.validated", "upstream", detail="管理员凭证已验证并加密保存")
                return {"session": token, "upstream_url": config.sub2api_url, "warning": warning}
            finally:
                await candidate.close()

    @admin_api.post("/connection/disconnect")
    async def disconnect(authorization: str = Header(default="")):
        admin_sessions.pop(authorization.removeprefix("Bearer "), None)
        native_sessions.pop(authorization.removeprefix("Bearer "), None)
        return {"ok": True}

    @admin_api.get("/accounts", dependencies=[Depends(admin)])
    async def accounts():
        return [{k: v for k, v in item.items() if k in ACCOUNT_FIELDS} for item in await upstream.list_accounts()]

    @admin_api.post("/accounts/{account_id}/schedulable", dependencies=[Depends(admin)])
    async def account_toggle(value: Toggle, account_id: int = ApiPath(gt=0)):
        async with mutations:
            for plan in store.list("schedules"):
                if plan.get("enabled") and account_id in plan["account_ids"]:
                    raise HTTPException(409, "账号由定时计划管理，请先停用计划再手动操作")
            await upstream.set_schedulable(account_id, value.schedulable)
            store.audit("account.toggle", str(account_id), detail=str(value.schedulable))
            return {"ok": True}

    @admin_api.get("/schedules", dependencies=[Depends(admin)])
    async def schedules():
        return store.list("schedules")

    @admin_api.post("/schedules", dependencies=[Depends(admin)])
    async def schedule_create(value: Schedule):
        async with mutations:
            check_schedule(value)
            await validate_accounts(value)
            result = store.create("schedules", value.model_dump())
            store.audit("schedule.create", str(result["id"]), detail=value.name)
            return result

    @admin_api.put("/schedules/{item_id}", dependencies=[Depends(admin)])
    async def schedule_update(item_id: str, value: Schedule):
        async with mutations:
            existing("schedules", item_id)
            check_schedule(value, item_id)
            await validate_accounts(value)
            result = store.update("schedules", item_id, value.model_dump())
            store.audit("schedule.update", item_id, detail=value.name)
            return result

    @admin_api.delete("/schedules/{item_id}", dependencies=[Depends(admin)])
    async def schedule_delete(item_id: str):
        async with mutations:
            existing("schedules", item_id)
            store.delete("schedules", item_id)
            store.audit("schedule.delete", item_id)
            return {"ok": True}

    @admin_api.get("/users", dependencies=[Depends(admin)])
    async def users():
        return [{k: v for k, v in item.items() if k in USER_FIELDS} for item in await upstream.list_users()]

    @admin_api.post("/users/{user_id}/email", dependencies=[Depends(admin)])
    async def user_email(value: Email, request: Request, user_id: int = ApiPath(gt=0)):
        limit(request, "email", 10)
        if not mailer.configured:
            raise HTTPException(409, "请先配置 SMTP 环境变量")
        user = await upstream.get_user(user_id)
        try:
            await mailer.send(user.get("email", ""), value.subject, value.body)
        except Exception:
            store.audit("user.email", str(user_id), status="error", detail="SMTP 发送失败，请检查配置；响应中不包含凭据")
            raise HTTPException(502, "SMTP 发送失败，请检查配置；未更改用户状态")
        store.audit("user.email", str(user_id))
        return {"ok": True}

    @admin_api.post("/users/{user_id}/status", dependencies=[Depends(admin)])
    async def user_status(value: UserStatus, user_id: int = ApiPath(gt=0)):
        async with mutations:
            user = await upstream.get_user(user_id)
            if user.get("role") != "user":
                raise HTTPException(409, "助手仅支持更改已确认的普通用户启用状态")
            await upstream.set_user_status(user_id, value.status)
            store.audit("user.status", str(user_id), detail=value.status)
            return {"ok": True}

    def check_rule(value, exclude=None):
        if value.enabled and not mailer.configured:
            raise HTTPException(409, "启用自动提醒规则前必须配置 SMTP")
        if value.enabled and any(r.get("enabled") and str(r["id"]) != str(exclude) for r in store.list("rules")):
            raise HTTPException(409, "当前版本仅允许一个自动闲置规则启用，避免重复发信")

    @admin_api.get("/rules", dependencies=[Depends(admin)])
    async def rules():
        return store.list("rules")

    @admin_api.post("/rules", dependencies=[Depends(admin)])
    async def rule_create(value: Rule):
        async with mutations:
            check_rule(value)
            result = store.create("rules", value.model_dump())
            store.audit("rule.create", str(result["id"]), detail=value.name)
            return result

    @admin_api.put("/rules/{item_id}", dependencies=[Depends(admin)])
    async def rule_update(item_id: str, value: Rule):
        async with mutations:
            existing("rules", item_id)
            check_rule(value, item_id)
            result = store.update("rules", item_id, value.model_dump())
            store.audit("rule.update", item_id, detail=value.name)
            return result

    @admin_api.delete("/rules/{item_id}", dependencies=[Depends(admin)])
    async def rule_delete(item_id: str):
        async with mutations:
            existing("rules", item_id)
            store.delete("rules", item_id)
            store.audit("rule.delete", item_id)
            return {"ok": True}

    @admin_api.get("/modules", dependencies=[Depends(admin)])
    async def modules():
        return Modules(**store.get_setting("modules", {})).model_dump()

    @admin_api.put("/modules", dependencies=[Depends(admin)])
    async def module_update(value: Modules):
        async with mutations:
            if any(value.model_dump().values()) and not config.public_url:
                raise HTTPException(409, "请设置 HELPER_PUBLIC_URL 为用户浏览器可访问的 HTTPS 地址")
            settings = await upstream.settings()
            current = settings.get("custom_menu_items", [])
            if not isinstance(current, list) or not all(isinstance(item, dict) for item in current):
                raise HTTPException(502, "上游菜单格式不兼容")
            managed = {"sub2api-helper-" + key for key in MODULE_NAMES}
            merged = [item for item in current if item.get("id") not in managed]
            for i, (name, label) in enumerate(MODULE_NAMES.items()):
                if getattr(value, name):
                    merged.append({"id": "sub2api-helper-" + name, "label": label, "icon_svg": "",
                                   "url": config.public_url.rstrip("/") + "/_helper/features/" + name,
                                   "visibility": "user", "sort_order": 100 + i, "hide_open_button": True})
            if len(merged) > 20:
                raise HTTPException(409, "上游最多支持 20 个菜单，请先移除部分菜单")
            await upstream.update_menus(merged)
            # Verify upstream actually applied the change before persisting local visibility.
            applied = await upstream.settings()
            if applied.get("custom_menu_items") != merged:
                raise HTTPException(502, "菜单写入未确认，请刷新后重试；本地功能状态未改变")
            store.set_setting("modules", value.model_dump())
            store.audit("modules.sync", "menus", detail=str(value.model_dump()))
            return value.model_dump()

    @admin_api.get("/audit", dependencies=[Depends(admin)])
    async def audit():
        return store.audits(200)

    @admin_api.get("/groups", dependencies=[Depends(admin)])
    async def groups():
        fields = {"id", "name", "status", "subscription_type", "rate_multiplier", "daily_limit_usd", "weekly_limit_usd", "monthly_limit_usd"}
        return [{k: v for k, v in item.items() if k in fields} for item in await upstream.list_groups()]

    async def check_automation(value, exclude=None):
        if value.type == "subscription_multiplier":
            group = await upstream.get_group(value.group_id)
            if not isinstance(group, dict) or group.get("status") != "active" or group.get("subscription_type") != "subscription":
                raise HTTPException(422, "请选择启用中的订阅分组")
        if not value.enabled:
            return
        for rule in store.list("automations"):
            if not rule.get("enabled") or str(rule["id"]) == str(exclude):
                continue
            if rule["type"] == value.type == "monthly_rewards":
                raise HTTPException(409, "一个实例同一时间只能启用一个月度奖励计划，避免重复结算")
            if rule["type"] == value.type == "subscription_multiplier" and rule.get("group_id") == value.group_id:
                raise HTTPException(409, "该分组已有启用的倍率规则，请先停用它，避免多个规则覆盖同一倍率")

    def automation_data(value, old=None):
        data = value.model_dump(mode="json")
        monthly_changed = bool(old and value.type == "monthly_rewards" and any(
            old.get(field) != data.get(field) for field in ("timezone", "mode", "rewards")))
        if value.enabled and (not (old or {}).get("enabled") or monthly_changed):
            data["enabled_at"] = datetime.now(timezone.utc).isoformat()
        return data

    async def cleanup_automation(rule):
        if rule["type"] == "subscription_multiplier":
            report = await engine.automation.deactivate(rule)
            if report.get("errors"):
                raise HTTPException(409, {"message": "部分倍率尚未安全恢复，请查看执行记录并处理后重试", "report": report})

    @admin_api.get("/automations", dependencies=[Depends(admin)])
    async def automation_list():
        return store.list("automations")

    @admin_api.get("/settlements", dependencies=[Depends(admin)])
    async def settlement_history():
        # Retain visibility into uncertain payouts even after the month changes
        # or the originating rule has been removed.
        if not config.sub2api_url:
            return []
        instance = hashlib.sha256(config.sub2api_url.rstrip("/").encode()).hexdigest()[:24]
        prefix = f"automation:monthly_rewards:{instance}:"
        fields = {"user_id", "rank", "amount", "actual_cost", "month", "status", "reason", "error", "attempted_at", "completed_at"}
        rows = []
        for snapshot in store.ledger_list(prefix).values():
            rows.append({**{k: snapshot.get(k) for k in ("month", "start_date", "end_date", "timezone", "rule_id")},
                         "actions": [{k: v for k, v in action.items() if k in fields} for action in snapshot.get("actions", [])]})
        return sorted(rows, key=lambda row: row.get("month", ""), reverse=True)

    @admin_api.post("/automations", dependencies=[Depends(admin)])
    async def automation_create(value: Automation):
        async with mutations:
            await check_automation(value)
            result = store.create("automations", automation_data(value))
            store.audit("automation.create", str(result["id"]), detail=f"{value.type}: {value.name}")
            return result

    @admin_api.put("/automations/{item_id}", dependencies=[Depends(admin)])
    async def automation_update(item_id: str, value: Automation):
        async with mutations:
            old = existing("automations", item_id)
            if old["type"] != value.type:
                raise HTTPException(409, "规则类型不能更改，请新建规则")
            await check_automation(value, item_id)
            changed = any(old.get(field) != value.model_dump(mode="json").get(field)
                          for field in ("enabled", "group_id", "scope", "multiplier", "remaining_fraction_gt", "quota_period", "expires_within_days", "restore_on_exit"))
            if changed:
                await cleanup_automation(old)
            result = store.update("automations", item_id, automation_data(value, old))
            store.audit("automation.update", item_id, detail=value.name)
            return result

    @admin_api.delete("/automations/{item_id}", dependencies=[Depends(admin)])
    async def automation_delete(item_id: str):
        async with mutations:
            old = existing("automations", item_id)
            await cleanup_automation(old)
            store.delete("automations", item_id)
            store.audit("automation.delete", item_id)
            return {"ok": True}

    @admin_api.post("/automations/{item_id}/preview", dependencies=[Depends(admin)])
    async def automation_preview(item_id: str, request: Request):
        limit(request, "automation.preview", 10)
        async with mutations:
            return await engine.automation.preview(existing("automations", item_id))

    @admin_api.post("/automations/{item_id}/run", dependencies=[Depends(admin)])
    async def automation_run(item_id: str, request: Request):
        limit(request, "automation.run", 5)
        async with mutations:
            rule = existing("automations", item_id)
            if not rule.get("enabled"):
                raise HTTPException(409, "请先启用规则；停用规则仅支持预览")
            result = await engine.automation.run(rule, manual=True)
            store.audit("automation.manual_run", item_id, detail=str(result.get("executed", 0)))
            return result

    @admin_api.get("/analytics/models", dependencies=[Depends(admin)])
    async def models(start_date: str | None = None, end_date: str | None = None):
        return await upstream.models(*dates(start_date, end_date))

    @admin_api.get("/analytics/ranking", dependencies=[Depends(admin)])
    async def ranking(start_date: str | None = None, end_date: str | None = None):
        return await upstream.ranking(*dates(start_date, end_date))

    async def do_benchmark(value, identity):
        if identity in benchmark_users or benchmark_slots.locked():
            raise HTTPException(429, "模型测试正在进行，请稍后再试")
        benchmark_users.add(identity)
        try:
            async with benchmark_slots:
                result = await run_benchmark(config, value)
                store.audit("benchmark.run", str(identity), detail=f"model={value.model}, score={result['score']}/{result['total']}")
                return result
        finally:
            benchmark_users.discard(identity)

    @admin_api.post("/benchmark", dependencies=[Depends(admin)])
    async def benchmark(value: BenchmarkSelection, request: Request):
        limit(request, "benchmark", 3, 300)
        key = await upstream.benchmark_key(value.key_id, value.group_id)
        resolved = Benchmark(model=value.model, api_key=key, api_format=value.api_format)
        return await do_benchmark(resolved, "admin")

    @admin_api.get("/benchmark/options", dependencies=[Depends(admin)])
    async def benchmark_options():
        return {"keys": await upstream.benchmark_keys()}

    @portal_api.post("/session")
    async def portal_session(value: Session, request: Request):
        limit(request, "login", 20)
        settings = await upstream.settings()
        if settings.get("session_binding_enabled"):
            raise HTTPException(409, "上游启用了会话绑定，助手不能安全代理验证；请使用管理端功能")
        profile = validate_profile(await upstream.profile(value.token))
        now = time.monotonic()
        for key in list(sessions):
            if sessions[key]["expires"] < now:
                del sessions[key]
        if len(sessions) >= 2048:
            raise HTTPException(503, "会话已满，请稍后再试")
        session = secrets.token_urlsafe(32)
        sessions[session] = {"token": value.token, "expires": now + 3600}
        return {"session": session, "username": profile.get("username"), "role": profile.get("role")}

    @portal_api.get("/modules")
    async def portal_modules(identity=Depends(portal)):
        return Modules(**store.get_setting("modules", {})).model_dump()

    @portal_api.get("/models")
    async def portal_models(start_date: str | None = None, end_date: str | None = None, identity=Depends(portal)):
        enabled("pricing")
        return await upstream.models(*dates(start_date, end_date), token=identity["token"])

    @portal_api.get("/ranking")
    async def portal_ranking(start_date: str | None = None, end_date: str | None = None, identity=Depends(portal)):
        enabled("ranking")
        result = await upstream.ranking(*dates(start_date, end_date))
        if identity["profile"].get("role") == "admin":
            return result
        # Explicit projection, never return upstream totals or unknown future fields.
        return {"ranking": [
            {"rank": i + 1, "username": f"用户 #{i+1}",
             "requests": item.get("requests", 0), "tokens": item.get("tokens", 0),
             "actual_cost": item.get("actual_cost", 0),
             "is_me": item.get("user_id") == identity["profile"].get("id")}
            for i, item in enumerate(result.get("ranking", []))]}

    @portal_api.post("/benchmark")
    async def portal_benchmark(value: BenchmarkSelection, request: Request, identity=Depends(portal)):
        enabled("benchmark")
        limit(request, "benchmark", 3, 300)
        key = await upstream.user_benchmark_key(identity["token"], value.key_id, value.group_id)
        resolved = Benchmark(model=value.model, api_key=key, api_format=value.api_format)
        return await do_benchmark(resolved, identity["profile"]["id"])

    @portal_api.get("/benchmark/options")
    async def portal_benchmark_options(identity=Depends(portal)):
        enabled("benchmark")
        return {"keys": await upstream.user_benchmark_keys(identity["token"])}

    async def check_compatibility():
        checks = []
        for name, path in [("账号调度字段", "/api/v1/admin/accounts"), ("用户实际使用时间", "/api/v1/admin/users"),
                           ("自定义菜单设置", "/api/v1/admin/settings"), ("上游版本", "/api/v1/admin/system/version")]:
            try:
                data = await upstream.request("GET", path, params={"page": 1, "page_size": 1, "lite": "true"})
                if "accounts" in path:
                    items = data.get("items", [])
                    ok = not items or {"id", "schedulable"} <= items[0].keys()
                elif "users" in path:
                    items = data.get("items", [])
                    ok = not items or {"id", "last_used_at", "role"} <= items[0].keys()
                elif path.endswith("settings"):
                    ok = isinstance(data.get("custom_menu_items"), list)
                    if data.get("session_binding_enabled"):
                        checks.append({"name": "内嵌用户身份验证", "ok": False, "detail": "上游会话绑定已启用，内嵌页面验证已阻止"})
                else:
                    ok = isinstance(data.get("version"), str)
                checks.append({"name": name, "ok": ok, "detail": "只读接口验证通过（不证明写权限）" if ok else "响应结构与基线不同"})
            except (UpstreamError, AttributeError, TypeError, KeyError):
                checks.append({"name": name, "ok": False, "detail": "接口不可用、权限不足或响应结构不兼容"})
        latest = None
        latest_version = None
        latest_tag = None
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get("https://api.github.com/repos/Wei-Shaw/sub2api/releases/latest",
                                            headers={"Accept": "application/vnd.github+json"})
                response.raise_for_status()
                release = response.json()
                tag = release["tag_name"]
                if release.get("draft") or release.get("prerelease") or not isinstance(tag, str) or not re.fullmatch(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag):
                    raise ValueError("Unsupported release tag")
                commit = await client.get("https://api.github.com/repos/Wei-Shaw/sub2api/commits/" + tag,
                                          headers={"Accept": "application/vnd.github+json"})
                commit.raise_for_status()
                candidate = commit.json()["sha"]
                if isinstance(candidate, str) and len(candidate) == 40 and all(c in "0123456789abcdef" for c in candidate):
                    latest = candidate
                    latest_tag = tag
                    latest_version = tag.removeprefix("v")
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            checks.append({"name": "上游更新查询", "ok": False, "detail": "GitHub 查询失败或被限流；未确认最新版本"})
        result = {"checked_at": datetime.now(timezone.utc).isoformat(), "upstream_commit": BASELINE,
                  "latest_commit": latest, "latest_tag": latest_tag, "latest_version": latest_version,
                  "update_available": (latest != BASELINE or latest_version != __version__) if latest else None,
                  "checks": checks, "notice": "基线是已审核的源码提交，不是当前部署 SHA。检测不会自动升级或证明新版本的写操作兼容。"}
        store.set_setting("compatibility", result)
        return result

    async def compatibility_loop():
        while True:
            try:
                if config.sub2api_url and config.sub2api_admin_key:
                    await check_compatibility()
            except Exception:
                store.audit("compatibility.check", "upstream", status="error", detail="检查失败，稍后重试")
            await asyncio.sleep(6 * 3600)

    @admin_api.get("/compatibility", dependencies=[Depends(admin)])
    async def compatibility():
        return store.get_setting("compatibility", {"checks": [], "upstream_commit": BASELINE, "notice": "尚未检查"})

    @admin_api.post("/compatibility/check", dependencies=[Depends(admin)])
    async def compatibility_check(request: Request):
        limit(request, "compatibility", 2)
        return await check_compatibility()

    install_native_api(app, store, upstream, mailer, engine, lambda: config, native_admin, limit)
    install_gallery_api(admin_api, portal_api, gallery, upstream, lambda: config, admin, portal, enabled, limit)
    app.include_router(admin_api, prefix="/_helper/api/admin")
    app.include_router(portal_api, prefix="/_helper/api/portal")
    app.mount("/_helper/static", StaticFiles(directory=STATIC), name="static")

    if config.legacy_routes:
        # Compatibility is deliberate and opt-in; fresh deployments reserve
        # only /_helper. Keep auth, payloads, and persisted menu links intact.
        app.include_router(admin_api, prefix="/api")
        app.include_router(portal_api, prefix="/api/portal")
        app.add_api_route("/healthz", health, methods=["GET"])
        app.add_api_route("/features/{module}", feature, methods=["GET"])
        app.mount("/static", StaticFiles(directory=STATIC), name="legacy-static")

        @app.get("/")
        async def legacy_index(request: Request):
            if config.native_ui and config.sub2api_url and is_document_request(request, ""):
                return await native_proxy.forward_document(request, "")
            return FileResponse(STATIC / "index.html")

    @app.get("/{path:path}")
    async def native_document(request: Request, path: str):
        if (path == "_helper" or path.startswith("_helper/") or not config.native_ui
                or not config.sub2api_url or not is_document_request(request, path)):
            raise HTTPException(404)
        return await native_proxy.forward_document(request, path)

    @app.api_route("/{path:path}", methods=["HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "CONNECT"])
    async def unmapped_request(path: str):
        # The ingress must send native APIs, assets, uploads, and all WebSockets
        # directly to sub2api. Unknown helper paths also stop here locally.
        raise HTTPException(404)
    return app
