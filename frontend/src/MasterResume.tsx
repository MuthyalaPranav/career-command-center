import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { historyJSON, historyRequest, uploadLabel, versionDate } from './historyApi'
import type { ActiveMaster, ResumeOption } from './historyApi'
import SourceDeletion from './SourceDeletion'
import ResumeExtraction from './ResumeExtraction'
import type { Match } from './ResumeEditor'
import { apiFetch } from './resumeApi'
import { EmptyState, Icon, LoadingSkeleton, ResumePreview } from './workspaceUI'

const MASTER_API = '/api/resumes/master'

export const getMaster = (signal: AbortSignal) => historyRequest<ActiveMaster | null>(MASTER_API, { signal })
export const getMasterOptions = (signal: AbortSignal) => historyRequest<ResumeOption[]>(`${MASTER_API}/options`, { signal })

export default function MasterResume({
  master, options, suggested, match, visible, uploadedFile, onChange, onRename, onDelete, onExtracted, loading, loadError, children,
}: {
  master: ActiveMaster | null
  options: ResumeOption[]
  suggested: string
  match: Match | null
  visible: boolean
  uploadedFile?: File
  onChange: (master: ActiveMaster) => void
  onDelete: (id: string) => void
  onRename: (resume: ResumeOption) => void
  onExtracted: () => void
  loading: boolean
  loadError: string
  children: ReactNode
}) {
  const [selection, setSelection] = useState<{ id: string; suggestion: string }>()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const preferred = selection?.suggestion === suggested ? selection.id : suggested
  const selected = options.some(option => option.id === preferred) ? preferred : master?.resume_id ?? ''
  const selectedOption = options.find(({ id }) => id === selected)

  const previewPanel = useRef<HTMLDetailsElement>(null)
  const [renameId, setRenameId] = useState('')
  function inspect(id: string, rename = false) {
    setSelection({ id, suggestion: suggested })
    setRenameId(rename ? id : '')
    if (rename) requestAnimationFrame(() => {
      const input = previewPanel.current?.querySelector<HTMLInputElement>('form input')
      const details = input?.closest('details')
      if (details) details.open = true
      input?.focus()
    })
    if (previewPanel.current) {
      previewPanel.current.open = true
      previewPanel.current.scrollIntoView({ block: 'start' })
      previewPanel.current.querySelector<HTMLElement>('summary')?.focus()
    }
  }
  async function save(event: FormEvent<HTMLFormElement> | null, id = selected) {
    event?.preventDefault()
    if (!id || busy) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const saved = await historyRequest<ActiveMaster>(MASTER_API, historyJSON('PUT', { resume_id: id }))
      onChange(saved)
      setSelection({ id: saved.resume_id, suggestion: suggested })
      setMessage('Active master resume saved.')
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not save the master resume.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section id="active-master" aria-labelledby="active-master-title" className="surface master-panel">
      <h2 id="active-master-title" className="text-lg font-semibold">My Resumes</h2>
      <p className="mt-2 text-sm text-stone-700 wrap-anywhere">
        {loading ? 'Loading the active master resume…' : master
          ? `Active resume · ${options.find(({ id }) => id === master.resume_id)?.label ?? uploadLabel(master.resume_id)}`
          : 'Choose an uploaded resume to use as your active master.'}
      </p>
      <form className="mt-3 flex flex-wrap items-end gap-2" onSubmit={event => void save(event)}>
        <label className="grid min-w-0 flex-1 gap-1 text-sm font-medium">
          Choose an uploaded resume
          <select className="w-full min-w-0 rounded-md border border-stone-400 bg-white px-3 py-2 text-sm"
            value={selectedOption?.id ?? ''} onChange={(event) => setSelection({ id: event.target.value, suggestion: suggested })} disabled={busy}>
            <option value="">Select a resume</option>
            {options.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
          </select>
        </label>
        <button type="submit" disabled={loading || busy || !selectedOption || selected === master?.resume_id}
          className="rounded-md bg-teal-700 px-3 py-2 text-sm font-semibold text-white hover:bg-teal-800 disabled:opacity-60">
          {busy ? 'Saving…' : 'Set as active master'}
        </button>
      </form>
      <SourceDeletion kind="resumes" items={options.map(option => ({
        id: option.id, label: option.original_filename ?? option.label,
        detail: [option.original_filename?.split('.').pop()?.toUpperCase(), option.id === master?.resume_id ? 'Active master' : ''].filter(Boolean).join(' / '),
      }))} actions={id => <div className="source-actions">
        <button onClick={() => inspect(id)}>Preview</button><button onClick={() => inspect(id, true)}>Rename</button>
        {id !== master?.resume_id && <button disabled={busy} onClick={() => void save(null, id)}>Set as master</button>}
      </div>} onDelete={onDelete} />
      {message && <p role="status" className="mt-2 text-sm text-teal-800">{message}</p>}
      {(error || loadError) && <p role="alert" className="mt-2 text-sm text-red-700">{error || loadError}</p>}
      {loading && <LoadingSkeleton label="Loading master resume" />}
      {children}
      <details ref={previewPanel} className="source-preview-panel"><summary>Selected resume preview and details</summary>
      <div className="master-details">
        <div className="master-assets">
          {visible && selectedOption && <MasterPreview key={`${selectedOption.id}-${match?.match_id ?? ''}`} resume={selectedOption}
            file={selected === suggested ? uploadedFile : undefined}
            matchId={match?.resume_id === selected ? match.match_id : undefined} />}
        </div>
        {selectedOption ? <UploadDetails key={selectedOption.id} renameOpen={renameId === selectedOption.id} resume={selectedOption} onRename={onRename}
          onExtracted={selected === master?.resume_id ? undefined : onExtracted} active={visible && selected === master?.resume_id} />
          : !loading && !loadError && <EmptyState icon="file" title="No resume selected">Upload a PDF or DOCX to get started.</EmptyState>}
      </div></details>
    </section>
  )
}

function MasterPreview({ resume, matchId, file }: { resume: ResumeOption; matchId?: string; file?: File }) {
  const [preview, setPreview] = useState<{ url?: string; error?: string }>()
  const docx = resume.original_filename?.toLowerCase().endsWith('.docx')

  useEffect(() => {
    if (file) {
      const url = URL.createObjectURL(docx ? file : file.slice(0, file.size, 'application/pdf'))
      setPreview({ url })
      return () => URL.revokeObjectURL(url)
    }
    if (!matchId || docx) return
    const controller = new AbortController()
    let url = ''
    apiFetch(`/api/resumes/${encodeURIComponent(resume.id)}/original-pdf?match_id=${encodeURIComponent(matchId)}`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok || response.headers.get('Content-Type') !== 'application/pdf') throw new Error('Original PDF unavailable.')
        return response.blob()
      })
      .then((blob) => {
        if (controller.signal.aborted) return
        url = URL.createObjectURL(blob)
        setPreview({ url })
      })
      .catch(() => {
        if (!controller.signal.aborted) setPreview({ error: 'This source file cannot be previewed as an original PDF.' })
      })
    return () => {
      controller.abort()
      if (url) URL.revokeObjectURL(url)
    }
  }, [resume.id, matchId, docx, file])

  return <div className="master-preview">
    <span className="icon-chip"><Icon name="file" /></span>
    <h3>{resume.label}</h3>
    <p>{resume.original_filename ?? 'Original filename unavailable'}</p>
    <p>{file?.lastModified ? `Source file modified · ${versionDate(new Date(file.lastModified).toISOString())}` : 'Last updated date unavailable'}</p>
    {preview?.url ? <>
      <a className="secondary-button" href={preview.url} download={file?.name ?? resume.original_filename ?? `${resume.label}.pdf`}>Download original {docx ? 'DOCX' : 'PDF'}</a>
      {docx ? <p>Review the extracted DOCX sections alongside this panel.</p>
        : <ResumePreview title="Original master PDF preview" url={preview.url} />}
    </> : <p>{docx ? 'Review the extracted DOCX sections alongside this panel.'
        : preview?.error ?? (matchId ? 'Loading original PDF…' : 'Compare this resume with a JD to open its original PDF.')}</p>}
  </div>
}

