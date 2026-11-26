import { useEffect, useState } from 'react'
import { HISTORY_API, historyJSON, historyRequest, versionName } from './historyApi'
import type { ResumeOption, ResumeVersion } from './historyApi'
import { downloadSavedResume } from './useResumePDF'
import { LoadingSkeleton } from './workspaceUI'

type Coverage = { matched: number; total: number; percentage: number | null }
type Keyword = {
  keyword: string
  priority: string
  resume_evidence: { text: string; section: string; line_number: number }[]
  jd_evidence: { snippet: string }[]
}
export type Recommendation = {
  job_id: string
  recommended_generation_id: string | null
  threshold: number
  candidates: {
    generation_id: string
    name: string | null
    job_title: string | null
    keyword_coverage: Coverage
    required_coverage: Coverage
    preferred_coverage: Coverage
    explanation: string
    matched_keywords: Keyword[]
    partial_keywords: Keyword[]
    missing_keywords: Keyword[]
    relevant_projects: { text: string; line_number: number }[]
    relevant_experience: { text: string; line_number: number }[]
    qualification_evidence: { requirement: string; section: string; text: string }[]
    qualifications_to_review: { requirement: string; category: string }[]
  }[]
}

function coverage(value: Coverage) {
  return value.percentage === null ? '—' : `${value.percentage}% (${value.matched}/${value.total})`
}

