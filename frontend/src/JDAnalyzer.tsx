import { useEffect, useId, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from './resumeApi'
import ReuseRecommendations from './ReuseRecommendations'
import type { Recommendation } from './ReuseRecommendations'
import type { ResumeOption } from './historyApi'
import type { LatestJob } from './CareerDashboard'
import { Icon } from './workspaceUI'

const MAX_DESCRIPTION_LENGTH = 30_000
const KEYWORD_GROUPS = {
  required_skills: 'Required skills',
  preferred_skills: 'Preferred skills',
  tools_and_technologies: 'Tools and technologies',
  ats_keywords: 'ATS keywords',
}
const DETAIL_GROUPS = {
  experience_requirements: 'Experience requirements',
  education_requirements: 'Education',
  responsibilities: 'Responsibilities',
  other_qualifications: 'Other qualifications',
}

type JobAnalysis = {
  job_id: string
  job_title: string | null
  evidence: {
    field: string
    value: string
    snippet: string
    line_number: number
    classification: 'required' | 'preferred' | 'mentioned' | 'negated'
    matched_text: string | null
  }[]
} & Record<keyof typeof KEYWORD_GROUPS | keyof typeof DETAIL_GROUPS, string[]>

type AnalysisState =
  | { status: 'idle' | 'loading' }
  | { status: 'error'; message: string }
  | { status: 'success'; analysis: JobAnalysis }

export default function JDAnalyzer({ archivedJob, masterId, onUseMaster, historyRevision, names, onAnalyze, onRecommendations }: {
  archivedJob: string; masterId: string | null; onUseMaster: (resumeId: string, jobId: string) => void; historyRevision: number
  names: ResumeOption[]; onAnalyze: (job: LatestJob) => void
  onRecommendations: (result: Recommendation) => void
}) {
  const headingId = useId()
  const descriptionId = useId()
  const [description, setDescription] = useState('')
  const [state, setState] = useState<AnalysisState>({ status: 'idle' })
  const isAnalyzing = state.status === 'loading'

  useEffect(() => {
    setState(current => current.status === 'success' && current.analysis.job_id === archivedJob ? { status: 'idle' } : current)
  }, [archivedJob])

  async function analyze(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (isAnalyzing) return
    if (!description.trim()) {
      setState({ status: 'error', message: 'Paste a job description before analyzing.' })
      return
    }

    setState({ status: 'loading' })
    try {
      const response = await apiFetch(`/api/jobs/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_description: description }),
      })
      const result = await response.json().catch(() => null)
      if (response.ok && typeof result?.job_id === 'string' && Array.isArray(result.ats_keywords)) {
        setState({ status: 'success', analysis: result })
        onAnalyze({ id: result.job_id, title: result.job_title })
      } else {
        setState({
          status: 'error',
          message: typeof result?.detail === 'string' ? result.detail : 'Analysis failed. Check the description and try again.',
        })
      }
    } catch {
      setState({ status: 'error', message: 'Could not reach the analysis service. Please try again.' })
    }
  }

  return (
    <section aria-labelledby={headingId} className="surface analyzer-panel">
      <h2 id={headingId} className="text-lg font-semibold">Job Descriptions</h2>
      <form className="mt-3" onSubmit={analyze} noValidate>
        <label htmlFor={descriptionId} className="text-sm font-medium text-stone-700">Job description</label>
        <textarea
          id={descriptionId}
          name="job_description"
          required
          rows={8}
          placeholder="Paste the job description, including responsibilities and qualifications"
          maxLength={MAX_DESCRIPTION_LENGTH}
          disabled={isAnalyzing}
          value={description}
          onChange={(event) => {
            setDescription(event.target.value)
            setState({ status: 'idle' })
          }}
          className="mt-2 block w-full resize-y rounded-md border border-stone-400 bg-white p-3 text-sm leading-relaxed focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={isAnalyzing}
          className="mt-3 rounded-md bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isAnalyzing ? 'Analyzing…' : 'Analyze JD'}
        </button>
        <p role="status" className={isAnalyzing ? "analysis-pulse mt-2" : "mt-2 text-sm text-teal-800"}>
          {isAnalyzing && <span aria-hidden="true"><Icon name="scan" /></span>}
          {isAnalyzing ? 'Analyzing the job description.' : state.status === 'success' ? 'Analysis saved.' : ''}
        </p>
        <p role="alert" className="text-sm text-red-700">{state.status === 'error' ? state.message : ''}</p>
      </form>
      {state.status === 'success' && (
        <div className="analysis-results">
          <details className="result-disclosure"><summary>Analysis details for {state.analysis.job_title ?? 'this role'}</summary><AnalysisResults analysis={state.analysis} /></details>
          <ReuseRecommendations key={state.analysis.job_id} jobId={state.analysis.job_id}
            masterId={masterId} onUseMaster={onUseMaster} historyRevision={historyRevision} names={names} onResult={onRecommendations} />
        </div>
      )}
    </section>
  )
}

function AnalysisResults({ analysis }: { analysis: JobAnalysis }) {
  return (
    <div className="mt-4 space-y-4 rounded-md border border-stone-200 bg-white p-4">
      <div>
        <h3 className="text-sm font-semibold">Job title</h3>
        <p className="mt-1 text-lg wrap-anywhere">{analysis.job_title ?? 'No title identified'}</p>
        <p className="mt-1 text-xs wrap-anywhere text-stone-600">Job ID <span className="font-mono">{analysis.job_id}</span></p>
      </div>
      {(Object.keys(KEYWORD_GROUPS) as (keyof typeof KEYWORD_GROUPS)[]).map((key) => (
        <section key={key}>
          <h3 className="text-sm font-semibold">{KEYWORD_GROUPS[key]}</h3>
          {analysis[key].length > 0 ? (
            <ul className="mt-2 flex flex-wrap gap-2">
              {analysis[key].map((keyword) => (
                <li key={keyword} className="rounded-md bg-teal-50 px-2 py-1 text-sm text-teal-900">{keyword}</li>
              ))}
            </ul>
          ) : <p className="mt-1 text-sm text-stone-600">None identified</p>}
        </section>
      ))}
      {(Object.keys(DETAIL_GROUPS) as (keyof typeof DETAIL_GROUPS)[]).map((key) => (
        <section key={key}>
          <h3 className="text-sm font-semibold">{DETAIL_GROUPS[key]}</h3>
          {analysis[key].length > 0 ? (
            <ul className="mt-1 list-disc space-y-1 pl-5 text-sm wrap-anywhere text-stone-700">
              {analysis[key].map((item) => <li key={item}>{item}</li>)}
            </ul>
          ) : <p className="mt-1 text-sm text-stone-600">None identified</p>}
        </section>
      ))}
      {analysis.evidence.length > 0 && (
        <details className="border-t border-stone-200 pt-3">
          <summary className="cursor-pointer text-sm font-semibold">View supporting evidence</summary>
          <ul className="mt-2 space-y-3 text-sm wrap-anywhere text-stone-700">
            {analysis.evidence.map((item, index) => (
              <li key={`${item.line_number}-${index}`}>
                <p className="font-medium text-stone-950">Line {item.line_number} · {item.matched_text ?? item.value} ({item.classification})</p>
                <p>{item.snippet}</p>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
