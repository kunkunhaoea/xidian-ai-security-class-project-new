"""Exercise a running local server; --collect performs one public GitHub query.

No analysis or paid model requests are made. A separate smoke topic is retained
so the user can inspect actual files and task logs in the workbench.
"""
import argparse
import base64
import json
import time
from uuid import uuid4

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--base-url", default="http://127.0.0.1:8080")
parser.add_argument("--collect", action="store_true")
args = parser.parse_args()
topic = "workbench-smoke-" + uuid4().hex[:8]
with httpx.Client(base_url=args.base_url, timeout=15) as client:
    def call(method, url, **kwargs):
        response = client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()

    health = call("GET", "/api/workbench/health")
    for url in ("/", "/workbench/", "/workbench/materials", "/api/health"):
        response = client.get(url)
        assert response.status_code == 200, (url, response.status_code)
    call("POST", "/api/workbench/topics", json={"id": topic, "name": "工作台验收演示（非研究资料）"})
    root = "/api/workbench/topics/" + topic
    uploaded = call("POST", root + "/import", json={"filename": "smoke.md", "title": "手工导入验收", "content_base64": base64.b64encode("# 导入验收\nSMOKE_CURRENT_TEXT\n".encode()).decode()})
    preview = call("GET", root + "/file", params={"path": uploaded["path"]})
    assert "SMOKE_CURRENT_TEXT" in preview["content"]
    prepared = call("POST", root + "/prepare", json={"kind": "analysis", "paths": [uploaded["path"]], "options": {"custom_themes": ["SMOKE_CURRENT_TEXT"]}})
    assert "SMOKE_CURRENT_TEXT" in prepared["documents"][0]["text"]
    print(json.dumps({"topic": topic, "entry_routes": "passed", "import_and_frozen_input": "passed", "pdf_available": health["pdf_available"]}, ensure_ascii=False), flush=True)
    if args.collect:
        task = call("POST", root + "/crawl", json={"platforms": ["github"], "keywords": "repo:HKUDS/DeepTutor", "max_notes_count": 1})
        for _ in range(100):
            detail = call("GET", root + "/tasks/" + task["id"])
            if detail["status"] not in {"queued", "running"}:
                print(json.dumps({"task": task["id"], "status": detail["status"], "outputs": detail["outputs"], "error": detail["error"], "archive_summary": detail.get("archive_summary")}, ensure_ascii=False), flush=True)
                if detail["status"] != "completed":
                    print(detail.get("log", "")[-5000:], flush=True)
                    raise SystemExit(1)
                break
            time.sleep(1)
        else:
            print("采集仍在运行，请在任务页查看", flush=True)
            raise SystemExit(2)
