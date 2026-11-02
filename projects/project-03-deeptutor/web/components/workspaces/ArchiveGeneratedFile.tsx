"use client";

import { useState } from "react";
import { requestJson } from "@/lib/api";
import { notify } from "@/lib/notifications";

export default function ArchiveGeneratedFile({url, filename}: {url?: string | null; filename: string}) {
  const [open,setOpen]=useState(false), [title,setTitle]=useState(filename.replace(/\.[^.]+$/, ""));
  const [kind,setKind]=useState("notes"), [busy,setBusy]=useState(false), [error,setError]=useState("");
  // Only native immutable workspace presentations are eligible. Backend checks
  // the original outputs path, topic, session, content hash and export policy.
  const match=url?.match(/\/files\/workspace-items\/(ws_[a-f0-9]{32})\/(wsi_[a-f0-9]{32})(?:[?/#]|$)/);
  if (!match) return null;
  const submit=async(e: React.FormEvent)=>{
    e.preventDefault();if(busy)return;setBusy(true);setError("");
    try {
      await requestJson<{path: string}>(`/api/workbench-bridge/archive?dt_workspace=${match[1]}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({workspace_id:match[1],workspace_item_id:match[2],kind,title,confirmed:true})});
      notify("归档成功",{tone:"success",durationMs:2500});setOpen(false);
    } catch(e:unknown){setError(e instanceof Error?e.message:"归档失败，请重试");}finally{setBusy(false);}
  };
  return <div className="shrink-0 text-xs"><button type="button" className="rounded border border-[var(--border)] px-2 py-1" onClick={()=>{setOpen(v=>!v);setError("");}}>归档</button>
    {open&&<div className="absolute right-3 top-16 z-50 w-80 max-w-[90vw] rounded-lg border border-[var(--border)] bg-[var(--card)] p-4 shadow-lg"><form onSubmit={submit} className="flex flex-col gap-3"><strong>归档选定生成文件</strong><label>标题<input className="w-full rounded border p-1 text-[var(--foreground)] bg-[var(--background)]" required maxLength={500} value={title} onChange={e=>setTitle(e.target.value)}/></label><label>归档到<select className="w-full rounded border p-1 bg-[var(--background)]" value={kind} onChange={e=>setKind(e.target.value)}><option value="notes">研究笔记</option><option value="plans">实验方案</option><option value="writing">写作草稿</option></select></label><p>确认后授权复制此文件并生成来源说明。保留outputs原件，不覆盖已有文档，不调用AI。</p>{error&&<p role="alert">{error}</p>}<div className="flex gap-3"><button disabled={busy} type="submit">{busy?"正在保存…":"确认归档"}</button><button disabled={busy} type="button" onClick={()=>setOpen(false)}>取消</button></div></form></div>}
  </div>;
}
