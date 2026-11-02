import { useEffect, useId, useRef, useState } from 'react'
import { Check, ChevronDown, Search } from 'lucide-react'
import type { Topic } from './api'

export function TopicPicker({ topics, value, onChange }: {
  topics: Topic[]; value: string; onChange: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const container = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const listId = useId()
  const selected = topics.find(topic => topic.id === value)
  const filtered = topics.filter(topic => `${topic.name} ${topic.id}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))

  useEffect(() => {
    if (!open) return
    input.current?.focus()
    const outside = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [open])

  useEffect(() => {
    container.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [active])

  const choose = (id: string) => {
    onChange(id)
    setOpen(false)
    trigger.current?.focus()
  }

  return <div className="topic-picker" ref={container}>
    <button ref={trigger} type="button" className="topic-trigger" aria-label="当前课题"
      aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? listId : undefined}
      title={selected?.name || '请选择课题'} onClick={() => {
        setQuery(''); setActive(Math.max(0, topics.findIndex(topic => topic.id === value))); setOpen(!open)
      }} onKeyDown={event => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault(); setQuery(''); setActive(0); setOpen(true)
        }
      }}>
      <span className={`topic-trigger-label ${selected ? '' : 'muted'}`}>{selected?.name || '请选择课题'}</span>
      <ChevronDown size={16} className={open ? 'rotated' : ''}/>
    </button>
    {open && <div className="topic-popover">
      <div className="topic-search"><Search size={17}/><input ref={input} role="combobox"
        aria-label="搜索课题" aria-autocomplete="list" aria-expanded={open} aria-controls={listId}
        aria-activedescendant={filtered[active] ? `${listId}-${active}` : undefined}
        placeholder="搜索课题名称或标识" value={query} onChange={event => { setQuery(event.target.value); setActive(0) }}
        onKeyDown={event => {
          if (event.key === 'Escape') { event.preventDefault(); setOpen(false); trigger.current?.focus() }
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault()
            setActive(index => Math.max(0, Math.min(filtered.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1))))
          }
          if (event.key === 'Enter' && filtered[active]) { event.preventDefault(); choose(filtered[active].id) }
          if (event.key === 'Tab') setOpen(false)
        }}/></div>
      <ul id={listId} role="listbox" aria-label="课题列表" className="topic-options">
        {filtered.length ? filtered.map((topic, index) => <li key={topic.id} id={`${listId}-${index}`}
          role="option" aria-selected={topic.id === value} data-active={index === active}
          className={`topic-option ${index === active ? 'highlighted' : ''}`}
          onPointerMove={() => setActive(index)} onMouseDown={event => event.preventDefault()} onClick={() => choose(topic.id)}>
          <span><span className="topic-option-name">{topic.name}</span><small>{topic.id}</small></span>
          {topic.id === value && <Check size={17}/>}
        </li>) : <li className="topic-no-results">{topics.length ? '没有匹配的课题' : '暂无课题，请先新建'}</li>}
      </ul>
      <div className="topic-picker-count">{query.trim() ? `匹配 ${filtered.length} 个课题` : `${topics.length} 个课题`}</div>
    </div>}
  </div>
}
