import { useEffect, useState } from 'react'
import { ArrowUpRight, BookOpen, Layers3, ScanText, SlidersHorizontal } from 'lucide-react'
import MasterFolio from './MasterFolio'
import { historyPage, historyRequest, versionDate, versionName } from './historyApi'
import type { ResumeOption, ResumeVersion } from './historyApi'
import type { Match } from './ResumeEditor'
import ReuseRecommendations from './ReuseRecommendations'
import type { Recommendation } from './ReuseRecommendations'
import { EmptyState, Icon, LoadingSkeleton, MatchScore } from './workspaceUI'


export type LatestJob = { id: string; title: string | null }

function ActivityTimeline({ versions, latest, match, names }: {
  versions: ResumeVersion[]; latest?: LatestJob; match: Match | null; names: ResumeOption[]
}) {
  if (!latest && !versions.length && match?.optimization?.status !== 'completed') {
    return <EmptyState icon="history" title="No recent activity">
      Saved exports and analyses will appear here.
    </EmptyState>
  }
  return <ul className="activity-timeline">
    {latest && <li>
      <span className="icon-chip cyan"><Icon name="scan" size={16} /></span>
      <div><a href="#jd-analyzer">{latest.title ?? 'Untitled job description'}</a><p>Latest JD analysis in this tab</p></div>
    </li>}
    {match?.optimization?.status === 'completed' && <li>
      <span className="icon-chip pink"><Icon name="spark" size={16} /></span>
      <div><a href="#resume-matching">{match.job_title ?? 'Resume optimization'}</a><p>Suggestions generated for the current comparison</p></div>
    </li>}
    {versions.map((version) => <li key={version.generation_id}>
      <span className="icon-chip"><Icon name="file" size={16} /></span>
      <div><a href={`#my-resumes/${version.generation_id}`}>{versionName(version, names)}</a>
        <p>Resume export saved · {versionDate(version.created_at)}</p>
      </div>
    </li>)}
  </ul>
}

export default function CareerDashboard({ recentJobs, masterStatus, latest, match, matchLoading, names, masterId, revision, onUseMaster, recommendation, onRecommendations }: {
  recentJobs: LatestJob[]; masterStatus: string; latest?: LatestJob; match: Match | null; matchLoading: boolean; names: ResumeOption[]; masterId: string | null; revision: number
  onUseMaster: (resumeId: string, jobId: string) => void
  recommendation?: Recommendation; onRecommendations: (result: Recommendation) => void
}) {
  const [history, setHistory] = useState<{ versions: ResumeVersion[]; total: number }>()
  const [jobs, setJobs] = useState<number>()
  const [issues, setIssues] = useState<string[]>([])
  const [attempt, setAttempt] = useState(0)
  const jobId = latest?.id ?? match?.job_id
  const matches = recommendation?.job_id === jobId ? recommendation?.candidates : undefined
  const scores = [
    match?.job_id === jobId ? match?.keyword_coverage.percentage : null,
    ...(matches?.map((candidate) => candidate.keyword_coverage.percentage) ?? []),
  ].filter((score): score is number => score != null)

  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    setIssues([])
    historyPage(new URLSearchParams({ sort: 'newest', limit: '4' }), signal)
      .then((data) => { if (!signal.aborted) setHistory(data) })
      .catch(() => { if (!signal.aborted) setIssues((items) => [...items, 'saved resumes']) })
    historyRequest<{ jobs: ResumeOption[] }>('/api/matches/sources', { signal })
      .then((data) => { if (!signal.aborted) setJobs(data.jobs.length) })
      .catch(() => { if (!signal.aborted) setIssues((items) => [...items, 'analyzed JDs']) })
    return () => controller.abort()
  }, [latest?.id, revision, attempt])

  const best = scores.length ? Math.max(...scores) : null
  return <div className="document-desk">
    <header className="desk-heading"><div><h1 id="career-title">Welcome to your workspace</h1>
      <p>Your source resume, working comparisons, and saved editions.</p></div>
      <a href="#how-it-works"><BookOpen size={16}/> Workspace guide</a>
    </header>
    <div className="desk-layout">
      <div className="overview-documents"><MasterFolio key={masterId ?? 'empty'} id={masterId} resume={names.find(item => item.id === masterId)} status={masterStatus}/>
        <section className="desk-editions"><header><Layers3 size={18}/><h2>Saved editions</h2><span data-edition-count>{history?.total ?? '\u2014'}</span></header>
          {history?.total === 0 && <p>Export a resume from Studio to start your collection.</p>}
          {history?.versions.slice(0, 2).map(version => <a className="edition-row" key={version.generation_id} href={'#my-resumes/' + version.generation_id}>
            <Icon name="file" size={16}/><span>{versionName(version,names)}</span></a>)}
          <a href="#my-resumes" className="desk-link">Open library <ArrowUpRight size={14}/></a>
        </section>
      </div>
      <aside className="desk-tools" aria-label="Tools and saved resumes">
        <a className="analysis-entry" href={masterId ? jobs ? '#match-studio' : '#jobs' : '#active-master'}><ScanText size={26}/><span><small>Recommended next step</small><strong>{masterId ? jobs ? 'Compare a resume with a role' : 'Analyze a job description' : 'Choose your master resume'}</strong></span><ArrowUpRight size={19}/></a>
        <div className="desk-ledger"><span>Uploaded resumes</span><b>{['Loading','Unavailable'].includes(masterStatus) ? 'Unavailable' : names.length}</b></div>
        <div className="desk-ledger"><span>Saved job descriptions</span><b>{jobs ?? '\u2014'}</b></div>
        {matchLoading && best === null && <p className="desk-ledger" role="status">Loading comparison...</p>}
        {best !== null && <div className="desk-ledger"><span>Best estimated match</span><b data-best-match><MatchScore value={best}/></b></div>}
        <a className="studio-entry" href="#resume-matching"><SlidersHorizontal size={20}/><strong>AI Resume Editor</strong><ArrowUpRight size={16}/></a>

      </aside>
    </div>
    {issues.length > 0 && <div className="workspace-alert" role="alert">Could not load {issues.join(' or ')}.
      <button className="text-action" onClick={() => setAttempt(value => value + 1)}>Retry</button></div>}
    <div className="desk-records"><section><header><h2>Recent activity</h2><a href="#resume-history">History</a></header>
      {!history && !issues.includes('saved resumes') ? <LoadingSkeleton label="Loading recent activity"/>
        : <ActivityTimeline versions={history?.versions ?? []} latest={latest} match={match} names={names}/>}
      {recentJobs.length > 1 && <details className="recent-jobs"><summary>Recent analyses in this tab</summary>
        <ul>{recentJobs.map(job => <li key={job.id}>{job.title ?? 'Untitled job description'}</li>)}</ul></details>}
    </section><section><header><h2>Recommended resumes</h2></header>
      {jobId ? <ReuseRecommendations key={jobId} jobId={jobId} masterId={masterId} onUseMaster={onUseMaster}
        historyRevision={revision} names={names} onResult={onRecommendations}/>
        : <p>Analyze a job description to find relevant saved versions.</p>}
    </section></div>
  </div>
}
