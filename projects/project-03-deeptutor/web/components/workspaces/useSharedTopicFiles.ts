"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Paperclip } from "lucide-react";
import { requestJson } from "@/lib/api";
import type { ContextTreeItem } from "@/components/chat/home/ContextReferenceTree";
import type { MessageAttachment } from "@/features/chat/ChatStateAdapter";

type FileReference = {path: string; filename: string; size?: number; url?: string; error?: string};
type References = {paths: string[]; files: FileReference[]; last_read?: {
  warnings: string[]; documents: {path: string; used_chars: number; original_chars: number}[];
  reads?: {path: string; used_chars: number; offset: number; original_chars: number}[];
}};

export function useSharedTopicFiles(sessionId: string | null, workspaceId: string, streaming: boolean,
  preview: (attachment: MessageAttachment) => void) {
  const [loaded,setLoaded] = useState<{key: string; data: References}>();
  const [failure,setFailure] = useState<{key: string; text: string}>();
  const [pending,setPending] = useState(false);
  const key = `${workspaceId}:${sessionId}`;
  const data = loaded?.key === key ? loaded.data : undefined;
  const endpoint = `/api/workbench-bridge/sessions/${encodeURIComponent(sessionId || "")}`;

  useEffect(() => {
    if (!sessionId?.startsWith("wb_") || !workspaceId) return;
    const abort = new AbortController();
    void requestJson<References>(`${endpoint}?workspace_id=${encodeURIComponent(workspaceId)}`, {signal: abort.signal})
      .then(value => {setLoaded({key,data:value});setFailure(undefined);})
      .catch((e: unknown) => {if(!abort.signal.aborted)setFailure({key,text:e instanceof Error?e.message:"共享附件读取失败"});});
    return () => abort.abort();
  },[sessionId,workspaceId,streaming,endpoint,key]);

  const remove = useCallback(async(path: string) => {
    if (!data || pending || streaming) return;
    setPending(true);
    try {
      const value = await requestJson<References>(`${endpoint}/references`, {method:"PATCH", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({workspace_id:workspaceId,paths:data.paths.filter(p=>p!==path)})});
      setLoaded({key,data:value});setFailure(undefined);
    } catch(e: unknown) {setFailure({key,text:e instanceof Error?e.message:"移除共享附件失败"});}
    finally {setPending(false);}
  },[data,pending,streaming,endpoint,workspaceId,key]);

  const items = useMemo<ContextTreeItem[]>(() => (data?.files || []).map(file => {
    const read = data?.last_read?.reads?.find(r=>r.path===file.path);
    const doc = data?.last_read?.documents.find(d=>d.path===file.path);
    const warnings = data?.last_read?.warnings.filter(w=>w.includes(file.path)) || [];
    const status = file.error ? "文件不可用" : !doc && warnings.length ? "读取失败" : read ? "已读取" : doc?.used_chars ? "已附预览" : "文件引用";
    return {key:`shared-${file.path}`,icon:Paperclip,kind:"共享文件",label:file.filename,status,
      title: [file.path, "引用shared原文件，预览和后续读取使用当前内容。", read?`本轮读取 ${read.offset}–${read.offset+read.used_chars}/${read.original_chars} 字符`:doc?.used_chars?`本轮仅附 ${doc.used_chars}/${doc.original_chars} 字符预览，正文按需读取。`:"尚未读取正文。", file.error,...warnings].filter(Boolean).join("\n"),
      onClick:file.url?()=>preview({type:"document",id:`shared:${workspaceId}:${file.path}`,filename:file.filename,url:`${file.url}&v=${Date.now()}`,size_bytes:file.size}):undefined,
      onRemove:pending||streaming?undefined:()=>void remove(file.path)};
  }),[data,pending,streaming,preview,remove,workspaceId]);
  return {items,pending,error:failure?.key===key?failure.text:""};
}
