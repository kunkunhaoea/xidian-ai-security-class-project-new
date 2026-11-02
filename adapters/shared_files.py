"""Small, file-based topic store. No project04 database is needed to read it."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

REPOSITORY = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".md", ".txt", ".py", ".csv", ".yaml", ".yml", ".tex"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def short(value, limit=72):
    value = re.sub(r"[^\w\-.]+", "-", str(value), flags=re.UNICODE).strip(".-_")
    return value.encode("utf-8")[:limit].decode("utf-8", "ignore").rstrip(".-_") or "untitled"


def clean_private(value):
    """Do not archive account credentials or transient platform access tokens."""
    if isinstance(value, dict):
        return {k: clean_private(v) for k, v in value.items()
                if not any(word in k.lower() for word in ("cookie", "token", "password", "secret", "api_key", "authorization"))}
    if isinstance(value, list):
        return [clean_private(v) for v in value]
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        parsed = urlsplit(value)
        query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                 if not any(w in k.lower() for w in ("token", "key", "cookie", "secret", "password"))]
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    return value


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def first(row, *keys):
    return next((str(row[k]).strip() for k in keys if row.get(k) is not None and str(row[k]).strip()), "")


def identity(row, platform):
    doi = first(row, "doi").lower().removeprefix("https://doi.org/")
    url = first(row, "source_url", "url", "note_url", "aweme_url", "video_url", "html_url")
    arxiv = first(row, "arxiv_id") or (re.search(r"arxiv.org/(?:abs|pdf)/([^?#]+)", url) or [None, ""])[1]
    if doi:
        return "doi:" + doi
    if arxiv:
        return "arxiv:" + re.sub(r"v\d+$", "", arxiv.removesuffix(".pdf").rstrip("/"))
    if url:
        parsed = urlsplit(url)
        path = parsed.path.rstrip("/").removesuffix(".git")
        # Query parameters can identify different social posts; retain them except on GitHub.
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "" if "github.com" in parsed.netloc.lower() else parsed.query, ""))
    source_id = first(row, "comment_id", "note_id", "aweme_id", "video_id", "id")
    return platform + ":" + (source_id or hashlib.sha256(json.dumps(clean_private(row), sort_keys=True, ensure_ascii=False).encode()).hexdigest())


class TopicStore:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else REPOSITORY / "shared"
        self.base = self.root / "topics"

    def _base(self):
        if self.root.is_symlink() or self.base.is_symlink():
            raise ValueError("共享根目录不能是符号链接")
        self.base.mkdir(parents=True, exist_ok=True)
        return self.base.resolve()

    def topic(self, topic_id):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", topic_id):
            raise ValueError("课题标识只能包含小写英文、数字、短横线和下划线，最多64字符")
        path = self._base() / topic_id
        if path.is_symlink():
            raise ValueError("课题不能是符号链接")
        if not path.is_dir():
            raise FileNotFoundError("课题不存在")
        return path

    def path(self, topic_id, relative=""):
        base = self.topic(topic_id)
        if "\\" in relative or "\x00" in relative:
            raise ValueError("非法文件路径")
        parts = PurePosixPath(relative).parts
        if PurePosixPath(relative).is_absolute() or any(p in {"..", "."} or p.startswith(".") for p in parts):
            raise ValueError("只能访问当前课题的公开文件")
        target = base.joinpath(*parts)
        cursor = base
        for part in parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("不允许通过符号链接读取或写入课题资料")
        if not target.resolve().is_relative_to(base.resolve()):
            raise ValueError("文件不属于当前课题")
        return target

    def create(self, topic_id, name):
        self._base()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", topic_id):
            raise ValueError("课题标识格式不正确")
        if not name.strip() or len(name) > 160 or "\n" in name:
            raise ValueError("请填写160字符以内的课题名称")
        path = self.base / topic_id
        path.mkdir(exist_ok=False)
        (path / "README.md").write_text(f"# {name.strip()}\n\n课题标识：{topic_id}\n\n## 研究方向与进展\n\n待补充。\n", encoding="utf-8")
        return self.info(topic_id)

    def info(self, topic_id):
        path = self.topic(topic_id)
        readme = self.path(topic_id, "README.md")
        text = readme.read_text(encoding="utf-8") if readme.is_file() else ""
        name = next((s[2:].strip() for s in text.splitlines() if s.startswith("# ")), topic_id)
        return {"id": topic_id, "name": name, "path": str(path)}

    def topics(self):
        return [self.info(p.name) for p in sorted(self._base().iterdir())
                if p.is_dir() and not p.is_symlink() and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", p.name)]

    def files(self, topic_id):
        base = self.topic(topic_id)
        result = []
        for p in sorted(base.rglob("*")):
            relative = p.relative_to(base).as_posix()
            if any(part.startswith(".") for part in p.relative_to(base).parts):
                continue
            try:
                p = self.path(topic_id, relative)
                if not p.is_file():
                    continue
                result.append({"path": relative, "name": p.name, "size": p.stat().st_size,
                               "modified": p.stat().st_mtime, "suffix": p.suffix.lower(),
                               "readable": p.suffix.lower() in TEXT_SUFFIXES or p.suffix.lower() == ".pdf"})
            except ValueError:
                continue
        return result

    def materials(self, topic_id):
        grouped = {}
        for item in self.files(topic_id):
            parts = item["path"].split("/")
            if len(parts) < 3 or parts[0] != "materials":
                continue
            directory = "/".join(parts[:2])
            group = grouped.setdefault(directory, {"path": directory, "id": parts[1].split("-")[0], "files": []})
            group["files"].append(item)
        for group in grouped.values():
            source = self.path(topic_id, group["path"] + "/来源.md")
            text = source.read_text(encoding="utf-8") if source.is_file() else ""
            group["title"] = next((s[2:].strip() for s in text.splitlines() if s.startswith("# ")), group["path"].split("/")[-1])
            names = {f["name"].lower() for f in group["files"]}
            group["status"] = "PDF已取得" if any(n.endswith(".pdf") for n in names) else "README已取得" if "readme.md" in names else "正文已保存" if "content.md" in names else "仅摘要 / 全文未下载" if "abstract.md" in names else "仅链接 / 附件未取得"
            metadata = self.path(topic_id, group["path"]) / ".record.json"
            if metadata.is_file() and not metadata.is_symlink():
                record = json.loads(metadata.read_text(encoding="utf-8"))
                if record.get("platform") == "github" and group["status"] == "正文已保存":
                    group["status"] = "仓库简介已保存 / README及代码未取得"
            group["modified"] = max(f["modified"] for f in group["files"])
        return sorted(grouped.values(), key=lambda g: g["modified"], reverse=True)

    def write_new(self, topic_id, directory, filename, data):
        parent = self.path(topic_id, directory)
        parent.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix.lower()
        stem = short(Path(filename).stem)
        filename = stem + (suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else "")
        for i in range(10000):
            name = filename if i == 0 else f"{stem}-{i}{suffix}"
            target = self.path(topic_id, directory + "/" + name)
            try:
                with target.open("xb") as handle:
                    handle.write(data if isinstance(data, bytes) else data.encode("utf-8"))
                return target.relative_to(self.topic(topic_id)).as_posix()
            except FileExistsError:
                continue
        raise ValueError("同名文件过多")

    def archive(self, topic_id, row, platform):
        row = clean_private(row)
        key = identity(row, platform)
        materials = self.path(topic_id, "materials")
        materials.mkdir(exist_ok=True)
        found = None
        for folder in materials.iterdir():
            if folder.is_symlink() or not folder.is_dir():
                continue
            meta = folder / ".record.json"
            if meta.is_file() and not meta.is_symlink():
                record = json.loads(meta.read_text(encoding="utf-8"))
                if key == record.get("identity"):
                    found = folder
                    break
        title = first(row, "title", "note_title", "name", "desc", "content", "text") or "未命名资料"
        if found:
            # Keep user edits authoritative; save changed machine observations separately.
            incoming = json.dumps(clean_private(row), ensure_ascii=False, indent=2)
            raw = found / "raw.json"
            if not raw.is_symlink() and (not raw.is_file() or raw.read_text(encoding="utf-8") != incoming):
                self.write_new(topic_id, found.relative_to(self.topic(topic_id)).as_posix(), "采集更新-" + uuid4().hex[:10] + ".json", incoming)
            if row.get("readme") and not (found / "README.md").exists():
                self.write_new(topic_id, found.relative_to(self.topic(topic_id)).as_posix(), "README.md", str(row["readme"]))
            return {"path": found.relative_to(self.topic(topic_id)).as_posix(), "new": False}
        numbers = [int(m.group(1)) for p in materials.iterdir() if (m := re.match(r"m(\d+)-", p.name))]
        directory = f"materials/m{max(numbers, default=0)+1:03d}-{short(title, 48)}"
        folder = self.path(topic_id, directory)
        folder.mkdir()
        atomic_json(folder / ".record.json", {"identity": key, "source_id": first(row, "note_id", "aweme_id", "video_id", "id"), "platform": platform})
        self.write_new(topic_id, directory, "raw.json", json.dumps(clean_private(row), ensure_ascii=False, indent=2))
        url = first(row, "source_url", "url", "note_url", "aweme_url", "video_url", "html_url")
        body = first(row, "content", "text", "desc", "description", "abstract", "summary")
        is_abstract = platform == "arxiv" or bool(row.get("abstract"))
        content_name = "abstract.md" if is_abstract else "content.md"
        if body:
            self.write_new(topic_id, directory, content_name, body + "\n")
        if row.get("readme"):
            self.write_new(topic_id, directory, "README.md", str(row["readme"]))
        state = "仅摘要 / 全文未下载" if is_abstract and body else "正文已保存，附件未取得" if body else "仅链接 / 全文未下载"
        source = f"# {title.replace(chr(10), ' ')}\n\n- 资料编号：{folder.name.split('-')[0]}\n- 来源平台：{platform}\n- 原始链接：{url}\n- 作者：{first(row, 'author', 'authors', 'nickname', 'creator')}\n- 获取时间：{now()}\n- 首次归档状态：{state}\n- 人工筛选：待阅读\n\n## 附件与正文\n\n"
        if body:
            source += f"- [已有{'摘要' if is_abstract else '正文'}]({content_name})\n"
        if row.get("readme"):
            source += "- [已取得的README](README.md)\n"
        source += "\n实际附件以本目录当前文件为准，可在工作台刷新查看。完整标题和原始记录保存在本目录；链接不代表已取得全文。\n"
        self.write_new(topic_id, directory, "来源.md", source)
        return {"path": directory, "new": True}

    def read_inputs(self, topic_id, paths):
        if not paths:
            raise ValueError("请明确选择本次输入；默认不读取任何资料")
        documents, warnings = [], []
        for relative in dict.fromkeys(paths):
            path = self.path(topic_id, relative)
            if not path.is_file():
                raise ValueError(f"输入不存在或不是文件：{relative}")
            if path.stat().st_size > 15 * 1024 * 1024:
                raise ValueError(f"文件超过15MB，请选择较小文件：{relative}")
            try:
                raw = path.read_bytes()
                if path.suffix.lower() == ".pdf":
                    try:
                        import pypdf
                        from io import BytesIO
                        pages = [(i + 1, p.extract_text() or '') for i, p in enumerate(pypdf.PdfReader(BytesIO(raw)).pages)]
                        text = "\n".join(f"[第{i}页]\n{body}" for i, body in pages if body.strip())
                    except ImportError:
                        raise ValueError("PDF解析器未安装，请安装workbench后端依赖，或提供Markdown/纯文本")
                    if not text.strip():
                        raise ValueError("未提取到PDF正文，可能为扫描件；本版不提供OCR")
                elif path.suffix.lower() in TEXT_SUFFIXES:
                    text = raw.decode("utf-8-sig")
                else:
                    raise ValueError("不支持将该格式作为文本输入")
                original_length = len(text)
                if original_length > 60000:
                    text = text[:60000]
                    warnings.append(f"{relative} 共{original_length}字符，本次仅使用前60000字符")
                if not text.strip():
                    raise ValueError("文本为空")
                if path.name == "来源.md":
                    warnings.append(f"{relative} 是来源说明，不能视为已读取论文全文")
                if path.name == "abstract.md":
                    warnings.append(f"{relative} 本次使用摘要，全文未纳入")
                documents.append({"path": relative, "title": next((s[2:] for s in text.splitlines() if s.startswith('# ')), path.stem), "text": text, "sha256": hashlib.sha256(raw).hexdigest(), "read_at": now(), "original_chars": original_length, "used_chars": len(text)})
            except Exception as exc:
                warnings.append(f"未使用 {relative}：{exc}")
        if not documents:
            raise ValueError("没有可使用的正文。" + "；".join(warnings))
        if sum(d["used_chars"] for d in documents) > 180000:
            raise ValueError("本次输入超过180000字符，请减少选择或拆成多次任务")
        return {"documents": documents, "warnings": warnings}
