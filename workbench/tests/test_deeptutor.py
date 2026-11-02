from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from adapters.deeptutor.files import SharedDiscussion
from adapters.deeptutor.http import create_router


@pytest.mark.asyncio
async def test_handoff_deduplicates_explicit_files_and_rejects_folders_and_other_topics(store):
    source = store.write_new("topic-a", "materials/m001", "正文.md", "ONLY_SELECTED")
    app = FastAPI()
    app.include_router(create_router(store))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        result = await client.post("/api/workbench/deeptutor/topics/topic-a/handoffs", json={"paths": [source, source]})
        assert result.status_code == 201
        data = result.json()
        assert data["paths"] == [source] and "workbench-connect?" in data["url"]
        record = SharedDiscussion(store).load("topic-a", "handoffs", data["id"])
        assert record["paths"] == [source]
        assert all(not f["path"].startswith(".") for f in store.files("topic-a"))
        for value in ("materials/m001", "../topic-b/README.md", "/etc/passwd", ".env"):
            assert (await client.post("/api/workbench/deeptutor/topics/topic-a/handoffs", json={"paths": [value]})).status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["ready", "login", "stopped", "old", "frontend_stopped"])
async def test_real_status_is_not_hardcoded(store, monkeypatch, state):
    import adapters.deeptutor.http as module
    def respond(request):
        if state == "stopped" or (state == "frontend_stopped" and request.url.path.endswith(".png")):
            raise httpx.ConnectError("test-offline", request=request)
        if request.url.path.endswith(".png"):
            return httpx.Response(200, content=b"asset")
        if state == "login":
            return httpx.Response(401, json={"detail": "login"})
        if state == "old":
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"bridge_version": 1})
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(module, "httpx", SimpleNamespace(AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs), HTTPError=httpx.HTTPError))
    app = FastAPI()
    app.include_router(create_router(store))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        result = (await client.get("/api/workbench/deeptutor/status")).json()
    assert result["available"] == (state in {"ready", "login"})
    if state not in {"ready"}:
        assert result["detail"]


def test_pdf_scan_not_treated_as_fulltext_and_partial_failure_explicit(store):
    import io
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    target = io.BytesIO()
    writer.write(target)
    pdf = store.write_new("topic-a", "materials/m001", "扫描.pdf", target.getvalue())
    text = store.write_new("topic-a", "outputs/chat/session/turn", "正文.md", "REAL_OUTPUT_TEXT")
    prepared = store.read_inputs("topic-a", [pdf, text])
    assert [d["path"] for d in prepared["documents"]] == [text]
    assert any("扫描件" in warning for warning in prepared["warnings"])
    assert store.read_inputs("topic-a", [text])["documents"][0]["text"] == "REAL_OUTPUT_TEXT"


def test_archive_binary_preserves_file_links_and_retries_after_save_failure(store, monkeypatch):
    bridge = SharedDiscussion(store)
    source = store.write_new("topic-a", "outputs/chat/session/turn", "figure.png", b"\x89PNG\r\nBINARY")
    exports = []
    def export(origin, destination, sha):
        store.write_new("topic-a", str(Path(destination).parent), Path(destination).name, store.path("topic-a", origin).read_bytes())
        exports.append(destination)
    original = store.write_new
    def fail_once(topic, directory, filename, data):
        if filename == "归档.md":
            raise OSError("SIMULATED_DISK_FAILURE")
        return original(topic, directory, filename, data)
    monkeypatch.setattr(store, "write_new", fail_once)
    with pytest.raises(OSError, match="SIMULATED_DISK_FAILURE"):
        bridge.archive("topic-a", source, "notes", "图示", session_id="session", turn_id="turn", export=export)
    assert store.path("topic-a", source).exists() and store.path("topic-a", exports[0]).exists()
    monkeypatch.setattr(store, "write_new", original)
    result = bridge.archive("topic-a", source, "notes", "图示", session_id="session", turn_id="turn", export=export)
    assert result["model_calls"] == 0 and result["attachment"] != exports[0]
    assert store.path("topic-a", result["attachment"]).read_bytes() == b"\x89PNG\r\nBINARY"
    body = store.path("topic-a", result["path"]).read_text()
    assert "非文本生成物" in body and "未记录" in body
    import re
    from urllib.parse import unquote
    for href in re.findall(r"\]\(([^)]+)\)", body):
        resolved = (store.path("topic-a", result["path"]).parent / unquote(href)).resolve()
        assert resolved.is_file() and resolved.is_relative_to(store.topic("topic-a"))