function UploadDetails({ resume, renameOpen, active, onRename, onExtracted }: {
  resume: ResumeOption; renameOpen: boolean; active: boolean; onRename: (resume: ResumeOption) => void; onExtracted?: () => void
}) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  async function rename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !name.trim()) return
    setBusy(true)
    setStatus('')
    setError('')
    try {
      const updated = await historyRequest<ResumeOption>(
        `${MASTER_API}/options/${resume.id}`, historyJSON('PATCH', { name: name.trim() }),
      )
      onRename(updated)
      setName('')
      setStatus('Resume name saved.')
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not rename the resume.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-w-0">
    <details key={String(renameOpen)} open={renameOpen} className="mt-3 text-sm">
      <summary className="cursor-pointer font-semibold text-teal-800">Rename or review selected resume</summary>
      <form className="mt-3 flex flex-wrap items-end gap-2" onSubmit={rename}>
        <label className="grid min-w-0 flex-1 gap-1 font-medium">
          Resume display name
          <input value={name} maxLength={255} placeholder={resume.label} onChange={(event) => setName(event.target.value)}
            className="w-full rounded-md border border-stone-400 bg-white px-3 py-2" />
        </label>
        <button type="submit" disabled={busy || !name.trim()}
          className="rounded-md border border-stone-300 px-3 py-2 font-semibold text-teal-800 hover:bg-stone-100 disabled:opacity-60">
          {busy ? 'Saving…' : 'Save resume name'}
        </button>
      </form>
      {status && <p role="status" className="mt-2 text-teal-800">{status}</p>}
      {error && <p role="alert" className="mt-2 text-red-700">{error}</p>}
    </details>
    <ResumeExtraction resumeId={resume.id} autoLoad={active} onExtracted={onExtracted} />
    </div>
  )
}
