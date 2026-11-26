import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { HISTORY_API, historyJSON, historyPage, historyRequest, versionDate, versionName } from './historyApi'
import type { GenerationMode, ResumeOption, ResumeVersion, VersionDetail } from './historyApi'
import { useResumePDF } from './useResumePDF'
import { ConfirmDialog, EmptyState, Icon, LoadingSkeleton, ResumePreview } from './workspaceUI'
import type { Recommendation } from './ReuseRecommendations'

import { motion, useReducedMotion } from 'motion/react'
const PdfThumbnail = lazy(() => import('./PdfThumbnail'))

const CONTROL = 'rounded-md border border-stone-400 bg-white px-3 py-2 text-sm'
const BUTTON = 'rounded-md border border-stone-300 px-3 py-2 text-sm font-semibold text-teal-800 hover:bg-stone-100 disabled:opacity-60'
const MODES: Record<GenerationMode, string> = { ats: 'ATS-friendly PDF', original_layout: 'Original-layout PDF' }
const PAGE_SIZE = 20

async function collectVersions(query: URLSearchParams, signal: AbortSignal, include: (version: ResumeVersion) => boolean) {
  const versions: ResumeVersion[] = []
  const batchSize = 100
  query.set('limit', String(batchSize))
  for (let offset = 0; ; offset += batchSize) {
    query.set('offset', String(offset))
    const data = await historyPage(query, signal)
    versions.push(...data.versions.filter(include))
    if (offset + batchSize >= data.total) break
  }
  return versions
}

async function filteredPage(query: URLSearchParams, role: string, page: number, signal: AbortSignal, names: ResumeOption[]) {
  const sortByDisplayedName = query.get('sort') === 'name'
  const search = (query.get('q') ?? '').trim().toLowerCase()
  if (!role && !sortByDisplayedName && !search) return historyPage(query, signal)
  const title = role.toLowerCase()
  const filenames = new Map(names.map(({ id, original_filename }) => [id, original_filename]))
  query.delete('q')
  const matching = await collectVersions(query, signal, (version) => (
    (!title || Boolean(version.job_title?.toLowerCase().includes(title)))
    && (!search || [version.name, version.original_filename, filenames.get(version.resume_id), version.job_title, ...(version.skills ?? []), ...(version.keywords ?? [])]
      .some((value) => value?.toLowerCase().includes(search)))
  ))
  if (sortByDisplayedName) matching.sort((left, right) => versionName(left, names).localeCompare(versionName(right, names)))
  return { versions: matching.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE), total: matching.length }
}

