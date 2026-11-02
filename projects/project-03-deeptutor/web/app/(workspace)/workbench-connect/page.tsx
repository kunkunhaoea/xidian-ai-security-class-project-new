"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { requestJson } from "@/lib/api";

export default function WorkbenchConnectPage() {
  const params = useSearchParams();
  const topic = params.get("topic") || "";
  const handoff = params.get("handoff") || "";
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const invalid = !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(topic) || !/^[a-f0-9]{32}$/.test(handoff);
  useEffect(() => {
    let active = true;
    if (invalid) return;
    void requestJson<{route: string}>("/api/workbench-bridge/connect", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({topic_id: topic, handoff_id: handoff}),
    }).then(result => {
      if (!/^\/chat\/wb_[a-f0-9]{32}\?dt_workspace=ws_[a-f0-9]{32}$/.test(result.route)) throw new Error("03返回了无效会话地址");
      if (active) window.location.replace(result.route);
    }).catch((e: unknown) => { if (active) setError(e instanceof Error ? e.message : "课题会话建立失败"); });
    return () => { active = false; };
  }, [topic, handoff, retry, invalid]);
  const failure = invalid ? "缺少有效课题或资料交接记录，请从工作台点击去聊天。" : error;
  return <div className="p-6"><h1>进入课题讨论</h1>{failure ? <><p role="alert">{failure}</p><button type="button" onClick={() => {setError("");setRetry(v=>v+1);}}>重试连接</button></> : <p role="status">正在注册或复用课题工作区，准备会话。此操作不会调用模型。</p>}</div>;
}
