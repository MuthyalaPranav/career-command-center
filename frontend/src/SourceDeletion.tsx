import { useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { FileText, Trash2 } from 'lucide-react'
import { historyRequest } from './historyApi'
import { ConfirmDialog } from './workspaceUI'

type Item = { id: string; label: string; detail?: string | null }

export default function SourceDeletion({ kind, items, onDelete, actions }: {
  kind: 'resumes' | 'jobs'
  actions?: (id: string) => ReactNode
  items: Item[]
  onDelete: (id: string) => void
}) {
  const resume = kind === 'resumes'
  const [query, setQuery] = useState('')
  const [companyOnly, setCompanyOnly] = useState(false)
  const visible = resume ? items : items.filter(item =>
    (!companyOnly || item.detail?.includes(' / ')) &&
    [item.label, item.detail].some(text => text?.toLowerCase().includes(query.trim().toLowerCase())))
  const noun = resume ? 'uploaded resume' : 'job analysis'
  const [selected, setSelected] = useState<Item>()
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const trigger = useRef<HTMLButtonElement>(null)
  const heading = useRef<HTMLElement>(null)

  async function remove(item: Item) {
    setBusy(true)
    try {
      await historyRequest<void>(`/api/${kind}/${encodeURIComponent(item.id)}`, { method: 'DELETE' })
      onDelete(item.id)
      setStatus(`Deleted ${item.label} from active sources.`)
      requestAnimationFrame(() => heading.current?.focus())
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : `Could not delete the ${noun}.`)
      requestAnimationFrame(() => trigger.current?.focus())
    } finally {
      setBusy(false)
      setSelected(undefined)
    }
  }

  return <details open className="source-management mt-4 border-t border-stone-200 pt-3">
    <summary ref={heading} className="cursor-pointer font-semibold">
      {resume ? 'Manage uploaded resumes' : 'Manage saved JD analyses'} ({items.length})
    </summary>
    {items.length === 0 && <p className="mt-2 text-sm text-stone-600">No {resume ? 'active uploads' : 'active job analyses'}.</p>}
    {!resume && items.length > 0 && <div className="jd-filters"><label>Search saved jobs<input type="search" value={query}
      onChange={e => setQuery(e.target.value)} placeholder="Job title or company" /></label>
      <label><input type="checkbox" checked={companyOnly} onChange={e => setCompanyOnly(e.target.checked)} /> With company information</label></div>}
    {!resume && items.length > 0 && !visible.length && <p>No jobs match these filters.</p>}
    <ul className="divide-y divide-stone-200">
      {visible.map(item => <li key={item.id} className="flex items-center justify-between gap-3 py-2">
        <FileText size={20} aria-hidden="true" /><span className="source-name min-w-0 wrap-anywhere text-sm">{item.label}
          {item.detail && <small className="block text-stone-600">{item.detail}</small>}</span>
        {actions?.(item.id)}<button type="button" className="source-delete" aria-label={`Delete ${item.label}`} onClick={event => {
          trigger.current = event.currentTarget
          setStatus('')
          setError('')
          setSelected(item)
        }}><Trash2 size={15} aria-hidden="true" />Delete</button>
      </li>)}
    </ul>
    {status && <p role="status" className="text-sm text-teal-800">{status}</p>}
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {selected && <ConfirmDialog title={`Delete this ${noun}?`} busy={busy} onConfirm={() => void remove(selected)}
      onCancel={() => {
        setSelected(undefined)
        requestAnimationFrame(() => trigger.current?.focus())
      }}>
      <strong className="block wrap-anywhere">{selected.label}</strong>
      This removes it from active selection. {resume ? 'Generated and edited resumes in Resume History remain saved.' : 'Previously generated resumes remain saved.'}
    </ConfirmDialog>}
  </details>
}
