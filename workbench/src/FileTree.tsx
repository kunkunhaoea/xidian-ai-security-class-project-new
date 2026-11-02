import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight, File, Folder, FolderOpen, Paperclip } from 'lucide-react'
import type { Material, SharedFile } from './api'

type Directory = { name: string; path: string; children: Map<string, Directory>; files: SharedFile[] }
const labels: Record<string, string> = { materials: '资料', notes: '研究笔记', plans: '实验方案', outputs: '原始生成文件', runs: '运行记录', writing: '写作' }

function treeFrom(files: SharedFile[]): Directory {
  const root: Directory = { name: '', path: '', children: new Map(), files: [] }
  for (const file of files) {
    const parts = file.path.split('/')
    let node = root
    for (const name of parts.slice(0, -1)) {
      if (!node.children.has(name)) node.children.set(name, { name, path: node.path ? `${node.path}/${name}` : name, children: new Map(), files: [] })
      node = node.children.get(name)!
    }
    node.files.push(file)
  }
  return root
}

type Props = {
  files: SharedFile[]
  topicName: string
  topicId: string
  materials?: Material[]
  query?: string
  selected?: string[]
  onToggle?: (path: string) => void
  onToggleMany?: (paths: string[], selected: boolean) => void
  defaultExpandedDepth?: number
  onPreview: (path: string) => void
  onAttach?: (path: string) => void
}

type TreeState = Pick<Props, 'topicName' | 'topicId' | 'onToggle' | 'onToggleMany' | 'onPreview' | 'onAttach'> & {
  materialMap: Map<string, Material>; selection: Set<string>; search: string;
  sourceFiles: SharedFile[];
  defaultExpandedDepth: number;
  mode: { action: 'default' | 'open' | 'closed'; version: number }
}
const TreeContext = createContext<TreeState | null>(null)

