import { useEffect, useId, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import SourceDeletion from './SourceDeletion'
import ATSResumeGenerator from './ATSResumeGenerator'
import { uploadLabel } from './historyApi'
import type { ResumeOption } from './historyApi'
import { apiFetch } from './resumeApi'
import ResumeEditor, { editorSuggestions } from './ResumeEditor'
import type { Match } from './ResumeEditor'
import { EmptyState, LoadingSkeleton } from './workspaceUI'

const MATCH_API = '/api/matches'
const SAVED_MATCH_KEY = 'resume-last-match'
const BUTTON = 'rounded-md border border-stone-300 px-3 py-2 text-sm font-semibold hover:bg-stone-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 disabled:cursor-not-allowed disabled:opacity-50'
const PRIMARY = `${BUTTON} border-teal-700 bg-teal-700 text-white hover:bg-teal-800`
const SELECT = 'mt-1 block w-full min-w-0 rounded-md border border-stone-400 bg-white p-2 text-sm focus-visible:outline-2 focus-visible:outline-teal-700 disabled:opacity-60'
type Sources = { resumes: ResumeOption[]; jobs: (ResumeOption & { company_name?: string | null })[] }
type ComparisonTarget = { resumeId: string; jobId: string; run?: boolean; savedMatchId?: string }
type MatchMode = 'analyzer' | 'studio' | 'combined'

async function matchRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await apiFetch(`${MATCH_API}${path}`, options)
  const data = await response.json().catch(() => null)
  if (!response.ok || !data) {
    throw new Error(typeof data?.detail === 'string' ? data.detail : 'The request failed. Check your selections and try again.')
  }
  return data as T
}

function jsonRequest(method: string, data: object): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }
}

