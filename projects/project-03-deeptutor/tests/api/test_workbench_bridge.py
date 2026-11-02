"""Offline real-store/workspace/HTTP tests; model boundary is mocked."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from deeptutor.services.workbench import shared_discussion
from deeptutor.multi_user.models import CurrentUser, UserScope
from deeptutor.multi_user.paths import user_context, get_account_path_service
from deeptutor.services.workspace import ContentWorkspaceService
from deeptutor.services.workspace.context import workspace_context
from deeptutor.services.session import get_session_store


@pytest.fixture
def setup(tmp_path, monkeypatch):
    import deeptutor.services.workbench as integration
    import deeptutor.services.workspace as workspaces
    import deeptutor.services.workspace.service as filesystem
    import deeptutor.api.routers.workbench as api
    prototype = shared_discussion()
    from adapters.shared_files import TopicStore

    bridge = type(prototype)(TopicStore(tmp_path / "shared"))
    bridge.store.create("alpha", "甲课题")
    bridge.store.create("beta", "乙课题")
    bridge.store.write_new("alpha", "materials/m001", "正文.md", "FIRST_CONTENT")
    bridge.store.write_new("beta", "materials/m001", "正文.md", "OTHER_TOPIC_SECRET")
    monkeypatch.setattr(integration, "shared_discussion", lambda: bridge)
    monkeypatch.setattr(api, "shared_discussion", lambda: bridge)
    monkeypatch.setattr(integration, "SHARED_TOPICS", bridge.store.base)
    monkeypatch.setattr(filesystem, "_SHARED_TOPICS", bridge.store.base)
    monkeypatch.delenv("DEEPTUTOR_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("DEEPTUTOR_WORKSPACE_ALLOWED_ROOTS", raising=False)
    user = CurrentUser("workbench-test", "test", "admin", UserScope("admin", "workbench-test", tmp_path / "account"))
    with user_context(user):
        get_account_path_service().ensure_all_directories()
        service = ContentWorkspaceService()
        monkeypatch.setattr(workspaces, "get_content_workspace_service", lambda: service)
        monkeypatch.setattr(api, "get_content_workspace_service", lambda: service)
        app = FastAPI()
        app.include_router(api.router, prefix="/api/workbench-bridge")
        yield bridge, service, app


async def connect(bridge, app, topic="alpha", paths=None, handoff=None):
    handoff = handoff or bridge.handoff(topic, paths or [])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/workbench-bridge/connect", json={"topic_id": topic, "handoff_id": handoff["id"]})
    assert response.status_code == 200, response.text
    return response.json(), handoff


@pytest.mark.asyncio
async def test_binding_reuse_rename_refresh_no_messages_and_scope(setup):
    bridge, service, app = setup
    old_root = service.current_binding().root
    a, handoff = await connect(bridge, app, paths=["materials/m001/正文.md"])
    refresh, _ = await connect(bridge, app, handoff=handoff)
    second, _ = await connect(bridge, app)
    b, _ = await connect(bridge, app, topic="beta")
    assert a["session_id"] == refresh["session_id"] != second["session_id"]
    assert a["workspace_id"] == refresh["workspace_id"] == second["workspace_id"] != b["workspace_id"]
    assert service.current_binding().root == old_root
    assert service.binding_by_id(a["workspace_id"]).root == bridge.store.topic("alpha")
    bridge.store.path("alpha", "README.md").write_text("# 改名\n", encoding="utf-8")
    renamed, _ = await connect(bridge, app)
    assert renamed["workspace_id"] == a["workspace_id"]
    with workspace_context(a["workspace_id"]):
        assert (await get_session_store().get_session_with_messages(a["session_id"]))["messages"] == []
    with workspace_context(b["workspace_id"]):
        assert await get_session_store().get_session(a["session_id"]) is None


@pytest.mark.asyncio
async def test_current_text_reaches_actual_turn_and_model_message_builder(setup, monkeypatch):
    bridge, service, app = setup
    a, _ = await connect(bridge, app, paths=["materials/m001/正文.md", "materials/m001/正文.md"])
    from deeptutor.core.stream import StreamEvent, StreamEventType
    from deeptutor.services.session.turn_runtime import TurnRuntimeManager
    from deeptutor.agents.loop import pipeline
    captured = []
    config = SimpleNamespace(model="offline-model", binding="openai", api_key="fake", base_url="http://unused.invalid")
    monkeypatch.setattr("deeptutor.services.llm.config.get_llm_config", lambda: config)
    monkeypatch.setattr(pipeline, "get_llm_config", lambda: config)
    class Builder:
        async def build(self, **kwargs):
            return SimpleNamespace(conversation_history=[], conversation_summary="", context_text="", token_count=0, budget=0)
    class Engine:
        async def execute(self, context):
            # Real downstream prompt assembler, intercepted before provider I/O.
            host = pipeline.AgenticLoopPipeline(language="zh")
            messages = host._build_loop_messages(context=context, enabled_tools=[])
            captured.append(json.dumps(messages, ensure_ascii=False))
            assert context.knowledge_bases == []
            yield StreamEvent(type=StreamEventType.CONTENT, source="chat", content="模拟回答", metadata={"call_kind": "llm_final_response"})
            yield StreamEvent(type=StreamEventType.DONE, source="chat")
    async def noop(*args, **kwargs):
        pass
    monkeypatch.setattr(TurnRuntimeManager, "_create_context_builder", lambda self: Builder())
    monkeypatch.setattr(TurnRuntimeManager, "_maybe_generate_session_title", noop)
    with workspace_context(a["workspace_id"]):
        runtime = TurnRuntimeManager(get_session_store(), turn_engine=Engine())
        for marker in ("EDITED_NOW_ONE", "EDITED_NOW_TWO"):
            bridge.store.path("alpha", "materials/m001/正文.md").write_text(marker, encoding="utf-8")
            _, turn = await runtime.start_turn({"session_id": a["session_id"], "workspace_id": a["workspace_id"], "content": "请解释选定资料", "tools": [], "language": "zh", "auto_route": False})
            await asyncio.wait_for(runtime._executions[turn["id"]].task, 20)
            saved = await get_session_store().get_turn(turn["id"])
            assert saved["status"] == "completed", saved
            assert marker in captured[-1]
            assert "OTHER_TOPIC_SECRET" not in captured[-1]
            snapshot = bridge.load("alpha", "turns", turn["id"])
            assert len(snapshot["documents"]) == 1 and marker in snapshot["documents"][0]["text"]
        with pytest.raises(RuntimeError, match="普通对话"):
            await runtime.start_turn({"session_id": a["session_id"], "content": "research", "capability": "deep_research"})
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["notes", "plans", "writing"])
async def test_real_save_present_archive_preserves_source_and_manual_edits_without_llm(setup, monkeypatch, kind):
    bridge, service, app = setup
    from deeptutor.tools.workspace import WorkspaceSaveTool
    from deeptutor.services import llm
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError("Archive must not call any model")
    monkeypatch.setattr(llm, "complete", forbidden)
    a, _ = await connect(bridge, app, paths=["materials/m001/正文.md"])
    with workspace_context(a["workspace_id"]):
        runtime = service.create_runtime_context(capability="chat", session_id=a["session_id"], turn_id="turn-one", workspace_id=a["workspace_id"])
        bridge.live_context("alpha", a["session_id"], "turn-one")
        result = await WorkspaceSaveTool().execute(filename="建议.md", content="# Generated\nEXACT_BODY", _workspace_id=a["workspace_id"], _workspace_output_dir=runtime.logical_output_dir)
        assert result.success, result
        item = result.metadata["workspace_items"][0]
        payload = {"workspace_id": a["workspace_id"], "workspace_item_id": item["workspace_item_id"], "kind": kind, "title": "中文😀" * 90, "confirmed": True}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/workbench-bridge/archive", json=payload)
            assert response.status_code == 200, response.text
            first = response.json()
            archived = bridge.store.path("alpha", first["path"])
            assert "EXACT_BODY" in archived.read_text() and "实际纳入模型上下文" in archived.read_text()
            assert item["relative_path"] in response.text
            archived.write_text("HUMAN_REVISED", encoding="utf-8")
            second = await client.post("/api/workbench-bridge/archive", json=payload)
            assert second.status_code == 200, second.text
            assert second.json()["path"] != first["path"] and archived.read_text() == "HUMAN_REVISED"
            assert bridge.store.path("alpha", item["relative_path"]).read_text().endswith("EXACT_BODY")
            assert not calls and first["model_calls"] == 0
            assert all(len(part.encode("utf-8")) < 160 for part in Path(first["path"]).parts)


@pytest.mark.asyncio
async def test_bound_paths_private_state_and_symlink_escape_rejected(setup):
    bridge, service, app = setup
    a, _ = await connect(bridge, app)
    binding = service.binding_by_id(a["workspace_id"])
    for path in ("../beta/README.md", ".deeptutor/data/anything", ".workbench/deeptutor/sessions/private.json"):
        with pytest.raises(ValueError):
            service.resolve(binding, path)
    link = bridge.store.topic("alpha") / "outside.md"
    link.symlink_to(bridge.store.path("beta", "README.md"))
    with pytest.raises(ValueError):
        bridge.handoff("alpha", ["outside.md"])
    with pytest.raises(ValueError):
        service.resolve(binding, "outside.md")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/workbench-bridge/connect", json={"topic_id": "../beta", "handoff_id": "a"*32})).status_code == 422
        assert (await client.post("/api/workbench-bridge/archive", json={"workspace_id": a["workspace_id"], "workspace_item_id":"wsi_"+"a"*32, "kind":"../beta", "title":"x", "confirmed":True})).status_code == 422


def test_unadvertised_tool_calls_cannot_bypass_shared_session_policy():
    from deeptutor.agents.loop.pipeline import AgenticLoopPipeline
    from deeptutor.core.context import UnifiedContext
    host = object.__new__(AgenticLoopPipeline)
    context = UnifiedContext(metadata={"workbench_topic": "alpha"})
    for name in ("exec", "load_tools", "read_memory", "read_skill", "mcp__filesystem__read"):
        with pytest.raises(ValueError, match="授权范围"):
            host._augment_tool_kwargs(name, {"path": "../beta/README.md"}, context)


@pytest.mark.asyncio
async def test_bridge_keeps_native_admin_authentication(setup, monkeypatch):
    from fastapi import Depends
    from deeptutor.api.routers import auth, workbench
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    app = FastAPI()
    app.include_router(workbench.router, prefix="/api/workbench-bridge", dependencies=[Depends(auth.require_admin)])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.get("/api/workbench-bridge/status")).status_code == 401
        async def regular_user():
            return SimpleNamespace(role="user")
        app.dependency_overrides[auth.require_auth] = regular_user
        assert (await client.get("/api/workbench-bridge/status")).status_code == 403


@pytest.mark.asyncio
async def test_shared_attachment_live_preview_removal_and_on_demand_read(setup):
    bridge, service, app = setup
    from deeptutor.core.context import UnifiedContext, TurnRuntimeContext
    from deeptutor.services.workbench import apply_shared_context
    from deeptutor.agents._shared.tool_runtime import bind_workspace_tool_runtime
    from deeptutor.tools.workspace import WorkspaceReadTool

    relative = "materials/m001/正文.md"
    source = bridge.store.path("alpha", relative)
    source.write_text("# Selected file\n" + "x"*5000 + "DEEP_CONTENT_FIRST", encoding="utf-8")
    a, handoff = await connect(bridge, app, paths=[relative])
    with workspace_context(a["workspace_id"]):
        runtime = service.create_runtime_context(capability="chat", session_id=a["session_id"], turn_id="on-demand-one", workspace_id=a["workspace_id"])
        context = UnifiedContext(session_id=a["session_id"], user_message="解释选定文档", source_manifest="NATIVE_UPLOAD",
                                 metadata={"source_index": {"uploaded": "NATIVE_ATTACHMENT_BODY"}}, runtime=TurnRuntimeContext(turn_id="on-demand-one", workspace=runtime))
        apply_shared_context(context)
        assert "DEEP_CONTENT_FIRST" not in context.user_message
        assert context.source_manifest == "NATIVE_UPLOAD" and context.metadata["source_index"]["uploaded"] == "NATIVE_ATTACHMENT_BODY"
        assert bridge.references("alpha", a["session_id"])["last_read"]["documents"][0]["used_chars"] == 2000
        kwargs = bind_workspace_tool_runtime("workspace_read", {"path": relative, "offset": 4900, "limit": 500}, context)
        result = await WorkspaceReadTool().execute(**kwargs)
        assert result.success and "DEEP_CONTENT_FIRST" in result.content
        assert bridge.references("alpha", a["session_id"])["last_read"]["reads"][0]["offset"] == 4900
        second = UnifiedContext(session_id=a["session_id"], user_message="继续", runtime=TurnRuntimeContext(turn_id="on-demand-two", workspace=runtime))
        apply_shared_context(second)
        assert bridge.references("alpha", a["session_id"])["last_read"]["documents"][0]["used_chars"] == 0
        source.write_text("EDITED_SHARED_FILE", encoding="utf-8")
        third = UnifiedContext(session_id=a["session_id"], user_message="修改后再看", runtime=TurnRuntimeContext(turn_id="on-demand-three", workspace=runtime))
        apply_shared_context(third)
        assert "EDITED_SHARED_FILE" in third.user_message
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            references = (await client.get(f"/api/workbench-bridge/sessions/{a['session_id']}", params={"workspace_id": a["workspace_id"]})).json()
            preview = await client.get(references["files"][0]["url"])
            assert preview.text == "EDITED_SHARED_FILE"
            updated = await client.patch(f"/api/workbench-bridge/sessions/{a['session_id']}/references", json={"workspace_id": a["workspace_id"], "paths": []})
            assert updated.status_code == 200 and updated.json()["paths"] == []
            assert (await client.get(references["files"][0]["url"])).status_code == 400
    again, _ = await connect(bridge, app, handoff=handoff)
    assert again["session_id"] == a["session_id"] and again["paths"] == []
    # Several long files still create compact references, rather than tripping
    # the separate 04 full-text aggregate limit.
    long_paths = [bridge.store.write_new("alpha", "notes", f"long-{i}.md", "x"*100000) for i in range(4)]
    long_session, _ = await connect(bridge, app, paths=long_paths)
    block, snapshot = bridge.live_context("alpha", long_session["session_id"], "compact-large")
    assert len(snapshot["documents"]) == 4 and len(block) < 11000
