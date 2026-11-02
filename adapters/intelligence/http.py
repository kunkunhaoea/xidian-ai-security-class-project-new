"""Workbench HTTP boundary, mounted by project04; file and task code is independent."""
from __future__ import annotations

import base64
import binascii
import os
import re
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from .engine import PROJECT04, Workbench


class Errors(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except FileExistsError as exc:
                raise HTTPException(409, "名称已存在，请换一个课题标识") from exc
            except FileNotFoundError as exc:
                raise HTTPException(404, "课题、文件或任务不存在") from exc
            except (ValueError, UnicodeError) as exc:
                raise HTTPException(400, str(exc)) from exc
        return handler


router = APIRouter(prefix="/api/workbench", tags=["workbench"], route_class=Errors)
engine = Workbench()


class Topic(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)


class Rename(BaseModel):
    name: str = Field(min_length=1, max_length=160, pattern=r"^[^\r\n]+$")


class Options(BaseModel):
    merge: bool = True
    themes: list[str] = Field(default_factory=list, max_length=20)
    custom_themes: list[str] = Field(default_factory=list, max_length=10)
    theme: str = Field(default="", max_length=200)
    count: int = Field(default=3, ge=1, le=10)
    reflections: int = Field(default=2, ge=1, le=4)
    strategy: Literal["v2_reflection", "v2_novelty"] = "v2_reflection"
    allow_rule_fallback: bool = False


class Prepare(BaseModel):
    kind: Literal["analysis", "ideas"]
    paths: list[str] = Field(min_length=1, max_length=30)
    options: Options = Field(default_factory=Options)


class Submit(BaseModel):
    prepared_id: str
    kind: Literal["analysis", "ideas"]


class Upload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(max_length=21 * 1024 * 1024)
    title: str = Field(default="", max_length=300)
    source_url: str = Field(default="", max_length=2000)
    material: str = Field(default="", max_length=200)


class Crawl(BaseModel):
    platforms: list[Literal["arxiv", "github", "xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu"]] = Field(min_length=1, max_length=9)
    keywords: str = Field(default="", max_length=1000)
    crawler_type: Literal["search", "detail", "creator"] = "search"
    specified_ids: str = Field(default="", max_length=3000)
    creator_ids: str = Field(default="", max_length=3000)
    max_notes_count: int = Field(default=5, ge=1, le=100)
    max_comments_count: int = Field(default=10, ge=1, le=200)
    start_page: int = Field(default=1, ge=1, le=1000)
    enable_comments: bool = False
    enable_sub_comments: bool = False
    login_type: Literal["qrcode", "cookie"] = "qrcode"
    cookies: str = Field(default="", max_length=20000)
    headless: bool = False


class Settings(BaseModel):
    model: str = Field(min_length=1, max_length=150, pattern=r"^[^\r\n]+$")
    base_url: str = Field(min_length=1, max_length=1000, pattern=r"^https?://[^\r\n]+$")
    api_key: str | None = Field(default=None, max_length=2000, pattern=r"^[^\r\n]*$")


@router.get("/health")
def health():
    from pipeline.analysis_service import available_themes
    from adapters.shared_files import REPOSITORY
    from importlib.util import find_spec
    return {"status": "ok", "shared": str(REPOSITORY / "shared"), "themes": available_themes(),
            "pdf_available": find_spec("pypdf") is not None,
            "license": "项目04基于MediaCrawler，保留原非商业学习许可证；见项目04/LICENSE"}


@router.get("/topics")
def topics():
    return engine.store.topics()


@router.post("/topics", status_code=201)
def create_topic(request: Topic):
    return engine.store.create(request.id, request.name)


@router.put("/topics/{topic_id}")
def rename(topic_id: str, request: Rename):
    path = engine.store.path(topic_id, "README.md")
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    title = "# " + request.name.strip()
    text = re.sub(r"(?m)^# .*$", lambda _: title, text, count=1) if re.search(r"(?m)^# .*$", text) else title + "\n\n" + text
    path.write_text(text, encoding="utf-8")
    return engine.store.info(topic_id)


@router.get("/topics/{topic_id}/files")
def files(topic_id: str):
    return {"files": engine.store.files(topic_id), "materials": engine.store.materials(topic_id)}


@router.get("/topics/{topic_id}/file")
def file(topic_id: str, path: str, download: bool = False):
    target = engine.store.path(topic_id, path)
    if not target.is_file():
        raise FileNotFoundError(path)
    if download:
        return FileResponse(target, filename=target.name, content_disposition_type="attachment")
    from adapters.shared_files import TEXT_SUFFIXES
    if target.suffix.lower() in TEXT_SUFFIXES or target.suffix.lower() == ".json":
        if target.stat().st_size > 15 * 1024 * 1024:
            raise ValueError("预览文件超过15MB，请下载查看")
        text = target.read_text(encoding="utf-8-sig")
        return {"path": path, "local_path": str(target), "content": text[:100000], "truncated": len(text) > 100000}
    return {"path": path, "local_path": str(target), "content": "该格式请下载后使用本地阅读器查看。", "truncated": False}


@router.post("/topics/{topic_id}/import")
def upload(topic_id: str, request: Upload):
    if "/" in request.filename or "\\" in request.filename or request.filename.startswith("."):
        raise ValueError("上传文件名不能包含路径或隐藏文件名")
    try:
        content = base64.b64decode(request.content_base64, validate=True)
    except binascii.Error as exc:
        raise ValueError("上传内容编码不正确") from exc
    if len(content) > 15 * 1024 * 1024:
        raise ValueError("单个文件不能超过15MB")
    if request.material:
        parts = PurePosixPath(request.material).parts
        if len(parts) != 2 or parts[0] != "materials":
            raise ValueError("附件只能补充到当前课题的资料目录")
        folder = engine.store.path(topic_id, request.material)
        if not folder.is_dir():
            raise ValueError("资料目录不存在")
        directory = request.material
    else:
        # Each upload is distinct unless a real source URL establishes identity.
        from uuid import uuid4
        row = {"id": uuid4().hex, "title": request.title or Path(request.filename).stem, "url": request.source_url}
        directory = engine.store.archive(topic_id, row, "manual")["path"]
    name = request.filename
    if Path(name).suffix.lower() in {".md", ".txt"} and Path(name).name.lower() != "readme.md":
        # Make the imported main text discoverable as current editable content.
        name = "content" + Path(name).suffix.lower() if not request.material else name
    path = engine.store.write_new(topic_id, directory, name, content)
    return {"path": path, "material": directory}


@router.post("/topics/{topic_id}/prepare")
async def prepare(topic_id: str, request: Prepare):
    if request.kind == "analysis" and not (request.options.themes or request.options.custom_themes):
        raise ValueError("请至少选择一个分析领域或填写自定义领域")
    return await engine.prepare(topic_id, request.paths, request.kind, request.options.model_dump())


@router.post("/topics/{topic_id}/tasks", status_code=202)
async def submit(topic_id: str, request: Submit):
    return engine.create(topic_id, request.kind, prepared_id=request.prepared_id)


@router.post("/topics/{topic_id}/crawl", status_code=202)
async def crawl(topic_id: str, request: Crawl):
    if request.crawler_type == "search" and not request.keywords.strip():
        raise ValueError("请填写检索关键词")
    if request.crawler_type != "search" and {"arxiv", "github"}.intersection(request.platforms):
        raise ValueError("当前arXiv/GitHub采集器只支持关键词搜索")
    if request.crawler_type == "detail" and not request.specified_ids.strip():
        raise ValueError("请填写内容ID或链接")
    if request.crawler_type == "creator" and not request.creator_ids.strip():
        raise ValueError("请填写创作者ID")
    options = request.model_dump(exclude={"cookies"})
    return engine.create(topic_id, "crawl", options, cookie=request.cookies)


@router.get("/topics/{topic_id}/tasks")
def tasks(topic_id: str):
    return engine.tasks(topic_id)


def owned(topic_id, task_id):
    engine.store.topic(topic_id)
    record = engine.get(task_id)
    if record["topic_id"] != topic_id:
        raise ValueError("任务不属于当前课题")
    return record


@router.get("/topics/{topic_id}/tasks/{task_id}")
def task(topic_id: str, task_id: str):
    record = owned(topic_id, task_id)
    path = engine.location(task_id) / "log.txt"
    record["log"] = path.read_text(encoding="utf-8")[-100000:] if path.exists() else "暂无日志"
    record["raw_files"] = [p.relative_to(engine.location(task_id) / "raw").as_posix() for p in (engine.location(task_id) / "raw").rglob("*.jsonl") if not p.is_symlink()]
    record["recoverable"] = (engine.location(task_id) / "result.json").is_file() or bool(record["raw_files"])
    return record


@router.get("/topics/{topic_id}/tasks/{task_id}/raw")
def raw(topic_id: str, task_id: str, path: str):
    owned(topic_id, task_id)
    root = engine.location(task_id) / "raw"
    if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts or "\\" in path:
        raise ValueError("非法原始记录路径")
    target = root / path
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()) or target.suffix != ".jsonl":
        raise ValueError("非法原始记录路径")
    if not target.is_file():
        raise FileNotFoundError(path)
    return FileResponse(target, filename=target.name)


@router.post("/topics/{topic_id}/tasks/{task_id}/cancel")
async def cancel(topic_id: str, task_id: str):
    owned(topic_id, task_id)
    return engine.cancel(task_id)


@router.post("/topics/{topic_id}/tasks/{task_id}/retry")
async def retry(topic_id: str, task_id: str, archive_only: bool = False):
    owned(topic_id, task_id)
    return engine.retry(task_id, archive_only)


@router.get("/settings")
def settings():
    return {"model": os.getenv("AI_MODEL", ""), "base_url": os.getenv("AI_BASE_URL", ""), "key_configured": bool(os.getenv("AI_API_KEY")), "github_token_configured": bool(os.getenv("GITHUB_TOKEN"))}


@router.put("/settings")
def save_settings(request: Settings):
    from dotenv import set_key
    values = {"AI_MODEL": request.model, "AI_BASE_URL": request.base_url.rstrip("/")}
    if request.api_key:
        values["AI_API_KEY"] = request.api_key
    for key, value in values.items():
        set_key(str(PROJECT04 / ".env"), key, value)
        os.environ[key] = value
    return settings()