export default function MyResumes({ selectedId, onRemove, timeline, names, searchRequest, onOpenMatch, onChange, recommendation }: {
  selectedId?: string; onRemove: (generationId: string) => void
  timeline: boolean; names: ResumeOption[]; searchRequest: { query: string; revision: number }
  onOpenMatch: (version: ResumeVersion) => void; onChange: () => void
  recommendation?: Recommendation
}) {
  const [list, setList] = useState({ versions: [] as ResumeVersion[], total: 0 })
  const [page, setPage] = useState(0)
  const [detail, setDetail] = useState<VersionDetail>()
  const [detailError, setDetailError] = useState<{ id: string; message: string }>()
  const [search, setSearch] = useState(searchRequest.query)
  const [role, setRole] = useState('')
  const [view, setView] = useState<'grid' | 'list' | 'carousel'>('grid')
  const track = useRef<HTMLUListElement>(null)
  const [position, setPosition] = useState(0)

  function goTo(index: number) {
    const rail = track.current
    const card = rail?.children[index]
    if (!rail || !(card instanceof HTMLElement)) return
    rail.scrollTo({ left: card.offsetLeft - (rail.clientWidth - card.offsetWidth) / 2,
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })
  }
  const [mode, setMode] = useState('')
  const [favorites, setFavorites] = useState(false)
  const [sort, setSort] = useState('newest')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const base = timeline ? '#resume-history' : '#my-resumes'

  useEffect(() => {
    setSearch(searchRequest.query)
    setPage(0)
  }, [searchRequest])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    const query = new URLSearchParams({ q: search, sort, offset: String(page * PAGE_SIZE), limit: String(PAGE_SIZE) })
    if (mode) query.set('mode', mode)
    if (favorites) query.set('favorite', 'true')
    const debounce = window.setTimeout(() => {
      filteredPage(query, role.trim(), page, controller.signal, names)
        .then((data) => {
          if (controller.signal.aborted) return
          const lastPage = Math.max(0, Math.ceil(data.total / PAGE_SIZE) - 1)
          if (page > lastPage) setPage(lastPage)
          else { setList(data); setPosition(0) }
        })
        .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message) })
        .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    }, 150)
    return () => {
      window.clearTimeout(debounce)
      controller.abort()
    }
  }, [search, role, mode, favorites, sort, page, revision, names])

  useEffect(() => {
    if (!selectedId) return
    const id = selectedId
    const controller = new AbortController()
    setDetail(undefined)
    setDetailError(undefined)
    historyRequest<VersionDetail>(`${HISTORY_API}/${encodeURIComponent(id)}`, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setDetail(data) })
      .catch((failure: Error) => { if (!controller.signal.aborted) setDetailError({ id, message: failure.message }) })
    return () => controller.abort()
  }, [selectedId])

  const selected = detail?.generation_id === selectedId ? detail : undefined
  const selectionError = detailError?.id === selectedId ? detailError?.message : undefined
  const pageCount = Math.ceil(list.total / PAGE_SIZE)

  return (
    <section aria-labelledby="history-title" className="resume-management">
      <header className="management-heading">
        <div><h1 id="history-title">{timeline ? 'Resume History' : 'Resume Library'}</h1>
          <p>{timeline ? 'Open or compare saved versions of your resumes.' : 'Browse your saved ATS and original-layout PDFs.'}</p></div>
        <a href={timeline ? '#my-resumes' : '#resume-history'} className="secondary-button">
          <Icon name={timeline ? 'folder' : 'history'} size={17} />{timeline ? 'Resume Library' : 'View History'}
        </a>
      </header>
      {error && (
        <div className="mt-3 flex items-center gap-3">
          <p role="alert" className="text-sm text-red-700">{error}</p>
          <button className={BUTTON} type="button" onClick={() => setRevision((current) => current + 1)}>Retry</button>
        </div>
      )}
      {selectedId && !selected && (selectionError
        ? <p role="alert" className="mt-3 text-sm text-red-700">{selectionError}</p>
        : <p role="status" className="mt-3 text-sm text-stone-600">Loading selected resume…</p>)}
      {selected && (
        <SavedVersion key={selected.generation_id} version={selected} names={names} closeHref={base} timeline={timeline}
          onOpenMatch={() => onOpenMatch(selected)} onUpdate={(updated) => {
          setDetail(updated)
          setRevision((current) => current + 1)
          onChange()
        }} onDelete={() => {
          setDetail(undefined)
          setRevision((current) => current + 1)
          onRemove(selected.generation_id)
          onChange()
          location.hash = base
        }} />
      )}
      <div className="surface library-controls">
        <label className="grid gap-1 text-sm font-medium">
          Search resumes
          <input type="search" className={CONTROL} value={search} maxLength={255}
            onChange={(event) => { setSearch(event.target.value); setPage(0) }} />
        </label>
        <label className="grid gap-1 text-sm font-medium">
          Export mode
          <select className={CONTROL} value={mode} onChange={(event) => { setMode(event.target.value); setPage(0) }}>
            <option value="">All modes</option>
            <option value="ats">{MODES.ats}</option>
            <option value="original_layout">{MODES.original_layout}</option>
          </select>
        </label>
        <label className="grid gap-1 text-sm font-medium">
          Sort by
          <select className={CONTROL} value={sort} onChange={(event) => { setSort(event.target.value); setPage(0) }}>
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="name">Name</option>
            <option value="favorites">Favorites first</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm font-medium">
          <input type="checkbox" checked={favorites}
            onChange={(event) => { setFavorites(event.target.checked); setPage(0) }} />
          Favorites only
        </label>
        <label className="grid gap-1 text-sm font-medium">
          Target role
          <input className={CONTROL} value={role} maxLength={255} placeholder="Filter by job title"
            onChange={(event) => { setRole(event.target.value); setPage(0) }} />
        </label>
        {!timeline && <div className="view-toggle" role="group" aria-label="Library layout">
          <button className="secondary-button" aria-pressed={view === 'carousel'} onClick={() => { setView('carousel'); setPosition(0) }}>Carousel</button>
          <button className="icon-button" aria-label="Grid view" type="button" aria-pressed={view === 'grid'} onClick={() => setView('grid')}><Icon name="dashboard" size={17} /></button>
          <button className="icon-button" aria-label="List view" type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}><Icon name="list" size={17} /></button>
        </div>}
      </div>
      {loading && <LoadingSkeleton label="Loading saved resumes" />}
      {!loading && !error && list.total === 0 && <EmptyState icon={timeline ? 'history' : 'folder'}
        title={search.trim() || role.trim() || mode || favorites ? 'No saved resumes match these filters' : 'No saved resumes yet'}>
        {search.trim() || role.trim() || mode || favorites ? 'Try adjusting your search or filters.' : <a href="#resume-matching">Open AI Resume Studio to create a PDF</a>}
      </EmptyState>}
      {!loading && !error && list.versions.length > 0 && (
        <>
          {!timeline && view === 'carousel' && <div className="rail-controls" aria-label="Carousel navigation">
            <button className="secondary-button" disabled={position === 0} onClick={() => goTo(position - 1)}>Previous resume</button>
            <span aria-live="polite">{position + 1} / {list.versions.length}</span>
            <button className="secondary-button" disabled={position >= list.versions.length - 1} onClick={() => goTo(position + 1)}>Next resume</button>
          </div>}
          <ul ref={track} className={timeline ? 'history-list' : 'library-' + view}
            onKeyDown={event => {
              if (timeline || view !== 'carousel' || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return
              event.preventDefault()
              const next = Math.max(0, Math.min(list.versions.length - 1, position + (event.key === 'ArrowRight' ? 1 : -1)))
              track.current?.children[next]?.querySelector<HTMLElement>('.resume-card-title')?.focus({ preventScroll: true })
              goTo(next)
            }}
            onScroll={event => {
              if (timeline || view !== 'carousel') return
              const rail = event.currentTarget
              const center = rail.scrollLeft + rail.clientWidth / 2
              const distances = Array.from(rail.children, child => {
                const card = child as HTMLElement
                return Math.abs(card.offsetLeft + card.offsetWidth / 2 - center)
              })
              setPosition(distances.indexOf(Math.min(...distances)))
            }}>
            {list.versions.map((item, index) => (
              <ResumeCard key={item.generation_id} item={item} onEdit={() => onOpenMatch(item)} foreground={index === position} thumbnail={timeline || view !== 'list'} filename={item.original_filename ?? names.find(option => option.id === item.resume_id)?.original_filename} title={versionName(item, names)} timeline={timeline}
                href={`${base}/${item.generation_id}`} active={item.generation_id === selectedId}
                score={recommendation && (!timeline || recommendation.job_id === item.job_id)
                  ? recommendation.candidates.find((candidate) => candidate.generation_id === item.generation_id)?.keyword_coverage.percentage
                  : undefined} />
            ))}
          </ul>
          {pageCount > 1 && (
            <nav aria-label="Saved resumes pages" className="mt-4 flex flex-wrap items-center gap-3 text-sm">
              <button type="button" className={BUTTON} disabled={page === 0}
                onClick={() => setPage((current) => current - 1)}>Previous</button>
              <span>Page {page + 1} of {pageCount}</span>
              <button type="button" className={BUTTON} disabled={page + 1 >= pageCount}
                onClick={() => setPage((current) => current + 1)}>Next</button>
            </nav>
          )}
        </>
      )}
    </section>
  )
}

