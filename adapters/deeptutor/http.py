"""Small 04-side handoff/status boundary; never imports the 03 application."""
from __future__ import annotations

import os
from urllib.parse import urlencode, urlsplit
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from adapters.deeptutor.files import SharedDiscussion


def service_urls():
    frontend = os.environ.get("WORKBENCH_DEEPTUTOR_FRONTEND_URL", "http://127.0.0.1:3782").rstrip("/")
    api = os.environ.get("WORKBENCH_DEEPTUTOR_API_URL", frontend).rstrip("/")
    for value in (frontend, api):
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("请配置不含凭据或查询参数的DeepTutor服务地址")
    return frontend, api


class Handoff(BaseModel):
    paths: list[str] = Field(default_factory=list, max_length=30)


def create_router(store):
    router = APIRouter(prefix="/api/workbench/deeptutor", tags=["workbench-deeptutor"])

    @router.get("/status")
    async def status():
        try:
            frontend, api = service_urls()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        result = {"frontend_url": frontend, "api_url": api, "available": False, "state": "unavailable", "detail": ""}
        try:
            async with httpx.AsyncClient(timeout=4, trust_env=False) as client:
                response = await client.get(api + "/api/workbench-bridge/status")
                if response.status_code in {401, 403}:
                    result.update(available=True, state="login_required", detail="03需要登录或管理员权限，请在03页面完成登录。")
                elif response.is_success and response.json().get("bridge_version") == 1:
                    # A cold Next development route can take much longer than
                    # a status probe to compile. Probe a static asset instead;
                    # this means reachable, not "all embedded UI verified".
                    page = await client.get(frontend + "/favicon-16x16.png")
                    result.update(available=page.status_code < 400, state="ready" if page.status_code < 400 else "frontend_unavailable", detail="" if page.status_code < 400 else "03后端已连接，前端入口不可访问，请检查03前端地址及构建。")
                else:
                    result.update(state="bridge_unavailable", detail="03接入接口不可用，请确认03已加载本轮代码并重启服务。")
        except (httpx.HTTPError, ValueError):
            result["detail"] = "无法连接03服务；请启动DeepTutor，并检查前端地址、后端地址和03自己的API代理设置。"
        return result

    @router.post("/topics/{topic_id}/handoffs", status_code=201)
    async def handoff(topic_id: str, payload: Handoff):
        try:
            record = SharedDiscussion(store).handoff(topic_id, payload.paths)
            frontend, _ = service_urls()
            return {**record, "url": frontend + "/workbench-connect?" + urlencode({"topic": topic_id, "handoff": record["id"]})}
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    return router
