"""Administrator configuration and access to generated works in a user's groups."""

from typing import Literal

from fastapi import Depends, HTTPException, Path, Request
from pydantic import Field

from .schemas import Input


class GalleryJob(Input):
    name: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=150)
    key_id: int = Field(gt=0)
    prompt: str = Field(min_length=1, max_length=8000)
    api_format: Literal["chat", "responses"] = "chat"
    interval_minutes: int = Field(default=30, ge=5, le=1440)
    enabled: bool = False


PUBLIC_GROUP = ("group_id", "name", "model", "enabled", "interval_minutes", "next_at", "running")
PUBLIC_WORK = ("id", "group_id", "title", "model", "created_at", "finished_at", "latency_ms", "status", "kind", "preview", "content", "mime", "error")


def project_group(group):
    return {key: group.get(key) for key in PUBLIC_GROUP}


def project_work(work, detail=False):
    fields = (*PUBLIC_WORK, "prompt") if detail else PUBLIC_WORK
    return {key: work[key] for key in fields if key in work}


def install_gallery_api(app, gallery, upstream, config_provider, admin, portal, enabled, limit):
    async def visible_groups(identity):
        enabled("benchmark")
        if identity["profile"]["role"] == "admin":
            return None
        return set(await upstream.available_groups(identity["token"]))

    async def check_access(identity, group_id):
        allowed = await visible_groups(identity)
        if allowed is not None and group_id not in allowed:
            raise HTTPException(404, "当前分组没有可用的作品")

    @app.get("/api/gallery", dependencies=[Depends(admin)])
    async def list_gallery_jobs():
        return {"groups": gallery.groups(), "timezone": config_provider().timezone}

    @app.get("/api/gallery/{group_id}", dependencies=[Depends(admin)])
    async def get_gallery_job(group_id: int = Path(gt=0)):
        return gallery.view(group_id)

    @app.put("/api/gallery/{group_id}", dependencies=[Depends(admin)])
    async def save_gallery_job(value: GalleryJob, request: Request, group_id: int = Path(gt=0)):
        limit(request, "gallery.configure", 15)
        await upstream.benchmark_key(value.key_id, group_id)
        return gallery.configure(group_id, value.model_dump())

    @app.post("/api/gallery/{group_id}/pause", dependencies=[Depends(admin)])
    async def pause_gallery_job(group_id: int = Path(gt=0)):
        # Stopping billing must work even if the selected model key was revoked.
        group = gallery.view(group_id)["group"]
        config = {key: group[key] for key in GalleryJob.model_fields}
        config["enabled"] = False
        return gallery.configure(group_id, config)

    @app.post("/api/gallery/{group_id}/run", status_code=202, dependencies=[Depends(admin)])
    async def run_gallery_job(request: Request, group_id: int = Path(gt=0)):
        limit(request, "gallery.run", 5)
        return await gallery.start(group_id)

    @app.get("/api/gallery/{group_id}/works/{work_id}", dependencies=[Depends(admin)])
    async def get_gallery_work(group_id: int = Path(gt=0), work_id: int = Path(gt=0)):
        return gallery.work(group_id, work_id)

    @app.get("/api/portal/gallery")
    async def portal_gallery(identity=Depends(portal)):
        allowed = await visible_groups(identity)
        return {"groups": [project_group(group) for group in gallery.groups()
                           if allowed is None or group["group_id"] in allowed],
                "timezone": config_provider().timezone}

    @app.get("/api/portal/gallery/{group_id}")
    async def portal_gallery_group(group_id: int = Path(gt=0), identity=Depends(portal)):
        await check_access(identity, group_id)
        view = gallery.view(group_id)
        return {"group": project_group(view["group"]), "stats": view["stats"],
                "works": [project_work(work) for work in view["works"]]}

    @app.get("/api/portal/gallery/{group_id}/works/{work_id}")
    async def portal_gallery_work(group_id: int = Path(gt=0), work_id: int = Path(gt=0), identity=Depends(portal)):
        await check_access(identity, group_id)
        return project_work(gallery.work(group_id, work_id), detail=True)