function ResumeCard({ item, onEdit, foreground, thumbnail, filename, title, href, active, timeline, score }: {
  onEdit: () => void; foreground: boolean; thumbnail: boolean; filename?: string | null; item: ResumeVersion; title: string; href: string; active: boolean; timeline: boolean; score?: number | null
}) {
  const card = useRef<HTMLLIElement>(null)
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    if (!thumbnail || !card.current || visible) return
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect() }
    })
    observer.observe(card.current)
    return () => observer.disconnect()
  }, [thumbnail, visible])
  const pdf = useResumePDF(thumbnail && visible && item.status === 'available' ? item.generation_id : undefined, 'history')
  return <li ref={card} className="resume-card" data-selected={active} data-front={foreground}
    onPointerMove={event => {
      if (!thumbnail || event.pointerType !== 'mouse' || matchMedia('(prefers-reduced-motion: reduce)').matches) return
      const bounds = event.currentTarget.getBoundingClientRect()
      event.currentTarget.style.setProperty('--tilt', ((event.clientX - bounds.left) / bounds.width * 9 - 4.5) + 'deg')
      event.currentTarget.style.setProperty('--pitch', (3 - (event.clientY - bounds.top) / bounds.height * 6) + 'deg')
    }} onPointerLeave={event => {
      event.currentTarget.style.removeProperty('--tilt')
      event.currentTarget.style.removeProperty('--pitch')
    }}>
    {thumbnail && <a className="mini-stage" href={href} aria-label={'Preview ' + title}>
      <span className="mini-sheet" aria-hidden="true">{pdf?.url
        ? <Suspense fallback={<small>Loading page...</small>}><PdfThumbnail url={pdf.url}/></Suspense>
        : <><Icon name="file" /><small>{item.status === 'unavailable' || pdf?.error ? 'Preview unavailable' : 'Saved PDF'}</small></>}</span>
    </a>}
    {!thumbnail && <span className="icon-chip"><Icon name={timeline ? 'history' : 'file'} size={19} /></span>}
    <div className="min-w-0">
      <a className="resume-card-title" href={href} aria-current={active ? 'true' : undefined}>{title}</a>
      <p>{MODES[item.mode]} · {versionDate(item.created_at)}{item.favorite && ' · Favorite'}{item.status === 'unavailable' && ' · PDF unavailable'}</p>
      {filename && filename !== title && <p>{filename}</p>}
      {item.job_title && <p>{item.job_title}</p>}
      {score != null && <p className="library-score">{score}% estimated match{!timeline && ' · Latest JD'}</p>}
      {timeline ? <p>Version {item.generation_id.slice(0, 8)}</p>
        : !!item.skills?.length && <p className="library-skills">{item.skills.join(', ')}</p>}
      {item.status === 'available' && <a className="text-action" href={href}>Preview and download</a>}
      {item.job_id && <button className="text-action" onClick={onEdit}>Edit in Studio</button>}
      {!timeline && <a className="text-action history-link" href={`#resume-history/${item.generation_id}`}>View History</a>}
    </div>
  </li>
}

