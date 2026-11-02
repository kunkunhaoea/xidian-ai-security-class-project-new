"""Authenticated course-workbench integration, using 03's existing services."""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from fastapi.responses import FileResponse

from deeptutor.services.workbench import shared_discussion
from deeptutor.services.workspace import get_content_workspace_service
from deeptutor.services.workspace.context import workspace_context
from deeptutor.services.session import get_session_store

router = APIRouter()
_connect_lock = asyncio.Lock()


class Connect(BaseModel):
    topic_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    handoff_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class Archive(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=100)
    workspace_item_id: str = Field(pattern=r"^wsi_[a-f0-9]{32}$")
    kind: Literal["notes", "plans", "writing"]
    title: str = Field(default="", max_length=500)
    confirmed: Literal[True]


class ReferencesUpdate(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=100)
    paths: list[str] = Field(max_length=30)


def session_references(session_id, workspace_id):
    bridge = shared_discussion()
    binding = get_content_workspace_service().binding_by_id(workspace_id)
    topic = bridge.topic_for_root(binding.root)
    record = bridge.references(topic, session_id)
    if not record:
        raise ValueError("该会话没有共享资料引用")
    return bridge, topic, record


@router.get("/status")
async def status():
    return {"status": "ready", "bridge_version": 1, "model_called": False}


@router.post("/connect")
async def connect(payload: Connect):
    bridge = shared_discussion()
    try:
        async with _connect_lock:
            handoff = bridge.load(payload.topic_id, "handoffs", payload.handoff_id)
            if not handoff or handoff["topic_id"] != payload.topic_id:
                raise ValueError("交接记录不存在或不属于当前课题，请返回工作台重新选择")
            session_id = "wb_" + payload.handoff_id
            previous = bridge.references(payload.topic_id, session_id)
            paths = bridge.selected(payload.topic_id, previous["paths"] if previous else handoff["paths"])
            service = get_content_workspace_service()
            topic = bridge.store.info(payload.topic_id)
            # Native catalog serializes same-directory registration/reuses it.
            registration = service.create_workspace(topic["name"][:100], path=str(bridge.store.topic(payload.topic_id).resolve()))
            workspace_id = registration["workspace_id"]
            with workspace_context(workspace_id):
                store = get_session_store()
                session = await store.get_session(session_id)
                if not session:
                    session = await store.create_session(title=topic["name"][:100], session_id=session_id)
                from adapters.shared_files import atomic_json, now
                record = bridge.references(payload.topic_id, session_id) or {"topic_id": payload.topic_id, "workspace_id": workspace_id, "session_id": session_id,
                          "paths": paths, "created_at": handoff["created_at"], "connected_at": now()}
                atomic_json(bridge.private(payload.topic_id, "sessions", session_id), record)
            return {**record, "route": f"/chat/{session_id}?dt_workspace={workspace_id}"}
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/sessions/{session_id}")
async def references(session_id: str, workspace_id: str):
    try:
        bridge = shared_discussion()
        binding = get_content_workspace_service().binding_by_id(workspace_id)
        topic = bridge.topic_for_root(binding.root)
        record = bridge.references(topic, session_id)
        if not record:
            return {"linked": False}
        with workspace_context(workspace_id):
            session = await get_session_store().get_session(session_id)
            if not session:
                raise ValueError("当前工作区中没有该会话")
        from urllib.parse import urlencode, urlsplit
        import os
        base = os.environ.get("WORKBENCH_PUBLIC_URL", "http://127.0.0.1:8080").rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("WORKBENCH_PUBLIC_URL配置无效")
        files = []
        for relative in record["paths"]:
            try:
                path = bridge.store.path(topic, relative)
                files.append({"path": relative, "filename": path.name, "size": path.stat().st_size,
                              "url": f"/api/workbench-bridge/sessions/{session_id}/file?" + urlencode({"workspace_id": workspace_id, "dt_workspace": workspace_id, "path": relative})})
            except (ValueError, OSError) as exc:
                files.append({"path": relative, "filename": relative.split("/")[-1], "error": str(exc)})
        return {"linked": True, **record, "files": files, "return_url": base + "/workbench/discussion?" + urlencode({"topic": topic}),
                "scope": "当前课题工作区；选定资料是本轮默认参考范围，不是文件级沙箱"}
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/sessions/{session_id}/references")
async def update_references(session_id: str, payload: ReferencesUpdate):
    try:
        bridge, topic, record = session_references(session_id, payload.workspace_id)
        record["paths"] = bridge.selected(topic, payload.paths)
        from adapters.shared_files import atomic_json
        atomic_json(bridge.private(topic, "sessions", session_id), record)
        return await references(session_id, payload.workspace_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/sessions/{session_id}/file")
async def reference_file(session_id: str, workspace_id: str, path: str):
    try:
        bridge, topic, record = session_references(session_id, workspace_id)
        if path not in record["paths"]:
            raise ValueError("该文件不在当前会话的附件引用中")
        source = bridge.store.path(topic, path)
        if not source.is_file():
            raise ValueError("文件已不存在，请移除该引用")
        return FileResponse(source, filename=source.name, content_disposition_type="inline", headers={"Cache-Control": "no-store"})
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/archive")
async def archive(payload: Archive):
    # Explicit UI confirmation authorizes this exact outputs -> topic export.
    # Apply the same account policy used by native data export, then invoke
    # native validate_export/export_once rather than bypassing outputs grants.
    from deeptutor.api.routers.workspace import _assert_migration_access

    _assert_migration_access()
    try:
        bridge = shared_discussion()
        service = get_content_workspace_service()
        binding = service.binding_by_id(payload.workspace_id)
        topic = bridge.topic_for_root(binding.root)
        with workspace_context(payload.workspace_id):
            _, item = service.resolve_published_item(payload.workspace_id, payload.workspace_item_id)
        source = bridge.store.path(topic, item.relative_path)
        if source.stat().st_size > 15 * 1024 * 1024:
            raise ValueError("单个归档文件不能超过15MB")
        import hashlib
        if hashlib.sha256(source.read_bytes()).hexdigest() != item.sha256:
            raise ValueError("原始生成文件已改变，请重新展示并确认后归档")
        parts = source.relative_to(binding.root).parts
        if len(parts) < 5 or parts[0] != "outputs":
            raise ValueError("只能归档outputs中的生成文件")
        with workspace_context(payload.workspace_id):
            session = await get_session_store().get_session(parts[2])
            if not session:
                raise ValueError("找不到生成文件所属会话")
        def export(origin, destination, sha):
            service.export_once(binding, source_path=origin, destination_path=destination, expected_sha256=sha)
        return bridge.archive(topic, item.relative_path, payload.kind, payload.title,
                              session_id=parts[2], turn_id=parts[3], export=export)
    except (ValueError, OSError, UnicodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
