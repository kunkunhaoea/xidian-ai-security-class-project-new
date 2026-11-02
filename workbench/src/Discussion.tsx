import { useEffect, useState } from 'react'
import { ExternalLink, RefreshCw } from 'lucide-react'
import { message, prefix, request } from './api'
import { Notice } from './common'

type Service = { frontend_url: string; api_url: string; available: boolean; state: string; detail: string }

export function Discussion({topic, handoff, open}: {topic: string; handoff: string; open: () => void}) {
  const [service,setService]=useState<Service>(), [error,setError]=useState(''), [loading,setLoading]=useState(false), [version,setVersion]=useState(0)
  useEffect(()=>{const abort=new AbortController();setLoading(true);request<Service>(prefix+'/deeptutor/status',undefined,'GET',abort.signal).then(s=>{setService(s);setError('')}).catch(e=>{if(!abort.signal.aborted)setError(message(e))}).finally(()=>{if(!abort.signal.aborted)setLoading(false)});return()=>abort.abort()},[topic,version])
  const url=service&&handoff?`${service.frontend_url}/workbench-connect?${new URLSearchParams({topic,handoff})}`:''
  return <section className="discussion-page"><div className="discussion-toolbar"><h1>研究讨论</h1><div className="row"><button disabled={loading} onClick={()=>setVersion(v=>v+1)}><RefreshCw size={16}/>重新连接</button>{url&&<a className="button" href={url} target="_blank" rel="noreferrer"><ExternalLink size={16}/>独立窗口打开DeepTutor</a>}</div></div><Notice error={error}/>{service?.detail&&<Notice text={service.detail}/>}{loading?<p role="status">正在检查03服务…</p>:!service?.available?<div className="panel"><p>DeepTutor尚未连接。请启动03服务，再点击重新连接。</p><p className="muted">服务地址由后端配置；03前端、后端和API代理分别配置，详见工作台说明。</p></div>:!handoff?<div className="panel"><p>进入当前课题的独立讨论会话。本次不自动附加文件。</p><button className="primary" onClick={open}>进入课题工作区</button></div>:<iframe key={topic+handoff+version} src={url} title="DeepTutor研究讨论" className="discussion-frame" allow="clipboard-write" onError={()=>setError('03页面连接失败，请重新连接或独立窗口打开。')}/>}
  </section>
}
