import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import AppShell, { pages, ThemeToggle } from './AppShell'
import type { Page } from './AppShell'
import Walkthrough from './Walkthrough'
import CareerDashboard from './CareerDashboard'
import type { LatestJob } from './CareerDashboard'
import JDAnalyzer from './JDAnalyzer'
import MatchDashboard from './MatchDashboard'
import MasterResume, { getMaster, getMasterOptions } from './MasterResume'
import MyResumes from './MyResumes'
import { uploadLabel } from './historyApi'
import type { ActiveMaster, ResumeOption } from './historyApi'
import { apiFetch, hasWorkspaceToken, setWorkspaceToken } from './resumeApi'
import ResumeUploader from './ResumeUploader'
import type { Match } from './ResumeEditor'
import ReuseRecommendations from './ReuseRecommendations'
import type { Recommendation } from './ReuseRecommendations'
import { Icon, LoadingSkeleton } from './workspaceUI'

const LATEST_JOB_KEY = 'career-workspace-latest-job'

function readLatestJob(): LatestJob | undefined {
  try {
    const raw = sessionStorage.getItem(LATEST_JOB_KEY)
    if (!raw) return
    const job: LatestJob = JSON.parse(raw)
    if (typeof job?.id === 'string' && (job.title === null || typeof job.title === 'string')) return job
  } catch { /* The dashboard can also use a restored comparison. */ }
}

const RECENT_JOBS_KEY = 'career-workspace-recent-jobs'

function readRecentJobs(): LatestJob[] {
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(RECENT_JOBS_KEY) ?? 'null')
    if (Array.isArray(saved)) return saved.filter(job => job && typeof job.id === 'string'
      && (typeof job.title === 'string' || job.title === null)).slice(0, 4)
  } catch { /* Older sessions may only have the latest analysis. */ }
  const latest = readLatestJob()
  return latest ? [latest] : []
}

