"""Temporary browser-verification servers. No real model calls, no user data.

Run using each project's Python, with --role 03/04 --state /tmp/<unique-dir>.
Only a test fixture: never use it as the normal application launcher.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--role", choices=["03", "04"], required=True)
parser.add_argument("--state", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
state = args.state.resolve()
if not state.is_relative_to(Path("/tmp")):
    parser.error("browser fixture state must be under /tmp")
state.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(root))
os.environ["DEEPTUTOR_HOME"] = str(state / "03")
os.environ["DEEPTUTOR_VERSION_CHECK_ENABLED"] = "false"
os.environ["WORKBENCH_PUBLIC_URL"] = "http://127.0.0.1:8089"
os.environ["WORKBENCH_DEEPTUTOR_FRONTEND_URL"] = "http://127.0.0.1:3799"
os.environ["WORKBENCH_DEEPTUTOR_API_URL"] = "http://127.0.0.1:8019"
from adapters.shared_files import TopicStore

topics = TopicStore(state / "shared")
if not topics.base.exists():
    topics.create("browser-alpha", "浏览器甲课题")
    topics.create("browser-beta", "浏览器乙课题")
    topics.write_new("browser-alpha", "materials/m001", "正文.md", "BROWSER_ORIGINAL")
    topics.write_new("browser-beta", "materials/m001", "正文.md", "OTHER_TOPIC_DO_NOT_READ")

if args.role == "04":
    sys.path.insert(0, str(root / "projects" / "project-04-Intelligence Gathering"))
    from api.main import app, workbench_engine
    # The router and engine retain one store object; change its test locations.
    workbench_engine.store.root = topics.root
    workbench_engine.store.base = topics.base
    workbench_engine.state = state / "04"
    port = 8089
else:
    sys.path.insert(0, str(root / "projects" / "project-03-deeptutor"))
    settings = state / "03" / "data" / "user" / "settings"
    settings.mkdir(parents=True, exist_ok=True)
    catalog = {"version": 1, "services": {"llm": {"active_profile_id": "offline", "active_model_id": "offline", "profiles": [{"id": "offline", "name": "OFFLINE TEST", "binding": "openai", "base_url": "http://127.0.0.1:1/v1", "api_key": "offline-not-a-secret", "models": [{"id": "offline", "name": "Offline simulated model", "model": "offline-test"}]}]}}}
    (settings / "model_catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    (settings / "auth.json").write_text('{"enabled":false}', encoding="utf-8")
    from deeptutor.api.main import app
    import deeptutor.services.workbench as bridge
    import deeptutor.api.routers.workbench as api
    import deeptutor.services.workspace.service as filesystem
    from adapters.deeptutor.files import SharedDiscussion
    instance = SharedDiscussion(topics)
    bridge.shared_discussion = lambda: instance
    api.shared_discussion = lambda: instance
    bridge.SHARED_TOPICS = topics.base
    filesystem._SHARED_TOPICS = topics.base
    from deeptutor.runtime.turn_engine import TurnEngine
    from deeptutor.core.stream import StreamEvent, StreamEventType
    from deeptutor.services.session.turn_runtime import TurnRuntimeManager
    from deeptutor.tools.workspace import WorkspaceSaveTool
    async def execute(self, context):
        (state / "actual-context.txt").write_text(context.user_message, encoding="utf-8")
        for part in ["离线模拟流式回答：", "已收到当前选定资料。"]:
            yield StreamEvent(type=StreamEventType.CONTENT, source="chat", content=part, metadata={"call_kind": "llm_final_response"})
            await asyncio.sleep(.2)
        saved = await WorkspaceSaveTool().execute(filename="生成笔记.md", content="# 模拟生成\nBROWSER_GENERATED_BODY", _workspace_id=context.runtime.workspace.workspace_id, _workspace_output_dir=context.runtime.workspace.logical_output_dir)
        if not saved.success:
            raise ValueError(saved.content)
        yield StreamEvent(type=StreamEventType.TOOL_RESULT, source="workspace_save", content=saved.content, metadata={"tool_name": "workspace_save", "tool_metadata": saved.metadata})
        yield StreamEvent(type=StreamEventType.SOURCES, source="chat", metadata={"sources": saved.sources})
        yield StreamEvent(type=StreamEventType.DONE, source="chat")
    async def no_title(*args, **kwargs):
        pass
    class Builder:
        async def build(self, **kwargs):
            return SimpleNamespace(conversation_history=[], conversation_summary="", context_text="", token_count=0, budget=0)
    TurnEngine.execute = execute
    TurnRuntimeManager._maybe_generate_session_title = no_title
    TurnRuntimeManager._create_context_builder = lambda self: Builder()
    # Any accidental automatic model request fails locally rather than charging.
    from deeptutor.services import llm
    async def forbidden(*args, **kwargs):
        with (state / "unexpected-model-calls.txt").open("a") as handle:
            handle.write("Unexpected model call\n")
        raise AssertionError("Browser fixture forbids real model calls")
    llm.complete = forbidden
    port = 8019

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port)