export default function ReuseRecommendations({ jobId, masterId, onUseMaster, historyRevision, names, onResult }: {
  jobId: string; masterId: string | null; onUseMaster: (resumeId: string, jobId: string) => void; historyRevision: number
  names: ResumeOption[]; onResult?: (result: Recommendation) => void
}) {
  const [result, setResult] = useState<Recommendation>()
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [downloading, setDownloading] = useState<string>()
  const [downloadStatus, setDownloadStatus] = useState('')
  const [downloadError, setDownloadError] = useState('')
  const [resumeIds, setResumeIds] = useState<Record<string, string>>({})

  useEffect(() => {
    const controller = new AbortController()
    setError('')
    setResult(undefined)
    historyRequest<Recommendation>('/api/resumes/recommend', {
      ...historyJSON('POST', { job_id: jobId }), signal: controller.signal,
    })
      .then((data) => { if (!controller.signal.aborted) setResult(data) })
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [jobId, retry, historyRevision])

  useEffect(() => {
    if (result) onResult?.(result)
  }, [result, onResult])

  useEffect(() => {
    if (!result) return
    const controller = new AbortController()
    const unnamed = result.candidates.filter((item) => !item.name)
    Promise.allSettled(unnamed.map((item) => historyRequest<ResumeVersion>(
      `${HISTORY_API}/${encodeURIComponent(item.generation_id)}`, { signal: controller.signal },
    ))).then((responses) => {
      if (controller.signal.aborted) return
      setResumeIds(Object.fromEntries(responses.flatMap((response) => response.status === 'fulfilled'
        ? [[response.value.generation_id, response.value.resume_id]] : [])))
    })
    return () => controller.abort()
  }, [result])

  function candidateName(item: Recommendation['candidates'][number]) {
    return versionName({ ...item, resume_id: resumeIds[item.generation_id] }, names)
  }

  async function useSavedResume(generationId: string) {
    setDownloading(generationId)
    setDownloadStatus('')
    setDownloadError('')
    try {
      await downloadSavedResume(generationId)
      setDownloadStatus('Download started. You can upload this PDF manually.')
    } catch (failure) {
      setDownloadError(failure instanceof Error ? failure.message : 'Could not download the saved resume.')
    } finally {
      setDownloading(undefined)
    }
  }

  const recommended = result?.candidates.find((candidate) => candidate.generation_id === result.recommended_generation_id)
  return (
    <section className="mt-4 space-y-3 rounded-md border border-teal-200 bg-teal-50 p-4">
      <h3 className="font-semibold">Recommended Saved Resumes</h3>
      {!result && !error && <LoadingSkeleton label="Comparing saved resumes" />}
      {error && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <p role="alert" className="text-red-700">{error}</p>
          <button type="button" className="text-teal-800 underline" onClick={() => setRetry((value) => value + 1)}>Retry</button>
        </div>
      )}
      {result && (
        <>
          {recommended ? (
            <p className="text-sm wrap-anywhere">
              Consider reusing <strong>{candidateName(recommended)}</strong>. Review the saved content against this JD before using it.
            </p>
          ) : (
            <div className="space-y-1 text-sm">
              <p>{result.candidates.length
                ? `Saved versions fall below the ${result.threshold}% reuse threshold or have insufficient keyword evidence.`
                : 'There are no available saved versions with readable resume content.'}</p>
              {masterId ? (
                <button type="button" className="font-semibold text-teal-800 underline" onClick={() => onUseMaster(masterId, jobId)}>
                  Create New Optimized Resume
                </button>
              ) : <a href="#active-master" className="font-semibold text-teal-800 underline">Choose a master resume to create a new version</a>}
            </div>
          )}
          {result.candidates.length > 0 && (
            <>
              <p className="text-sm text-stone-700">Internal keyword coverage measures recognized skills. Employer screening and qualifications require separate review.</p>
              <ol className="space-y-2">
                {result.candidates.map((item) => (
                  <li key={item.generation_id} className={`space-y-2 rounded-md border bg-white p-3 text-sm ${item.generation_id === result.recommended_generation_id ? 'border-teal-600' : 'border-teal-100'}`}>
                    {item.generation_id === result.recommended_generation_id && <p className="font-semibold text-teal-800">Recommended</p>}
                    <h4 className="font-semibold wrap-anywhere">{candidateName(item)}</h4>
                    <p>Overall {coverage(item.keyword_coverage)} · Required {coverage(item.required_coverage)} · Preferred {coverage(item.preferred_coverage)}</p>
                    <p>{item.explanation}</p>
                    <details className="wrap-anywhere">
                      <summary className="cursor-pointer font-medium">View match evidence</summary>
                      <KeywordEvidence title="Matched keywords" items={item.matched_keywords} />
                      <KeywordEvidence title="Partial matches" items={item.partial_keywords} />
                      <KeywordEvidence title="Missing keywords" items={item.missing_keywords} />
                      {([['Relevant projects', item.relevant_projects], ['Relevant experience', item.relevant_experience]] as const).map(([label, entries]) => (
                        entries.length > 0 && (
                          <div key={label} className="mt-2">
                            <h5 className="font-semibold">{label}</h5>
                            {entries.map((entry) => <p key={entry.line_number}>{entry.text}</p>)}
                          </div>
                        )
                      ))}
                      {item.qualifications_to_review.length > 0 && (
                        <div className="mt-2">
                          <h5 className="font-semibold">Qualifications to review</h5>
                          {item.qualifications_to_review.map((entry) => (
                            <p key={`${entry.category}:${entry.requirement}`}>{entry.requirement}</p>
                          ))}
                          {item.qualification_evidence.map((entry) => (
                            <p key={`${entry.section}:${entry.requirement}`}>Saved resume excerpt · {entry.text}</p>
                          ))}
                        </div>
                      )}
                    </details>
                    <div className="flex flex-wrap items-center gap-3">
                      <a href={`#my-resumes/${item.generation_id}`} className="font-semibold text-teal-800 underline">
                        Preview Resume
                      </a>
                      <button type="button" disabled={Boolean(downloading)}
                        onClick={() => void useSavedResume(item.generation_id)}
                        className="rounded-md bg-teal-700 px-3 py-2 font-semibold text-white hover:bg-teal-800 disabled:opacity-60">
                        {downloading === item.generation_id ? 'Downloading…' : 'Use This Resume'}
                      </button>
                    </div>
                  </li>
                ))}
              </ol>
              {downloadStatus && <p role="status" className="text-sm">{downloadStatus}</p>}
              {downloadError && <p role="alert" className="text-sm text-red-700">{downloadError}</p>}
            </>
          )}
        </>
      )}
    </section>
  )
}

function KeywordEvidence({ title, items }: { title: string; items: Keyword[] }) {
  return (
    <div className="mt-2">
      <h5 className="font-semibold">{title}</h5>
      {items.length === 0 && <p className="text-stone-600">None</p>}
      {items.map((item) => (
        <div key={item.keyword} className="mt-1">
          <p>{item.keyword} · {item.priority}</p>
          {item.resume_evidence.slice(0, 2).map(({ text, section, line_number }) => (
            <p className="ml-2 text-stone-700" key={`${section}:${line_number}:${text}`}>Resume · {text}</p>
          ))}
          {item.jd_evidence[0] && <p className="ml-2 text-stone-700">JD · {item.jd_evidence[0].snippet}</p>}
        </div>
      ))}
    </div>
  )
}