function SavedVersion({ version, closeHref, timeline, names, onOpenMatch, onUpdate, onDelete }: {
  version: VersionDetail; closeHref: string; timeline: boolean; names: ResumeOption[]
  onOpenMatch: () => void; onUpdate: (version: VersionDetail) => void; onDelete: () => void
}) {
  const reduceMotion = useReducedMotion()
  const [name, setName] = useState(version.name ?? '')
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const heading = useRef<HTMLHeadingElement>(null)
  const deleteButton = useRef<HTMLButtonElement>(null)
  const pdf = useResumePDF(version.status === 'available' ? version.generation_id : undefined, 'history')
  const endpoint = `${HISTORY_API}/${version.generation_id}`

  useEffect(() => {
    heading.current?.focus()
  }, [])

  async function update(options: RequestInit) {
    setBusy(true)
    setError('')
    setStatus('')
    try {
      if (options.method === 'DELETE') {
        await historyRequest<void>(endpoint, options)
        onDelete()
      } else {
        const updated = await historyRequest<VersionDetail>(endpoint, options)
        onUpdate(updated)
        setName(updated.name ?? '')
        setStatus('Saved resume updated.')
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not update the saved resume.')
      setConfirm(false)
    } finally {
      setBusy(false)
    }
  }

  function rename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void update(historyJSON('PATCH', { name: name.trim() || null }))
  }

  return (
    <motion.article className="edition-reader mt-4 space-y-3" initial={reduceMotion ? false : { opacity: .3, y: 12, scale: .98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ duration: .24 }}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 ref={heading} tabIndex={-1} className="text-lg font-semibold wrap-anywhere">{versionName(version, names)}</h3>
        <a href={closeHref} className="text-sm text-teal-800 underline">Close preview</a>
      </div>
      <p className="text-sm text-stone-600">
        {MODES[version.mode]} · {version.page_count} {version.page_count === 1 ? 'page' : 'pages'} · {versionDate(version.created_at)}
      </p>
      <div className="edition-spread">
      {pdf?.url && <ResumePreview url={pdf.url} title="Saved resume PDF preview" />}
      <div className="edition-settings space-y-3">
      <details className="text-sm">
        <summary className="cursor-pointer">Source details</summary>
        <dl className="mt-2 grid gap-1 wrap-anywhere">
          <div><dt className="inline font-semibold">Original resume </dt><dd className="inline">{version.resume_id}</dd></div>
          <div><dt className="inline font-semibold">Job </dt><dd className="inline">{version.job_title ?? version.job_id ?? 'Unavailable'}</dd></div>
          <div><dt className="inline font-semibold">Comparison </dt><dd className="inline">{version.match_id}</dd></div>
        </dl>
      </details>
      <form onSubmit={rename} className="flex flex-wrap items-end gap-2">
        <label className="grid min-w-0 flex-1 gap-1 text-sm font-medium">
          Resume name
          <input className={CONTROL} placeholder={version.job_title ?? 'Untitled resume'} value={name} maxLength={120}
            onChange={(event) => setName(event.target.value)} />
        </label>
        <button type="submit" className={BUTTON} disabled={busy || name.trim() === (version.name ?? '')}>Save name</button>
      </form>
      <div className="flex flex-wrap gap-2">
        {version.job_id && <button type="button" className={BUTTON} onClick={onOpenMatch}>Open in Studio</button>}
        <button type="button" className={BUTTON} disabled={busy} aria-pressed={version.favorite}
          onClick={() => void update(historyJSON('PATCH', { favorite: !version.favorite }))}>
          {version.favorite ? 'Remove favorite' : 'Mark as favorite'}
        </button>
        <button type="button" ref={deleteButton} className={BUTTON} disabled={busy} onClick={() => setConfirm(true)}>Delete version</button>
      </div>
      {confirm && <ConfirmDialog title="Delete saved version?" busy={busy}
        onConfirm={() => void update({ method: 'DELETE' })} onCancel={() => {
          setConfirm(false)
          requestAnimationFrame(() => deleteButton.current?.focus())
        }}>This export will be removed from your library and history.</ConfirmDialog>}
      {status && <p role="status" className="text-sm text-teal-800">{status}</p>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {version.status === 'unavailable' && <p role="status" className="text-sm">The saved PDF file is unavailable.</p>}
      {version.status === 'available' && !pdf && <p role="status" className="text-sm">Loading PDF…</p>}
      {pdf?.error && <p role="alert" className="text-sm text-red-700">{pdf.error}</p>}
      {pdf?.url && (
        <>
          <a className={`${BUTTON} inline-block`} href={pdf.url}
            download={`${versionName(version, names).replace(/\.(pdf|docx)$/i, '')}.pdf`}>Download PDF</a>

        </>
      )}
      </div></div>
      {timeline && <VersionComparison version={version} names={names} />}
    </motion.article>
  )
}

function VersionComparison({ version, names }: { version: VersionDetail; names: ResumeOption[] }) {
  const [id, setId] = useState('')
  const [alternatives, setAlternatives] = useState<{ versions?: ResumeVersion[]; error?: string }>()
  const [loaded, setLoaded] = useState<{ id: string; data?: VersionDetail; error?: string }>()
  const comparison = loaded?.id === id ? loaded : undefined
  const hasContent = Boolean(version.snapshot?.content)

  useEffect(() => {
    if (!hasContent) return
    const controller = new AbortController()
    collectVersions(new URLSearchParams(), controller.signal, (item) => (
      item.resume_id === version.resume_id && item.generation_id !== version.generation_id
    ))
      .then((versions) => { if (!controller.signal.aborted) setAlternatives({ versions }) })
      .catch((failure: Error) => { if (!controller.signal.aborted) setAlternatives({ error: failure.message }) })
    return () => controller.abort()
  }, [hasContent, version.resume_id, version.generation_id])

  useEffect(() => {
    if (!id) return
    const controller = new AbortController()
    historyRequest<VersionDetail>(`${HISTORY_API}/${encodeURIComponent(id)}`, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setLoaded({ id, data }) })
      .catch((failure: Error) => { if (!controller.signal.aborted) setLoaded({ id, error: failure.message }) })
    return () => controller.abort()
  }, [id])

  if (!version.snapshot?.content) return <p className="text-sm text-stone-600">Saved text is unavailable for comparison.</p>
  if (!alternatives) return <LoadingSkeleton label="Loading comparison versions" />
  if (alternatives.error) return <p role="alert" className="text-sm text-red-700">{alternatives.error}</p>
  const options = alternatives.versions ?? []
  if (!options.length) return <p className="text-sm text-stone-600">Save another export from this source resume to compare versions.</p>
  const left = version.snapshot.content.sections
  const right = comparison?.data?.snapshot?.content?.sections
  const changed = right && [...new Set([...Object.keys(left), ...Object.keys(right)])]
    .filter((section) => JSON.stringify(left[section] ?? []) !== JSON.stringify(right[section] ?? []))

  return <section className="surface version-comparison">
    <h4 className="font-semibold">Compare saved versions</h4>
    <label className="grid gap-1 mt-2 text-sm font-medium">Compare with
      <select className={CONTROL} value={id} onChange={(event) => { setLoaded(undefined); setId(event.target.value) }}>
        <option value="">Choose a version</option>
        {options.map((item) => <option key={item.generation_id} value={item.generation_id}>
          {versionName(item, names)} · {versionDate(item.created_at)} · {item.generation_id.slice(0, 8)}
        </option>)}
      </select>
    </label>
    {id && !comparison && <LoadingSkeleton label="Loading saved version" />}
    {comparison?.error && <p role="alert">{comparison.error}</p>}
    {comparison?.data && !right && <p>Saved text is unavailable for this version.</p>}
    {changed?.length === 0 && <p className="mt-2 text-sm">These versions have identical section text.</p>}
    {right && changed?.map((section) => <div className="mt-3" key={section}>
      <h5 className="capitalize font-semibold">{section.replaceAll('_', ' ')}</h5>
      <div className="comparison-grid mt-2">
        {[left, right].map((content, index) => <div key={index} className="surface">
          <strong>{index === 0 ? 'Open version' : 'Comparison version'}</strong>
          <p className="whitespace-pre-wrap wrap-anywhere text-sm">{content[section]?.join('\n') || 'No text in this section'}</p>
        </div>)}
      </div>
    </div>)}
  </section>
}