export default function App() {
  const [access, setAccess] = useState<'checking' | 'locked' | 'open' | 'offline'>('checking')
  const [guide, setGuide] = useState(location.hash === '#how-it-works')
  useEffect(() => {
    const update = () => {
      setGuide(location.hash === '#how-it-works')
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])
  const [token, setToken] = useState('')
  const [error, setError] = useState('')

  async function checkAccess(signal?: AbortSignal, submitted = false) {
    setAccess('checking')
    setError('')
    try {
      const response = await apiFetch('/api/access', { signal })
      if (signal?.aborted) return
      if (response.status === 401) {
        setWorkspaceToken('')
        setAccess('locked')
        if (submitted) setError('The workspace token was not accepted.')
      } else if (response.ok) {
        setToken('')
        setAccess('open')
      } else {
        setAccess('offline')
      }
    } catch {
      if (!signal?.aborted) setAccess('offline')
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    void checkAccess(controller.signal)
    return () => controller.abort()
  }, [])

  function unlock(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setWorkspaceToken(token.trim())
    void checkAccess(undefined, true)
  }

  function lock() {
    setWorkspaceToken('')
    sessionStorage.removeItem(RECENT_JOBS_KEY)
    sessionStorage.removeItem(LATEST_JOB_KEY)
    void checkAccess()
  }

  if (access === 'open') return <Workspace onLock={hasWorkspaceToken() ? lock : undefined} />

  if (guide) return <main className="public-guide"><a className="text-action" href="#jd-analyzer">Open workspace</a><Walkthrough /></main>

  return (
    <main className="access-page">
      <div className="access-theme"><ThemeToggle /></div>
      <section aria-labelledby="page-title" className="access-card">
        <span className="brand-mark"><Icon name="spark" /></span>
        <h1 id="page-title">Career Command Center</h1>
        <p>Compare your resume with a job description, review wording suggestions, and export your resume.</p>
        <a className="text-action" href="#how-it-works">How it works</a>
        {access === 'checking' && <LoadingSkeleton label="Checking workspace access" />}
        {access === 'offline' && (
          <div className="mt-6 space-y-2">
            <p role="alert">Could not connect to the workspace API.</p>
            <button type="button" className="primary-button" onClick={() => void checkAccess()}>Retry</button>
          </div>
        )}
        {access === 'locked' && (
          <form className="mt-6 flex max-w-sm flex-col items-start gap-2" onSubmit={unlock}>
            <label htmlFor="workspace-token" className="text-sm font-semibold">Workspace access token</label>
            <input id="workspace-token" className="w-full rounded-md border border-stone-400 bg-white p-2"
              autoComplete="current-password" type="password" value={token} required
              onChange={(event) => setToken(event.target.value)} />
            {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
            <button type="submit" className="primary-button">Unlock workspace</button>
          </form>
        )}
      </section>
    </main>
  )
}

function Workspace({ onLock }: { onLock?: () => void }) {
  const [hash, setHash] = useState(location.hash)
  const [master, setMaster] = useState<ActiveMaster | null>(null)
  const [masterLoading, setMasterLoading] = useState(true)
  const [masterError, setMasterError] = useState('')
  const [resumes, setResumes] = useState<ResumeOption[]>([])
  const [uploaderRevision, setUploaderRevision] = useState(0)
  const [archivedResume, setArchivedResume] = useState('')
  const [archivedJob, setArchivedJob] = useState('')
  const [uploadedId, setUploadedId] = useState('')
  const [uploadedFile, setUploadedFile] = useState<File>()
  const [comparison, setComparison] = useState<{ resumeId: string; jobId: string; run?: boolean; savedMatchId?: string }>()
  const [recentJobs, setRecentJobs] = useState(readRecentJobs)
  const latestJob = recentJobs[0]
  const [currentMatch, setCurrentMatch] = useState<Match | null>(null)
  const [matchLoading, setMatchLoading] = useState(true)
  const [historyRevision, setHistoryRevision] = useState(0)
  const [recommendations, setRecommendations] = useState<Recommendation>()
  const [sourceRevision, setSourceRevision] = useState(0)
  const [search, setSearch] = useState({ query: '', revision: 0 })
  const [deletedIds, setDeletedIds] = useState<string[]>([])
  const [route, selected] = hash.slice(1).split('/')
  const page: Page = Object.hasOwn(pages, route) ? route as Page : 'dashboard'
  const library = page === 'my-resumes' || page === 'resume-history'
  const combined = page === 'tailor'
  const analyzer = page === 'jd-analyzer' || page === 'jobs'
  const matching = page === 'match-studio'
  const exporting = page === 'export-center'
  const studio = page === 'resume-matching'
  const showMaster = page === 'active-master' || combined

  useEffect(() => {
    const navigate = () => {
      setHash(location.hash)
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', navigate)
    return () => window.removeEventListener('hashchange', navigate)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      getMaster(controller.signal).then((current) => { if (!controller.signal.aborted) setMaster(current) }),
      getMasterOptions(controller.signal).then((options) => {
        if (!controller.signal.aborted) {
          setResumes((current) => [...current, ...options.filter(({ id }) => !current.some((item) => item.id === id))])
        }
      }),
    ]).catch((failure: Error) => { if (!controller.signal.aborted) setMasterError(failure.message) })
      .finally(() => { if (!controller.signal.aborted) setMasterLoading(false) })
    return () => controller.abort()
  }, [])

  function openStudio(resumeId: string, jobId: string) {
    setComparison({ resumeId, jobId })
    location.hash = 'resume-matching'
  }

  function refreshSources() {
    setSourceRevision((value) => value + 1)
  }

  function removeUpload(id: string) {
    setArchivedResume(id)
    setResumes(items => items.filter(item => item.id !== id))
    setMaster(current => current?.resume_id === id ? null : current)
    if (uploadedId === id) {
      setUploadedId('')
      setUploadedFile(undefined)
      setUploaderRevision(value => value + 1)
    }
    setComparison(current => current?.resumeId === id && !current.savedMatchId ? undefined : current)
    refreshSources()
  }

  function removeJob(id: string) {
    setArchivedJob(id)
    const recent = recentJobs.filter(item => item.id !== id)
    setRecentJobs(recent)
    sessionStorage.setItem(RECENT_JOBS_KEY, JSON.stringify(recent))
    if (recent[0]) sessionStorage.setItem(LATEST_JOB_KEY, JSON.stringify(recent[0]))
    else sessionStorage.removeItem(LATEST_JOB_KEY)
    setRecommendations(current => current?.job_id === id ? undefined : current)
    setComparison(current => current?.jobId === id && !current.savedMatchId ? undefined : current)
    refreshSources()
  }

  return (
    <AppShell page={page} onLock={onLock} onSearch={(query) => {
      setSearch((value) => ({ query, revision: value.revision + 1 }))
      location.hash = 'my-resumes'
    }}>
      {page === 'how-it-works' && <Walkthrough />}
      {page === 'dashboard' && <CareerDashboard recentJobs={recentJobs} latest={latestJob} match={currentMatch} matchLoading={matchLoading}
        masterStatus={masterError ? 'Unavailable' : masterLoading ? 'Loading' : master ? 'Active' : 'Not set'} names={resumes} masterId={master?.resume_id ?? null} revision={historyRevision} onUseMaster={openStudio}
        recommendation={recommendations} onRecommendations={setRecommendations} />}
      <div className="master-workspace" hidden={!showMaster}>
        <MasterResume master={master} options={resumes} suggested={uploadedId} visible={showMaster}
          onDelete={removeUpload} match={combined ? null : currentMatch} uploadedFile={combined ? undefined : uploadedFile}
          onChange={(saved) => { setMaster(saved); setMasterError(''); refreshSources() }}
          onRename={(updated) => setResumes((items) => items.map((item) => item.id === updated.id ? updated : item))}
          onExtracted={refreshSources} loading={masterLoading} loadError={masterError}>
          <ResumeUploader key={uploaderRevision} onUploaded={(id, filename, file) => {
            setUploadedId(id)
            setUploadedFile(file)
            setResumes((items) => [
              { id, label: uploadLabel(id, filename), original_filename: filename },
              ...items.filter((item) => item.id !== id),
            ])
          }} onExtracted={refreshSources} />
        </MasterResume>
      </div>
      <div className="workflow-page" hidden={!analyzer && !combined}>
        <JDAnalyzer archivedJob={archivedJob} masterId={master?.resume_id ?? null} historyRevision={historyRevision} names={resumes}
          onUseMaster={openStudio} onRecommendations={setRecommendations} onAnalyze={(job) => {
            const recent = [job, ...recentJobs.filter(item => item.id !== job.id)].slice(0, 4)
            setRecentJobs(recent)
            sessionStorage.setItem(RECENT_JOBS_KEY, JSON.stringify(recent))
            sessionStorage.setItem(LATEST_JOB_KEY, JSON.stringify(job))
            refreshSources()
            if (!combined && master) setComparison({ resumeId: master.resume_id, jobId: job.id, run: true })
          }} />
      </div>
      <div className="workflow-page" hidden={!analyzer && !matching && !exporting && !studio && !combined}>
        <MatchDashboard jobsOnly={page === 'jobs'} exporting={exporting} matchOnly={matching} archivedResume={archivedResume} archivedJob={archivedJob} onDeleteJob={removeJob} masterId={master?.resume_id} comparison={comparison} deletedIds={deletedIds} resumeNames={resumes}
          mode={combined ? 'combined' : analyzer || matching ? 'analyzer' : 'studio'} sourceRevision={sourceRevision}
          onMatch={setCurrentMatch} onLoading={setMatchLoading}
          onExport={() => setHistoryRevision((value) => value + 1)} />
      </div>
      {library && <MyResumes selectedId={selected} timeline={page === 'resume-history'} names={resumes} searchRequest={search}
        recommendation={recommendations?.job_id === (latestJob?.id ?? currentMatch?.job_id) ? recommendations : undefined}
        onOpenMatch={(version) => {
          if (!version.job_id) return
          setComparison({ resumeId: version.resume_id, jobId: version.job_id, savedMatchId: version.match_id })
          location.hash = 'resume-matching'
        }}
        onChange={() => setHistoryRevision((value) => value + 1)}
        onRemove={(id) => setDeletedIds((ids) => [...ids, id])} />}
      {page === 'resume-history' && latestJob && <details className="result-disclosure"><summary>Recommended versions for {latestJob.title ?? 'the latest job'}</summary>
        <ReuseRecommendations key={latestJob.id} jobId={latestJob.id} masterId={master?.resume_id ?? null} onUseMaster={openStudio}
          historyRevision={historyRevision} names={resumes} onResult={setRecommendations} /></details>}
      {page === 'settings' && <section className="settings-panel surface">
        <h1>Workspace settings</h1>
        <div><span><strong>Appearance</strong><small>Theme preferences are saved on this device.</small></span><ThemeToggle /></div>
        <div><span><strong>Workspace access</strong><small>{onLock ? 'Unlocked with a workspace token.' : 'Token protection is disabled on this server.'}</small></span>
          {onLock && <button className="secondary-button" onClick={onLock}>Lock workspace</button>}</div>
        <nav aria-label="Alternate workspace views"><a className="text-action" href="#tailor">Open combined tailoring workflow</a></nav>
      </section>}
    </AppShell>
  )
}
