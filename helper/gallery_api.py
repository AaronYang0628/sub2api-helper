"""Administrator configuration and access to generated works in a user's groups."""

import asyncio
from typing import Literal

from fastapi import Depends, HTTPException, Path, Request
from pydantic import Field, field_validator

from .schemas import Input
from .upstream import UpstreamError


class GalleryJob(Input):
    name: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=150)
    key_id: int = Field(gt=0)
    prompt: str = Field(min_length=1, max_length=8000)
    api_format: Literal["chat", "responses"] = "chat"
    reasoning_effort: Literal["default", "none", "minimal", "low", "medium", "high", "xhigh", "max"] = "default"
    interval_minutes: int = Field(default=30, ge=5, le=1440)
    enabled: bool = False


class GalleryComparison(Input):
    group_ids: list[int] = Field(min_length=2, max_length=32)
    prompt: str = Field(min_length=1, max_length=8000)
    interval_minutes: int = Field(default=30, ge=5, le=1440)
    enabled: bool = False

    @field_validator("group_ids")
    @classmethod
    def valid_groups(cls, values):
        if any(not 0 < value < 2**63 for value in values) or len(set(values)) != len(values):
            raise ValueError("请选择不同的有效分组")
        return values


PUBLIC_GROUP = ("group_id", "name", "model", "reasoning_effort", "enabled", "interval_minutes", "next_at", "running",
                "comparison_member", "comparison_enabled", "comparison_next_at")
PUBLIC_WORK = ("id", "group_id", "round_id", "title", "model", "reasoning_effort", "created_at", "finished_at", "latency_ms",
               "status", "kind", "preview", "content", "mime", "error")


def project_group(group):
    return {key: group.get(key) for key in PUBLIC_GROUP}


def project_work(work, detail=False):
    fields = (*PUBLIC_WORK, "prompt") if detail else PUBLIC_WORK
    return {key: work[key] for key in fields if key in work}


def project_comparison(data, allowed):
    config = data.get("config")
    visible_config = None
    if config:
        group_ids = [group_id for group_id in config["group_ids"] if allowed is None or group_id in allowed]
        if group_ids:
            visible_config = {key: config.get(key) for key in ("enabled", "interval_minutes", "next_at", "running")}
            visible_config["group_ids"] = group_ids
    source = data.get("round")
    visible_round = None
    if source:
        items = []
        for item in source["items"]:
            if allowed is not None and item["group_id"] not in allowed:
                continue
            safe = {key: item.get(key) for key in ("group_id", "name", "model", "reasoning_effort", "status")}
            if isinstance(item.get("work"), dict):
                safe["work"] = project_work(item["work"])
            items.append(safe)
        if items:
            states = {item["status"] for item in items}
            if allowed is None:
                status = source["status"]
            elif states.intersection({"queued", "running"}):
                status = "running"
            elif len(states) == 1:
                status = states.pop()
            else:
                status = "partial"
            visible_round = {key: source.get(key) for key in ("id", "prompt", "created_at", "finished_at")}
            visible_round.update(status=status, items=items)
    if visible_config:
        visible_config["running"] = bool(visible_round and visible_round["status"] == "running")
    return {"config": visible_config, "round": visible_round}