function FolderCheckbox({ paths, label, selection, onToggleMany }: {
  paths: string[]; label: string; selection: Set<string>;
  onToggleMany: (paths: string[], selected: boolean) => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const count = paths.filter(path => selection.has(path)).length
  const checked = paths.length > 0 && count === paths.length
  const partial = count > 0 && !checked
  useEffect(() => { if (input.current) input.current.indeterminate = partial }, [partial])
  return <input ref={input} type="checkbox" className="tree-folder-checkbox"
    aria-label={`选择文件夹 ${label}`} aria-checked={partial ? 'mixed' : checked}
    title="选择此文件夹及子目录中的全部可读取文件，不受分页限制"
    checked={checked} disabled={!paths.length} onChange={() => onToggleMany(paths, !checked)}/>
}

function DirectoryRow({ node, depth }: { node: Directory; depth: number }) {
    const { materialMap, selection, mode, search, sourceFiles, topicName, topicId, onToggle, onToggleMany, onPreview, onAttach, defaultExpandedDepth } = useContext(TreeContext)!
    // A folder selection covers all its readable descendants, including other
    // pages and items hidden by a file search. It never reaches another topic.
    const descendants = sourceFiles.filter(file => !node.path || file.path.startsWith(node.path + '/'))
    const selectable = descendants.filter(file => file.readable).map(file => file.path)
    const chosen = descendants.filter(file => selection.has(file.path)).length
    const material = materialMap.get(node.path)
    const [open, setOpen] = useState(!!search || mode.action === 'open' || (mode.action === 'default' && depth <= defaultExpandedDepth))
    const [limit, setLimit] = useState(10)
    const [page, setPage] = useState(0)
    const expanded = open
    const folders = [...node.children.values()].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN', { numeric: true }))
    const entries = [...folders.map(folder => ({ folder, file: undefined })), ...node.files.map(file => ({ folder: undefined, file }))]
    const totalPages = limit ? Math.max(1, Math.ceil(entries.length / limit)) : 1
    const currentPage = Math.min(page, totalPages - 1)
    const visible = limit ? entries.slice(currentPage * limit, (currentPage + 1) * limit) : entries
    const label = depth === 0 ? topicName : material ? `${material.id} · ${material.title}` : depth === 1 ? labels[node.name] || node.name : node.name
    const path = depth === 0 ? topicId : node.path

    return <div className={`tree-folder ${depth === 0 ? 'tree-root' : ''}`}>
      <div className="tree-folder-row">
        {onToggleMany && <FolderCheckbox paths={selectable} label={label} selection={selection} onToggleMany={onToggleMany}/>}
        <button type="button" className="tree-folder-toggle" aria-expanded={expanded} title={path} onClick={() => setOpen(!open)}>
          <ChevronRight size={15} className={expanded ? 'tree-chevron expanded' : 'tree-chevron'}/>
          {expanded ? <FolderOpen size={17}/> : <Folder size={17}/>}
          <span className="tree-folder-name">{label}</span>
        </button>
        <span className="tree-count">{descendants.length} 个文件{chosen > 0 ? ` · 已选 ${chosen}` : ''}</span>
        {material && <span className="tag tree-material-status">{material.status}</span>}
        {expanded && entries.length > 0 && <label className="tree-limit">每页<select aria-label={`${label}每页显示数量`} value={limit} onChange={event => { setLimit(+event.target.value); setPage(0) }}>
          <option value={5}>5项</option><option value={10}>10项</option><option value={20}>20项</option><option value={50}>50项</option><option value={0}>全部</option>
        </select></label>}
        {material && onAttach && <button type="button" className="tree-attach" aria-label={`为${material.title}补充附件`} onClick={() => onAttach(node.path)}><Paperclip size={14}/>附件</button>}
      </div>
      {expanded && <div className="tree-children">{visible.map(entry => entry.folder
        ? <DirectoryRow key={entry.folder.path} node={entry.folder} depth={depth + 1}/>
        : <div className="tree-file" key={entry.file!.path}>
          {onToggle && <input type="checkbox" aria-label={`选择 ${entry.file!.path}`} checked={selection.has(entry.file!.path)} disabled={!entry.file!.readable} onChange={() => onToggle(entry.file!.path)}/>}
          <File size={16}/><button type="button" className="tree-file-name" title={entry.file!.path} onClick={() => onPreview(entry.file!.path)}>{entry.file!.name}</button>
          <span className="tree-file-size">{(entry.file!.size / 1024).toFixed(1)} KB</span>
          <button type="button" className="text tree-preview" aria-label={`预览 ${entry.file!.path}`} onClick={() => onPreview(entry.file!.path)}>预览</button>
        </div>)}
        {!entries.length && <p className="tree-empty">没有匹配的文件</p>}
        {totalPages > 1 && <div className="tree-pagination"><span>第 {currentPage + 1} / {totalPages} 页 · {entries.length} 项</span><button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button><button type="button" disabled={currentPage === totalPages - 1} onClick={() => setPage(currentPage + 1)}>下一页</button></div>}
      </div>}
    </div>
  }


export function FileTree({ files, topicName, topicId, materials = [], query = '', selected = [], onToggle, onToggleMany, onPreview, onAttach, defaultExpandedDepth = 1 }: Props) {
  const materialMap = useMemo(() => new Map(materials.map(material => [material.path, material])), [materials])
  const search = query.trim().toLocaleLowerCase()
  const filtered = useMemo(() => files.filter(file => {
    const material = materialMap.get(file.path.split('/').slice(0, 2).join('/'))
    return !search || `${file.path} ${material?.title || ''} ${material?.status || ''}`.toLocaleLowerCase().includes(search)
  }), [files, materialMap, search])
  const root = useMemo(() => treeFrom(filtered), [filtered])
  const [mode, setMode] = useState<{ action: 'default' | 'open' | 'closed'; version: number }>({ action: 'default', version: 0 })
  const selection = new Set(selected)


  return <TreeContext.Provider value={{ materialMap, selection, mode, search, sourceFiles: files, topicName, topicId, onToggle, onToggleMany, onPreview, onAttach, defaultExpandedDepth }}><div className="file-tree"><div className="tree-tools"><span>{search ? `匹配 ${filtered.length} 个文件` : '文件目录'}</span><div className="row"><button type="button" className="text" onClick={() => setMode(previous => ({ action: 'open', version: previous.version + 1 }))}>展开全部</button><button type="button" className="text" onClick={() => setMode(previous => ({ action: 'closed', version: previous.version + 1 }))}>收起全部</button></div></div>
    <DirectoryRow key={`${mode.version}-${search}`} node={root} depth={0}/>
  </div></TreeContext.Provider>
}