export default function MatchDashboard({ jobsOnly, exporting, matchOnly, archivedResume, archivedJob, onDeleteJob, masterId, comparison, deletedIds, resumeNames, mode, sourceRevision, onMatch, onLoading, onExport }: {
  jobsOnly: boolean; exporting: boolean; matchOnly: boolean
  archivedResume: string; archivedJob: string; onDeleteJob: (id: string) => void
  masterId?: string; comparison?: ComparisonTarget; deletedIds: string[]; resumeNames: ResumeOption[]
  mode: MatchMode; sourceRevision: number; onMatch: (match: Match | null) => void; onLoading: (loading: boolean) => void
  onExport: () => void
}) {
  const heading = useId()
  const resumeField = useId()
  const jobField = useId()
  const [sources, setSources] = useState<Sources>({ resumes: [], jobs: [] })
  const [resumeId, setResumeId] = useState('')
  const [jobId, setJobId] = useState('')
  const [match, setMatch] = useState<Match | null>(null)
  const [loadingSources, setLoadingSources] = useState(true)
  const [busy, setBusy] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const initialized = useRef(false)
  const handledTarget = useRef<ComparisonTarget>(undefined)
  const pendingMutation = useRef(0)

  async function refreshSources(signal?: AbortSignal, target?: ComparisonTarget) {
    setLoadingSources(true)
    setError('')
    try {
      const next = await matchRequest<Sources>('/sources', { signal })
      if (signal?.aborted) return
      setSources(next)
      setResumeId((current) => next.resumes.some((option) => option.id === current) ? current : masterId ?? '')
      setJobId((current) => next.jobs.some((option) => option.id === current) ? current : '')
      if (target) {
        ++pendingMutation.current
        setBusy('')
        initialized.current = true
        handledTarget.current = target
        if (target.savedMatchId) {
          const previous = await matchRequest<Match>(`/${encodeURIComponent(target.savedMatchId)}`, { signal })
          if (signal?.aborted) return
          setResumeId(next.resumes.some(option => option.id === previous.resume_id) ? previous.resume_id : '')
          setJobId(next.jobs.some(option => option.id === previous.job_id) ? previous.job_id : '')
          setMatch(previous)
          sessionStorage.setItem(SAVED_MATCH_KEY, previous.match_id)
          setStatus('Saved comparison opened.')
          return
        }
        if (!next.resumes.some(({ id }) => id === target.resumeId) || !next.jobs.some(({ id }) => id === target.jobId)) {
          throw new Error('The master resume or analyzed job description is no longer available.')
        }
        setResumeId(target.resumeId)
        setJobId(target.jobId)
        setMatch(null)
        setStatus('')
        sessionStorage.removeItem(SAVED_MATCH_KEY)
        if (target.run) {
          await run('', jsonRequest('POST', { resume_id: target.resumeId, job_id: target.jobId }), 'Comparing…', 'Comparison saved.', true)
        }
        return
      }
      const saved = signal && !initialized.current && sessionStorage.getItem(SAVED_MATCH_KEY)
      if (saved) {
        const previous = await matchRequest<Match>(`/${encodeURIComponent(saved)}`, { signal })
        if (signal.aborted) return
        setResumeId(next.resumes.some(({ id }) => id === previous.resume_id) ? previous.resume_id : '')
        setJobId(next.jobs.some(({ id }) => id === previous.job_id) ? previous.job_id : '')
        setMatch(previous)
      }
      initialized.current = true
    } catch (failure) {
      if (!signal?.aborted) setError(failure instanceof Error ? failure.message : 'Unable to load saved sources.')
    } finally {
      if (!signal?.aborted) setLoadingSources(false)
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    void refreshSources(controller.signal, comparison !== handledTarget.current ? comparison : undefined)
    return () => controller.abort()
  }, [comparison, sourceRevision])

  useEffect(() => {
    if (!archivedResume && !archivedJob) return
    setSources(current => ({
      resumes: current.resumes.filter(item => item.id !== archivedResume),
      jobs: current.jobs.filter(item => item.id !== archivedJob),
    }))
    setResumeId(current => current === archivedResume ? '' : current)
    setJobId(current => current === archivedJob ? '' : current)
  }, [archivedResume, archivedJob])

  useEffect(() => onMatch(match), [match, onMatch])
  useEffect(() => onLoading(loadingSources), [loadingSources, onLoading])

  useEffect(() => {
    setResumeId((current) => current || masterId || '')
  }, [masterId])

  async function run(path: string, options: RequestInit, progress: string, success: string, replace = false) {
    if (busy && !replace) return 'Another update is in progress.'
    const request = ++pendingMutation.current
    setBusy(progress)
    setError('')
    setStatus('')
    try {
      const result = await matchRequest<Match>(path, options)
      if (request !== pendingMutation.current) return
      setMatch(result)
      sessionStorage.setItem(SAVED_MATCH_KEY, result.match_id)
      setStatus(success)
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : 'Unable to complete the request.'
      if (request !== pendingMutation.current) return
      setError(message)
      return message
    } finally {
      if (request === pendingMutation.current) setBusy('')
    }
  }

  function compare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (resumeId && jobId) {
      void run('', jsonRequest('POST', { resume_id: resumeId, job_id: jobId }), 'Comparing…', 'Comparison saved.')
    }
  }

  function changeSource(update: (value: string) => void, value: string) {
    update(value)
    sessionStorage.removeItem(SAVED_MATCH_KEY)
    setMatch(null)
    setStatus('')
    setError('')
  }

  const approved = editorSuggestions(match).filter((item) => item.approval_status === 'accepted')
  const revision = [match?.match_id, ...approved.map((item) => item.edit_revision
    ? `${item.suggestion_id}~${item.edit_revision}` : item.suggestion_id).sort()].join('|')
  const labels = new Map(resumeNames.map(({ id, label }) => [id, label]))
  const resumeOptions = masterId && !sources.resumes.some(({ id }) => id === masterId)
    ? [{ id: masterId, label: uploadLabel(masterId) }, ...sources.resumes] : sources.resumes

  return (
    <section id="resume-matching" aria-labelledby={heading} className="match-workspace" data-mode={mode}>
      <div className="surface match-source-panel">
      <h2 id={heading} className="text-lg font-semibold">{jobsOnly ? 'Saved job descriptions' : exporting ? 'Export Center' : matchOnly ? 'JD Match Studio' : mode === 'studio' ? 'AI Resume Editor' : 'Resume vs JD matching'}</h2>
      <details hidden={jobsOnly || exporting} key={mode + Boolean(match)} open={mode !== 'studio' || !match}><summary className="source-summary">Comparison sources</summary>
      <p className="mt-2 text-sm text-stone-600">Extract a resume and analyze a job description, then refresh the saved sources.</p>
      <form onSubmit={compare} className="mt-3 max-w-xl space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="min-w-0">
            <label htmlFor={resumeField} className="text-sm font-medium">Extracted resume</label>
            <select id={resumeField} className={SELECT} value={resumeId} disabled={loadingSources || !!busy}
              onChange={(event) => changeSource(setResumeId, event.target.value)}>
              <option value="">Select a resume</option>
              {resumeOptions.map((item) => <option key={item.id} value={item.id}>{labels.get(item.id) ?? item.label}</option>)}
            </select>
          </div>
          <div className="min-w-0">
            <label htmlFor={jobField} className="text-sm font-medium">Analyzed job description</label>
            <select id={jobField} className={SELECT} value={jobId} disabled={loadingSources || !!busy}
              onChange={(event) => changeSource(setJobId, event.target.value)}>
              <option value="">Select a job description</option>
              {sources.jobs.map((item) => <option key={item.id} value={item.id}>{item.label} · {item.id}</option>)}
            </select>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className={BUTTON} type="button" disabled={loadingSources || !!busy} onClick={() => void refreshSources()}>
            {loadingSources ? 'Loading sources…' : 'Refresh sources'}
          </button>
          <button className={PRIMARY} type="submit" disabled={loadingSources || !!busy || !resumeId || !jobId}>
            Compare Resume with JD
          </button>
        </div>
      </form></details>
      <div hidden={mode === 'studio' || matchOnly}><SourceDeletion kind="jobs" items={sources.jobs.map(job => ({
        id: job.id, label: job.label, detail: [job.company_name, job.id.slice(0, 8)].filter(Boolean).join(' / '),
      }))} onDelete={onDeleteJob} /></div>
      <p role="status" className="mt-2 text-sm text-teal-800">{busy || status}</p>
      <p role="alert" className="text-sm text-red-700">{error}</p>
      {loadingSources && <LoadingSkeleton label="Loading saved comparison sources" />}
      </div>
      {jobsOnly && <a className="primary-button mt-4" href="#match-studio">Open JD Match Studio</a>}
      {exporting && !match && <a className="primary-button" href="#match-studio">Choose a resume and job in Match Studio</a>}
      {!jobsOnly && !exporting && !match && !loadingSources && !busy && mode !== 'combined' && <EmptyState icon={mode === 'studio' ? 'spark' : 'target'}
        title={mode === 'studio' ? 'Start with a comparison' : 'Ready to compare'}>
        Choose an extracted resume and an analyzed job description to see the results.
      </EmptyState>}
      <div hidden={jobsOnly || exporting}>{match ? <ResumeEditor key={`editor-${match.match_id}`} match={match} busy={!!busy} mutate={run} mode={mode}>
        {mode === 'studio' ? <a className="primary-button" href="#export-center">Open Export Center</a> : mode === 'combined' && <ATSResumeGenerator key={revision} deletedIds={deletedIds} onExport={onExport}
        disabled={!!busy || loadingSources} source={match && {
          resumeId: match.resume_id, matchId: match.match_id, jobTitle: match.job_title, approvedCount: approved.length, revision,
        }} />}
      </ResumeEditor> : mode === 'combined' && <ATSResumeGenerator deletedIds={deletedIds} onExport={onExport} disabled source={null} />}</div>
      {exporting && match && <ATSResumeGenerator key={revision} chooseMode deletedIds={deletedIds} onExport={onExport}
        disabled={!!busy || loadingSources} source={{ resumeId: match.resume_id, matchId: match.match_id,
          jobTitle: match.job_title, approvedCount: approved.length, revision }} />}
    </section>
  )
}
