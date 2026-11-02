import { useEffect, useRef, useState } from 'react'
import { Copy, Download, X } from 'lucide-react'
import { downloadURL, message, request, topicURL } from './api'

export function Notice({ error, text }: {error?: string; text?: string}) {
  return error ? <div role="alert" className="notice error">{error}</div> : text ? <div role="status" className="notice">{text}</div> : null
}
export function Empty({children}: {children: React.ReactNode}) { return <div className="empty">{children}</div> }
export function Modal({title, close, children}: {title: string; close: () => void; children: React.ReactNode}) {
  const ref = useRef<HTMLElement>(null)
  useEffect(()=>{const previous=document.activeElement as HTMLElement|null;ref.current?.focus();return()=>previous?.focus()},[])
  return <div className="overlay" onClick={e => { if(e.target === e.currentTarget) close() }} onKeyDown={e => { if(e.key === 'Escape') close();if(e.key==='Tab'){const elements=ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled)');if(elements?.length){const first=elements[0],last=elements[elements.length-1];if(e.shiftKey&&(document.activeElement===first||document.activeElement===ref.current)){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}} }}><section ref={ref} tabIndex={-1} className="modal" role="dialog" aria-modal="true" aria-label={title}><div className="row spread"><h2>{title}</h2><button aria-label="关闭" onClick={close}><X size={18}/></button></div>{children}</section></div>
}
export function FilePreview({topic, path, close}: {topic: string; path: string; close: () => void}) {
  const [data, setData] = useState<{content: string; local_path: string; truncated: boolean}>()
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const load = async () => { try { setData(await request(`${topicURL(topic)}/file?path=${encodeURIComponent(path)}`)); setError('') } catch(e) {setError(message(e))} }
  useEffect(() => { void load() }, [topic,path])
  return <Modal title={path} close={close}><div className="row"><button onClick={load}>重新读取</button><a className="button" href={downloadURL(topic,path)}><Download size={15}/>下载</a><button disabled={!data} onClick={async()=>{try {await navigator.clipboard.writeText(data!.local_path); setCopied(true)} catch {setError('复制不可用，请从下方手动复制路径')}}}><Copy size={15}/>{copied ? '已复制' : '复制本地路径'}</button></div><Notice error={error}/>{data ? <><p className="muted path">{data.local_path}</p>{data.truncated && <Notice text="预览仅显示前100000字符，请下载查看完整内容。"/>}<pre className="document">{data.content}</pre></> : <p>正在读取…</p>}</Modal>
}
