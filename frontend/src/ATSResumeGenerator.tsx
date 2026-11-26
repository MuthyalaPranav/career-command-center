import { useEffect, useId, useState } from 'react'
import { apiFetch } from './resumeApi'
import { useResumePDF } from './useResumePDF'
import { ResumePreview } from './workspaceUI'

type Source = {
  resumeId: string
  matchId: string
  jobTitle: string | null
  approvedCount: number
  revision: string
}

type Mode = 'ats' | 'original_layout'

type GeneratedResume = {
  generation_id: string
  resume_id: string
  match_id: string
  mode: Mode
  page_count: number
  applied_suggestion_count: number
  skipped_suggestion_count: number
}

const RESUME_API = '/api/resumes'

type GeneratorProps = { source: Source | null; disabled: boolean; deletedIds: string[]; onExport: () => void }

export default function ATSResumeGenerator({ chooseMode = false, ...props }: GeneratorProps & { chooseMode?: boolean }) {
  const group = useId()
  const [mode, setMode] = useState<Mode>('ats')
  const [compatibility, setCompatibility] = useState('')
  const matchId = props.source?.matchId
  const resumeId = props.source?.resumeId
  useEffect(() => {
    if (!chooseMode || mode !== 'original_layout' || !resumeId || !matchId) return
    const controller = new AbortController()
    setCompatibility('Checking original layout compatibility...')
    apiFetch(RESUME_API + '/generate/compatibility', { method: 'POST', signal: controller.signal,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ resume_id: resumeId, match_id: matchId, mode }) })
      .then(async response => {
        const result = await response.json()
        if (!controller.signal.aborted) setCompatibility(response.ok ? result.message : 'Compatibility check unavailable.')
      }).catch(() => { if (!controller.signal.aborted) setCompatibility('Compatibility check unavailable.') })
    return () => controller.abort()
  }, [chooseMode, mode, resumeId, matchId, props.source?.revision])
  return (
    <>
      {chooseMode && <fieldset className="format-options"><legend>Export format</legend>
        {(['ats', 'original_layout'] as const).map(value => <label key={value}><input type="radio" name={group} checked={mode === value}
          onChange={() => setMode(value)} />{value === 'ats' ? 'ATS-friendly PDF' : 'Preserve original PDF design'}</label>)}</fieldset>}
      {chooseMode && mode === 'original_layout' && <p role="status">{compatibility}</p>}
      <div hidden={chooseMode && mode !== 'ats'}><PDFExport {...props} mode="ats" /></div>
      <div hidden={chooseMode && mode !== 'original_layout'}><PDFExport {...props} mode="original_layout" /></div>
    </>
  )
}

