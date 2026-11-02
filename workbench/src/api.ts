export type Topic = { id: string; name: string; path: string }
export type SharedFile = { path: string; name: string; size: number; modified: number; readable: boolean; suffix: string }
export type Material = { id: string; title: string; path: string; status: string; modified: number; files: SharedFile[] }
export type FileList = { materials: Material[]; files: SharedFile[] }
export type Document = { path: string; title: string; text: string; sha256: string; read_at: string; original_chars: number; used_chars: number }
export type Prepared = { id: string; documents: Document[]; warnings: string[]; external: {title: string; year: string; url: string}[]; external_status: string }
export type Task = { id: string; kind: string; status: string; created_at: string; started_at?: string; ended_at?: string; options: Record<string, unknown>; inputs: {path: string; sha256: string; used_chars: number}[]; outputs: string[]; warnings: string[]; error: string; archive_errors?: string[]; crawl_errors?: {platform: string; exit_code: number}[]; archive_summary?: {records: number; new: number; duplicates: number; comments: number}; archive_status?: string; log?: string; raw_files?: string[]; recoverable?: boolean }
export type Health = { status: string; shared: string; themes: string[]; pdf_available: boolean; license: string }
export type Settings = { model: string; base_url: string; key_configured: boolean; github_token_configured: boolean }
export const prefix = '/api/workbench'
export const topicURL = (topic: string) => `${prefix}/topics/${encodeURIComponent(topic)}`
export const downloadURL = (topic: string, path: string) => `${topicURL(topic)}/file?path=${encodeURIComponent(path)}&download=true`
export async function request<T>(url: string, body?: unknown, method = 'GET', signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { method, signal, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) })
  if (!response.ok) {
    const result = await response.json().catch(() => ({}))
    const detail = result.detail
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((d: {msg: string}) => d.msg).join('；') : `服务请求失败（${response.status}）`)
  }
  return response.json()
}
export const message = (error: unknown) => error instanceof Error ? error.message : '操作失败'
export const statusText: Record<string, string> = { queued: '等待中', running: '运行中', completed: '已完成', failed: '失败', partial: '部分完成', cancelled: '已取消', interrupted: '已中断' }
export const kindText: Record<string, string> = { crawl: '资料采集', analysis: '内容分析', ideas: '想法生成' }