def install_gallery_api(admin_api, portal_api, gallery, upstream, config_provider, admin, portal, enabled, limit):
    async def visible_groups(identity):
        enabled("benchmark")
        if identity["profile"]["role"] == "admin":
            return None
        return set(await upstream.available_groups(identity["token"]))

    async def check_access(identity, group_id):
        allowed = await visible_groups(identity)
        if allowed is not None and group_id not in allowed:
            raise HTTPException(404, "当前分组没有可用的作品")

    @admin_api.get("/gallery", dependencies=[Depends(admin)])
    async def list_gallery_jobs():
        return {"groups": gallery.groups(), "timezone": config_provider().timezone}

    @admin_api.get("/gallery/options", dependencies=[Depends(admin)])
    async def gallery_options():
        # Keep the group directory visible even when the admin has not created
        # model keys yet or a scoped management key cannot read one catalog.
        async def catalog(reader):
            try:
                return await reader(), None
            except UpstreamError as failure:
                return [], failure.detail

        (groups, group_error), (keys, key_error) = await asyncio.gather(
            catalog(upstream.list_groups), catalog(upstream.benchmark_keys))
        return {"groups": [{key: row.get(key) for key in ("id", "name", "platform", "status")} for row in groups],
                "keys": keys, "group_error": group_error, "key_error": key_error}

    @admin_api.get("/gallery/comparison", dependencies=[Depends(admin)])
    async def get_comparison():
        return {**gallery.comparison(), "timezone": config_provider().timezone}

    @admin_api.put("/gallery/comparison", dependencies=[Depends(admin)])
    async def save_comparison(value: GalleryComparison, request: Request):
        limit(request, "gallery.configure", 15)
        jobs = {group["group_id"]: group for group in gallery.groups()}
        if any(group_id not in jobs for group_id in value.group_ids):
            raise HTTPException(422, "请先为所有参与对比的分组保存模型和 Key")
        options = await upstream.benchmark_keys()
        live = {(key["group_id"], key["id"]) for key in options}
        if any((group_id, jobs[group_id]["key_id"]) not in live for group_id in value.group_ids):
            raise HTTPException(422, "部分参与分组的模型 Key 已失效，请更换后保存")
        return gallery.configure_comparison(value.model_dump())

    @admin_api.post("/gallery/comparison/run", status_code=202, dependencies=[Depends(admin)])
    async def run_comparison(request: Request):
        limit(request, "gallery.run", 5)
        return await gallery.start_comparison()

    @admin_api.post("/gallery/comparison/pause", dependencies=[Depends(admin)])
    async def pause_comparison():
        return gallery.pause_comparison()

    @admin_api.delete("/gallery/comparison", dependencies=[Depends(admin)])
    async def clear_comparison():
        return gallery.clear_comparison()

    @admin_api.get("/gallery/{group_id}", dependencies=[Depends(admin)])
    async def get_gallery_job(group_id: int = Path(gt=0)):
        return gallery.view(group_id)

    @admin_api.put("/gallery/{group_id}", dependencies=[Depends(admin)])
    async def save_gallery_job(value: GalleryJob, request: Request, group_id: int = Path(gt=0)):
        limit(request, "gallery.configure", 15)
        await upstream.benchmark_key(value.key_id, group_id)
        return gallery.configure(group_id, value.model_dump())

    @admin_api.post("/gallery/{group_id}/pause", dependencies=[Depends(admin)])
    async def pause_gallery_job(group_id: int = Path(gt=0)):
        # Stopping billing must work even if the selected model key was revoked.
        group = gallery.view(group_id)["group"]
        config = {key: group[key] for key in GalleryJob.model_fields}
        config["enabled"] = False
        return gallery.configure(group_id, config)

    @admin_api.post("/gallery/{group_id}/run", status_code=202, dependencies=[Depends(admin)])
    async def run_gallery_job(request: Request, group_id: int = Path(gt=0)):
        limit(request, "gallery.run", 5)
        return await gallery.start(group_id)

    @admin_api.get("/gallery/{group_id}/works/{work_id}", dependencies=[Depends(admin)])
    async def get_gallery_work(group_id: int = Path(gt=0), work_id: int = Path(gt=0)):
        return gallery.work(group_id, work_id)

    @portal_api.get("/gallery")
    async def portal_gallery(identity=Depends(portal)):
        allowed = await visible_groups(identity)
        return {"groups": [project_group(group) for group in gallery.groups()
                           if allowed is None or group["group_id"] in allowed],
                "timezone": config_provider().timezone}

    @portal_api.get("/gallery/comparison")
    async def portal_comparison(identity=Depends(portal)):
        allowed = await visible_groups(identity)
        return {**project_comparison(gallery.comparison(), allowed), "timezone": config_provider().timezone}

    @portal_api.get("/gallery/{group_id}")
    async def portal_gallery_group(group_id: int = Path(gt=0), identity=Depends(portal)):
        await check_access(identity, group_id)
        view = gallery.view(group_id)
        return {"group": project_group(view["group"]), "stats": view["stats"],
                "works": [project_work(work) for work in view["works"]]}

    @portal_api.get("/gallery/{group_id}/works/{work_id}")
    async def portal_gallery_work(group_id: int = Path(gt=0), work_id: int = Path(gt=0), identity=Depends(portal)):
        await check_access(identity, group_id)
        return project_work(gallery.work(group_id, work_id), detail=True)