function PDFExport({ source, disabled, mode, deletedIds, onExport }: GeneratorProps & { mode: Mode }) {
  const heading = useId()
  const [savedResult, setResult] = useState<GeneratedResume | null>(null)
  const result = savedResult && !deletedIds.includes(savedResult.generation_id) ? savedResult : null
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  const [issues, setIssues] = useState<{ suggestion_id: string; reason: string }[]>([])
  const storageKey = `resume-export-${mode}`
  const revision = source?.revision
  const resumeId = source?.resumeId
  const matchId = source?.matchId
  const generationId = result?.generation_id
  const pdf = useResumePDF(generationId)
  const preview = pdf?.url
  const ats = mode === 'ats'

  useEffect(() => {
    const saved = sessionStorage.getItem(storageKey)?.split(':')
    if (!saved || !revision || saved[0] !== revision) return
    const controller = new AbortController()
    async function restore(id: string) {
      try {
        const response = await apiFetch(`${RESUME_API}/generated/${encodeURIComponent(id)}`, { signal: controller.signal })
        if (!response.ok) return
        const data: GeneratedResume = await response.json()
        if (!controller.signal.aborted && data.resume_id === resumeId && data.match_id === matchId && data.mode === mode) {
          setResult((current) => current ?? data)
        }
      } catch {
        // A previously generated PDF can be generated again if restoration fails.
      }
    }
    void restore(saved[1])
    return () => controller.abort()
  }, [storageKey, revision, resumeId, matchId, mode])

  async function generate() {
    if (!source || generating || disabled) return
    setGenerating(true)
    setError('')
    setIssues([])
    try {
      const response = await apiFetch(`${RESUME_API}/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resume_id: source.resumeId, match_id: source.matchId, mode }),
      })
      const data = await response.json().catch(() => null)
      if (!response.ok || !data) {
        const detail = data?.detail
        if (Array.isArray(detail?.issues)) setIssues(detail.issues)
        throw new Error(typeof detail === 'string' ? detail : detail?.message ?? 'Unable to generate the resume PDF.')
      }
      setResult(data as GeneratedResume)
      sessionStorage.setItem(storageKey, `${source.revision}:${data.generation_id}`)
      onExport()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Unable to generate the resume PDF.')
    } finally {
      setGenerating(false)
    }
  }

  return (
    <section aria-labelledby={heading} className="surface export-panel">
      <h2 id={heading} className="text-lg font-semibold">{ats ? 'ATS Resume Generator' : 'Original-layout PDF'}</h2>
      {source ? (
        <>
          <p className="mt-2 text-sm text-stone-700 wrap-anywhere">Selected comparison · {source.jobTitle ?? 'Untitled job description'}</p>
          <p className="mt-1 text-sm text-stone-600">
            {source.approvedCount === 0
              ? ats ? 'No suggestions approved. You can generate a PDF with the original content.'
                : 'Without approved edits, a compatible export copies the original PDF.'
              : `${source.approvedCount} approved ${source.approvedCount === 1 ? 'suggestion' : 'suggestions'}`}
          </p>
        </>
      ) : (
        <p className="mt-2 text-sm text-stone-600">Select an extracted resume and analyzed job description above, then compare them.</p>
      )}
      <button type="button" disabled={!source || disabled || generating} onClick={() => void generate()}
        className="mt-3 rounded-md bg-teal-700 px-3 py-2 text-sm font-semibold text-white hover:bg-teal-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 disabled:cursor-not-allowed disabled:opacity-50">
        {generating ? 'Generating PDF…' : ats ? 'Generate ATS Resume' : 'Generate Original-Layout PDF'}
      </button>
      <p role="alert" className="mt-2 text-sm text-red-700">{error || pdf?.error}</p>
      {issues.length > 0 && (
        <ul className="mt-1 list-inside list-disc text-sm text-stone-700">
          {issues.map((issue) => <li key={issue.suggestion_id}>{issue.reason}</li>)}
        </ul>
      )}
      <p role="status" className="mt-2 text-sm text-teal-800">
        {generating ? 'Preparing the PDF preview…'
          : result && `PDF ready · ${result.page_count} ${result.page_count === 1 ? 'page' : 'pages'} · ${result.applied_suggestion_count} applied ${result.applied_suggestion_count === 1 ? 'edit' : 'edits'}`}
      </p>
      {result && preview && (
        <div className="mt-3 space-y-3">
          {result.skipped_suggestion_count > 0 && (
            <p className="text-sm text-stone-700">
              {result.skipped_suggestion_count} approved {result.skipped_suggestion_count === 1 ? 'edit' : 'edits'} could no longer be verified against the source.
            </p>
          )}
          <div className="flex flex-wrap items-center gap-3 text-sm font-semibold">
            <a className="rounded-md border border-stone-300 px-3 py-2 text-teal-800 hover:bg-stone-100"
              href={preview} download={`${ats ? 'ats' : 'layout'}-resume-${generationId}.pdf`}>Download PDF</a>
            <a className="text-teal-800 underline" href={preview} target="_blank" rel="noopener noreferrer">Open preview in new tab</a>
          </div>
          <ResumePreview title={ats ? 'Generated ATS resume PDF preview' : 'Generated original-layout PDF preview'} url={preview} />
        </div>
      )}
    </section>
  )
}
