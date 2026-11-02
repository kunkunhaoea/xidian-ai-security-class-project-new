"""Durable local tasks calling existing project04 business functions.

Runtime state stays in project04/data/workbench (ignored); shared holds readable outputs.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
from pathlib import Path
from uuid import uuid4

import httpx

from adapters.shared_files import REPOSITORY, TopicStore, atomic_json, clean_private, first, now

PROJECT04 = REPOSITORY / "projects" / "project-04-Intelligence Gathering"
ACTIVE = {"queued", "running"}


class Workbench:
    def __init__(self, store=None, state=None):
        self.store = store or TopicStore()
        self.state = Path(state) if state is not None else PROJECT04 / "data" / "workbench"
        self.running = {}
        self.processes = {}
        self.shutting_down = False
        self.crawl_lock = asyncio.Lock()

    def location(self, task_id):
        if not re.fullmatch(r"[0-9a-f]{32}", task_id):
            raise ValueError("非法任务标识")
        path = self.state / "tasks" / task_id
        if path.is_symlink():
            raise ValueError("任务目录不能是符号链接")
        return path

    def redact(self, text, extra=()):
        text = str(text)
        secrets = [v for k, v in os.environ.items() if any(w in k.upper() for w in ("API_KEY", "TOKEN", "COOKIE", "PASSWORD")) and len(v) > 3]
        for secret in [*secrets, *extra]:
            if secret:
                text = text.replace(secret, "[已隐藏]")
        return re.sub(r"(?i)(authorization|cookie|api[_-]?key|access[_-]?token)\s*[:=]\s*[^\s,;]+", r"\1=[已隐藏]", text)

    def get(self, task_id):
        return json.loads((self.location(task_id) / "task.json").read_text(encoding="utf-8"))

    def save(self, record):
        atomic_json(self.location(record["id"]) / "task.json", record)

    def log(self, task_id, text, extra=()):
        with (self.location(task_id) / "log.txt").open("a", encoding="utf-8") as handle:
            handle.write(f"{now()} {self.redact(text, extra)}\n")

    def recover(self):
        if not (self.state / "tasks").exists():
            return
        for path in (self.state / "tasks").glob("*/task.json"):
            record = self.get(path.parent.name)
            if record["status"] in ACTIVE and record["id"] not in self.running:
                record.update(status="interrupted", ended_at=now(), error="服务已中断；未自动重跑。原输入与已有结果仍保留。")
                self.save(record)

    def tasks(self, topic_id):
        self.store.topic(topic_id)
        result = []
        for path in (self.state / "tasks").glob("*/task.json"):
            record = self.get(path.parent.name)
            if record["topic_id"] == topic_id:
                result.append(record)
        return sorted(result, key=lambda r: r["created_at"], reverse=True)

    async def prepare(self, topic_id, paths, kind, options):
        snapshot = self.store.read_inputs(topic_id, paths)
        if kind not in {"analysis", "ideas"}:
            raise ValueError("不支持的操作")
        snapshot.update(topic_id=topic_id, kind=kind, options=options, id=uuid4().hex, external=[], external_status="未启用外部文献检索")
        if kind == "ideas" and options.get("strategy") == "v2_novelty":
            query = options.get("theme") or snapshot["documents"][0]["title"]
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=8)) as client:
                    response = await client.get("https://api.openalex.org/works", params={"search": query[:200], "per-page": 5})
                    response.raise_for_status()
                    snapshot["external"] = [{"title": r.get("display_name"), "year": r.get("publication_year"), "url": r.get("doi") or r.get("id")} for r in response.json().get("results", [])]
                snapshot["external_status"] = f"OpenAlex检索完成，{len(snapshot['external'])}篇；仅作为新颖性风险参考，未下载全文"
            except Exception as exc:
                snapshot["external_status"] = f"OpenAlex检索失败：{type(exc).__name__}，本次没有文献校验结果"
                snapshot["warnings"].append(snapshot["external_status"])
        atomic_json(self.state / "prepared" / (snapshot["id"] + ".json"), snapshot)
        return snapshot

    def create(self, topic_id, kind, options=None, prepared_id=None, cookie=""):
        self.store.topic(topic_id)
        snapshot = None
        if kind in {"analysis", "ideas"}:
            if not prepared_id or not re.fullmatch(r"[0-9a-f]{32}", prepared_id):
                raise ValueError("请先预览并冻结本次输入")
            snapshot = json.loads((self.state / "prepared" / (prepared_id + ".json")).read_text(encoding="utf-8"))
            if snapshot["topic_id"] != topic_id or snapshot["kind"] != kind:
                raise ValueError("预览输入不属于当前课题或操作")
            options = snapshot["options"]
        elif kind != "crawl":
            raise ValueError("未知任务类型")
        record = {"id": uuid4().hex, "topic_id": topic_id, "kind": kind, "status": "queued", "created_at": now(),
                  "options": clean_private(options or {}), "inputs": [{k: d[k] for k in ("path", "sha256", "read_at", "original_chars", "used_chars")} for d in snapshot["documents"]] if snapshot else [],
                  "outputs": [], "warnings": snapshot.get("warnings", []) if snapshot else [], "error": "", "cookie_required": bool(cookie)}
        record["model_config"] = {"model": os.getenv("AI_MODEL", ""), "base_url": os.getenv("AI_BASE_URL", "https://api.openai.com/v1")}
        directory = self.location(record["id"])
        directory.mkdir(parents=True)
        if snapshot:
            atomic_json(directory / "input.json", snapshot)
        self.save(record)
        future = asyncio.create_task(self.run(record["id"], cookie, os.getenv("AI_API_KEY", "")))
        self.running[record["id"]] = future
        def finished(completed):
            # A task cancelled before its coroutine first runs never enters run's finally.
            if completed.cancelled():
                current = self.get(record["id"])
                if current["status"] in ACTIVE:
                    current.update(status="interrupted" if self.shutting_down else "cancelled", ended_at=now(), error="服务停止，任务中断" if self.shutting_down else "用户取消了尚未执行的任务")
                    self.save(current)
                self.running.pop(record["id"], None)
        future.add_done_callback(finished)
        return record

    def cancel(self, task_id):
        record = self.get(task_id)
        if task_id not in self.running or record["status"] not in ACTIVE:
            raise ValueError("任务已经结束或当前服务无法取消它")
        self.running[task_id].cancel()
        return {"requested": True}

    def retry(self, task_id, archive_only=False):
        old = self.get(task_id)
        if old["status"] in ACTIVE:
            raise ValueError("运行中的任务不能重试")
        if archive_only:
            if old["kind"] == "crawl":
                record = self.archive_crawl(task_id)
                if not record["outputs"]:
                    raise ValueError("没有可恢复的原始资料")
            else:
                record = self.publish(task_id)
            if old["status"] != "cancelled":
                record.update(status=self.completion_status(record), error="", ended_at=now())
                self.save(record)
            return record
        if old.get("cookie_required"):
            raise ValueError("Cookie未持久化，请重新填写登录信息并新建采集任务")
        if old["kind"] == "crawl":
            return self.create(old["topic_id"], "crawl", old["options"])
        snapshot = json.loads((self.location(task_id) / "input.json").read_text(encoding="utf-8"))
        snapshot["id"] = uuid4().hex
        atomic_json(self.state / "prepared" / (snapshot["id"] + ".json"), snapshot)
        record = self.create(old["topic_id"], old["kind"], prepared_id=snapshot["id"])
        record["retry_of"] = task_id
        self.save(record)
        return record

    def completion_status(self, record):
        if record.get("crawl_errors") or record.get("archive_errors") or any(w.startswith("未使用 ") for w in record.get("warnings", [])):
            return "partial"
        result = self.location(record["id"]) / "result.json"
        if result.exists() and json.loads(result.read_text(encoding="utf-8")).get("failures"):
            return "partial"
        return "completed"

    async def stop_process(self, task_id):
        process = self.processes.get(task_id)
        if process and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                await asyncio.wait_for(process.wait(), 10)
            except (ProcessLookupError, asyncio.TimeoutError):
                if process.returncode is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()

    async def run(self, task_id, cookie="", api_key=""):
        from pipeline.analysis_service import task_model_settings
        record = self.get(task_id)
        token = task_model_settings.set({**record["model_config"], "api_key": api_key})
        try:
            record.update(status="running", started_at=now())
            self.save(record)
            self.log(task_id, "任务开始；使用提交时已冻结的课题、输入及选项")
            if record["kind"] == "crawl":
                async with self.crawl_lock:
                    await self.crawl(task_id, cookie)
            else:
                snapshot = json.loads((self.location(task_id) / "input.json").read_text(encoding="utf-8"))
                if record["kind"] == "analysis":
                    from pipeline.analysis_service import analyze_records
                    options = record["options"]
                    groups = [snapshot["documents"]] if options.get("merge", True) else [[d] for d in snapshot["documents"]]
                    result = {"reports": [], "failures": []}
                    for group_index, group in enumerate(groups, 1):
                        self.log(task_id, f"开始分析第{group_index}/{len(groups)}组，输入文件{len(group)}个")
                        rows = [{"title": d["title"], "content": d["text"][i:i+2500], "__source_platform": "shared", "source_url": d["path"]}
                                for d in group for i in range(0, len(d["text"]), 2500)]
                        try:
                            analysis = await analyze_records(rows, options.get("themes", []), options.get("custom_themes", []))
                            result["reports"].append({"paths": [d["path"] for d in group], "analysis": analysis})
                            self.log(task_id, f"第{group_index}组完成：{analysis['method']}；保留{analysis['output_count']}段，领域筛选排除{analysis['scope_excluded']}段")
                        except Exception as exc:
                            result["failures"].append({"paths": [d["path"] for d in group], "error": self.redact(exc)})
                        atomic_json(self.location(task_id) / "result.json", result)
                    if not result["reports"]:
                        raise ValueError("所有分析均失败：" + json.dumps(result["failures"], ensure_ascii=False))
                else:
                    from api.routers.scientist import _generate_research_ideas
                    options = record["options"]
                    self.log(task_id, f"开始生成研究想法：{options.get('strategy', 'v2_reflection')}，数量{options.get('count', 3)}，反思轮数{options.get('reflections', 2)}")
                    ideas, method, basis = await _generate_research_ideas(options.get("count", 3), options.get("theme", ""), options.get("strategy", "v2_reflection"), options.get("reflections", 2), options.get("allow_rule_fallback", False), selected_context=snapshot)
                    result = {"ideas": ideas, "method": method, "basis": basis}
                    atomic_json(self.location(task_id) / "result.json", result)
                    self.log(task_id, f"已生成{len(ideas)}个候选想法；原始生成结果已保存，方法{method}")
                # Store successful generation before publishing: retry publication does not call the model.
                self.publish(task_id)
            record = self.get(task_id)
            record["status"] = self.completion_status(record)
            self.save(record)
            self.log(task_id, f"任务结束：{record['status']}；共享产物{len(record['outputs'])}项")
        except asyncio.CancelledError:
            await self.stop_process(task_id)
            record = self.get(task_id)
            record.update(status="interrupted" if self.shutting_down else "cancelled", error="服务停止；任务中断，已取得资料和日志仍保留" if self.shutting_down else "用户取消；已取得资料和日志仍保留")
            self.save(record)
            if record["kind"] == "crawl":
                self.archive_crawl(task_id)
        except Exception as exc:
            record = self.get(task_id)
            record.update(status="failed", error=self.redact(exc, [cookie]))
            self.save(record)
            self.log(task_id, "失败：" + record["error"], [cookie])
        finally:
            task_model_settings.reset(token)
            await self.stop_process(task_id)
            record = self.get(task_id)
            if record["kind"] == "crawl" and record["status"] == "failed" and (self.location(task_id) / "raw").exists():
                try:
                    record = self.archive_crawl(task_id)
                except Exception as exc:
                    self.log(task_id, "恢复部分采集结果失败：" + self.redact(exc))
            record["ended_at"] = now()
            self.save(record)
            self.running.pop(task_id, None)
            self.processes.pop(task_id, None)

    async def crawl(self, task_id, cookie):
        from api.schemas.crawler import CrawlerStartRequest
        from api.services.crawler_manager import crawler_manager
        record = self.get(task_id)
        if crawler_manager.status in {"running", "stopping"}:
            raise ValueError("原04页面有采集正在运行，请结束后再启动工作台采集")
        options = record["options"]
        raw = self.location(task_id) / "raw"
        raw.mkdir(exist_ok=True)
        errors = []
        for platform in options["platforms"]:
            config = CrawlerStartRequest(**{**options, "platform": platform, "platforms": [platform], "cookies": cookie, "save_option": "jsonl"})
            command = crawler_manager._build_command(config) + ["--save_data_path", str(raw)]
            self.log(task_id, f"采集平台 {platform}；原始输出位于本任务独立目录")
            # Credentials are passed to the existing crawler, never written to the task record or command log.
            process = await asyncio.create_subprocess_exec(*command, cwd=PROJECT04, env={**os.environ, "PYTHONUNBUFFERED": "1"}, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True, limit=1024*1024)
            self.processes[task_id] = process
            while line := await process.stdout.readline():
                self.log(task_id, line.decode("utf-8", "replace").rstrip(), [cookie])
            code = await process.wait()
            if code:
                errors.append({"platform": platform, "exit_code": code})
        record = self.get(task_id)
        record["crawl_errors"] = errors
        self.save(record)
        result = self.archive_crawl(task_id)
        if not result["outputs"]:
            raise ValueError("本次没有取得可归档资料，请查看采集日志；未将历史记录当作结果")

    def archive_crawl(self, task_id):
        record = self.get(task_id)
        results, errors, comments = [], [], []
        for path in sorted((self.location(task_id) / "raw").rglob("*.jsonl")):
            platform = path.relative_to(self.location(task_id) / "raw").parts[0]
            sanitized = []
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    row = clean_private(json.loads(line))
                    sanitized.append(json.dumps(row, ensure_ascii=False))
                    if not isinstance(row, dict):
                        raise ValueError("原始记录不是对象")
                    if "comment" in path.name:
                        comments.append((platform, row))
                    else:
                        archived = self.store.archive(record["topic_id"], row, platform)
                        results.append(archived)
                        self.copy_acquired_attachments(task_id, record["topic_id"], archived["path"], row, platform)
                except Exception as exc:
                    errors.append(f"{path.name}:{number} {self.redact(exc)}")
                    if not sanitized or sanitized[-1] != line:
                        # Preserve malformed observations for diagnosis instead of silently discarding them.
                        self.log(task_id, f"原始记录解析或归档失败 {path.name}:{number}；{self.redact(line)}")
            # Original content stays available, but platform credentials are never downloadable.
            path.write_text("\n".join(sanitized) + ("\n" if sanitized else ""), encoding="utf-8")
        for platform, row in comments:
            try:
                parent_id = str(row.get("note_id") or row.get("aweme_id") or row.get("video_id") or "")
                folder = None
                for material in self.store.materials(record["topic_id"]):
                    meta = self.store.path(record["topic_id"], material["path"]) / ".record.json"
                    if meta.is_file() and not meta.is_symlink():
                        source = json.loads(meta.read_text(encoding="utf-8"))
                        if parent_id and source.get("source_id") == parent_id and source.get("platform") == platform:
                            folder = material["path"]
                            break
                if folder:
                    comment_id = str(row.get("comment_id") or uuid4().hex)
                    from adapters.shared_files import short
                    name = f"comment-{short(comment_id, 45)}.json"
                    target = self.store.path(record["topic_id"], folder + "/" + name)
                    if not target.exists():
                        self.store.write_new(record["topic_id"], folder, name, json.dumps(clean_private(row), ensure_ascii=False, indent=2))
                else:
                    results.append(self.store.archive(record["topic_id"], {**row, "title": "未匹配原帖的社交评论（原始讨论，非论文证据）"}, platform))
            except Exception as exc:
                errors.append(self.redact(exc))
        record.update(outputs=sorted(set(r["path"] for r in results)), archive_errors=errors,
                      archive_summary={"records": len(results), "new": sum(r["new"] for r in results), "duplicates": sum(not r["new"] for r in results), "comments": len(comments)}, archive_status="failed" if errors else "completed")
        self.save(record)
        return record

    def copy_acquired_attachments(self, task_id, topic_id, directory, row, platform):
        """Preserve existing crawler downloads, without fetching additional URLs."""
        import hashlib
        source_id = first(row, "note_id", "aweme_id", "video_id", "id")
        if not source_id:
            return
        raw = self.location(task_id) / "raw"
        aliases = {"dy": "douyin", "bili": "bilibili", "wb": "weibo"}
        for namespace in {platform, aliases.get(platform, platform)}:
            for candidate in (raw / namespace).rglob("*"):
                if candidate.is_symlink() or not candidate.is_file() or not candidate.resolve().is_relative_to(raw.resolve()):
                    continue
                if source_id not in candidate.relative_to(raw).parts or candidate.suffix.lower() in {".jsonl", ".json"}:
                    continue
                existing = self.store.path(topic_id, directory + "/" + candidate.name)
                data = candidate.read_bytes()
                if existing.is_file() and hashlib.sha256(existing.read_bytes()).digest() == hashlib.sha256(data).digest():
                    continue
                self.store.write_new(topic_id, directory, candidate.name, data)

    def publish(self, task_id):
        record = self.get(task_id)
        result = json.loads((self.location(task_id) / "result.json").read_text(encoding="utf-8"))
        snapshot = json.loads((self.location(task_id) / "input.json").read_text(encoding="utf-8"))
        outputs = record.get("outputs", [])
        for index, item in enumerate(result.get("reports", result.get("ideas", []))):
            marker = self.location(task_id) / f"published-{index}.json"
            if marker.exists():
                path = json.loads(marker.read_text())["path"]
                if path not in outputs:
                    outputs.append(path)
                continue
            kind = record["kind"]
            title = f"分析报告 {record['created_at']}" if kind == "analysis" else item.get("Title", "研究想法")
            directory = "notes/分析报告" if kind == "analysis" else "notes/研究想法"
            text = f"# {title}\n\n- 稳定标识：{task_id}-{index+1}\n- 生成时间：{now()}\n- 方法：{item['analysis']['method'] if kind == 'analysis' else result['method']}\n\n## 实际输入\n\n"
            for doc in snapshot["documents"]:
                if kind == "analysis" and doc["path"] not in item["paths"]:
                    continue
                from urllib.parse import quote
                text += f"- [{doc['path']}]({quote('../../' + doc['path'], safe='/')})；读取 {doc['read_at']}；SHA256 {doc['sha256']}；使用 {doc['used_chars']}/{doc['original_chars']} 字符\n"
            text += "\n## 范围、缺失与失败说明\n\n" + "\n".join("- " + w for w in snapshot["warnings"]) + "\n"
            if kind == "analysis":
                analysis = item["analysis"]
                text += f"\n- 输入片段 {analysis['input_count']}；领域筛选后保留 {analysis['output_count']}；排除 {analysis['scope_excluded']}\n"
                text += "\n## 来源证据与规则分析\n\n"
                for row in analysis["items"]:
                    text += f"### {row['title']}\n\n来源：{row.get('source_url', '')}\n\n" + "\n".join("> " + e for e in row.get("evidence", [])) + "\n\n规则摘要：" + row["summary"] + "\n\n"
                text += "\n## AI分批总结\n\n"
                for summary in analysis["batch_summaries"]:
                    text += f"### 第{summary.get('batch')}批（{summary.get('record_count')}段）\n\n{summary.get('summary', '')}\n\n"
                    text += "主题：" + "、".join(str(t) for t in summary.get("topics", [])) + "\n\n"
                if not analysis["batch_summaries"]:
                    text += "本次没有AI总结。\n"
                text += "\n## 研究主题与候选方向（规则建议）\n\n"
                for topic in analysis["intelligence"].get("topics", []):
                    text += f"- {topic['topic']}：{topic['content_count']}个片段；关键词：{'、'.join(topic['representative_terms'])}\n"
                for direction in analysis["intelligence"].get("research_directions", []):
                    text += f"\n### {direction['research_direction']}\n\n{direction['why_now']}\n\n"
                    text += "\n".join("- " + q for q in direction["research_questions"]) + "\n\n"
                text += "\n上述条数以输入片段计；规则趋势和候选问题属于启发式建议，需人工核验。\n"
                if result.get("failures"):
                    text += "\n\n## 其他输入分析失败\n\n" + json.dumps(result["failures"], ensure_ascii=False, indent=2)
            else:
                text += "\n## AI建议（待验证）\n\n"
                for key, label in [("Short Hypothesis", "核心假设"), ("Related Work", "相关工作"), ("Abstract", "研究问题"), ("Experiments", "建议实验与指标"), ("Risk Factors and Limitations", "风险与局限")]:
                    text += f"### {label}\n\n{item.get(key, '模型未提供，请人工补充。')}\n\n"
                text += "\n## 外部文献补充\n\n" + snapshot.get("external_status", "未检索") + "\n\n"
                for paper in snapshot.get("external", []):
                    text += f"- {paper.get('title', '')}（{paper.get('year', '')}）：{paper.get('url', '')}\n"
                text += "\n\n候选想法尚未证明创新性；外部记录不等于已阅读全文或验证创新。\n"
            text += "\n\n## 人工判断与修订\n\n待补充。\n"
            path = self.store.write_new(record["topic_id"], directory, f"{task_id[:12]}-{index+1}.md", text)
            atomic_json(marker, {"path": path})
            outputs.append(path)
            record["outputs"] = outputs
            self.save(record)
        record.update(outputs=outputs, archive_status="completed")
        self.save(record)
        return record
