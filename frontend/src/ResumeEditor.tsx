import { useEffect, useId, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { apiFetch } from './resumeApi'
import { Icon, LoadingSkeleton, MatchScore } from './workspaceUI'

const BUTTON = 'rounded-md border border-stone-300 px-3 py-2 text-sm font-semibold hover:bg-stone-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 disabled:opacity-50'
const PRIMARY = `${BUTTON} border-teal-700 bg-teal-700 text-white hover:bg-teal-800`
const PANEL = 'min-w-0 rounded-md border border-stone-200 bg-white p-4 wrap-anywhere'
const FIELD = 'mt-1 block w-full rounded-md border border-stone-400 bg-white p-2 text-sm'
const SECTION_LABELS: Record<string, string> = {
  professional_summary: 'Professional summary', technical_skills: 'Technical skills', projects: 'Projects',
  work_experience: 'Work experience', education: 'Education', certifications: 'Certifications',
  contact_information: 'Contact information', unclassified: 'Additional information',
}
const EVIDENCE_LABELS = { project: 'Project', task: 'Task', work_experience: 'Work experience' }
const COVERAGE_FIELDS = { required: 'Required', preferred: 'Preferred', overall: 'Overall' } as const

type ResumeEvidence = { section: string; line_number: number; text: string; matched_text: string }
type JobEvidence = { snippet: string; line_number: number }
type Keyword = {
  keyword: string; priority: 'required' | 'preferred' | 'mentioned'
  match_type: 'exact' | 'alias' | 'contextual' | 'missing'; resume_evidence: ResumeEvidence[]; jd_evidence: JobEvidence[]
}
type RelevantLine = { line_number: number; text: string; keywords: string[] }
type Decision = 'accepted' | 'rejected'
type EvidenceSource = keyof typeof EVIDENCE_LABELS
type ReviewStatus = 'already_know' | 'not_experienced' | 'review_later'
type Suggestion = {
  suggestion_id: string; section: string; original_text: string; suggested_replacement: string
  relevant_jd_keywords: string[]; supporting_resume_evidence: ResumeEvidence[]; reason: string
  verification_status: 'verified_against_source' | 'user_attested'; approval_status: Decision | 'pending'
  manual_replacement?: string | null
  edit_revision?: number
  user_evidence?: { keyword: string; source: EvidenceSource; text: string } | null
}
type SkillReview = { status: ReviewStatus; proposal: Suggestion | null }
type Coverage = { matched: number; total: number; percentage: number | null }
type CoverageBreakdown = Record<keyof typeof COVERAGE_FIELDS, Coverage>
type SectionContent = Record<string, string[]>
type EditorView = {
  job: {
    responsibilities: string[]; experience_requirements: string[]
    education_requirements: string[]; other_qualifications: string[]
  }
  original_sections: SectionContent; preview_sections: SectionContent; self_reported_additions: SectionContent
  skills: Record<string, string[]>
  current_coverage: CoverageBreakdown; proposed_coverage: CoverageBreakdown
}
export type Match = {
  match_id: string; resume_id: string; job_id: string; job_title: string | null
  matched_skills: Keyword[]; partial_matches: Keyword[]; missing_keywords: Keyword[]
  relevant_projects: RelevantLine[]; relevant_experience: RelevantLine[]
  required_qualifications_needing_review?: { requirement: string; category: string }[]
  keyword_coverage: Coverage
  optimization: { status: 'completed' | 'no_candidates'; suggestions: Suggestion[]; discarded_suggestions: number } | null
  skill_reviews?: Record<string, SkillReview>
}

type Mutate = (path: string, options: RequestInit, progress: string, success: string) => Promise<string | undefined>

function send(method: 'PUT' | 'PATCH', data: object): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }
}

export function editorSuggestions(match: Match | null) {
  const proposals = Object.values(match?.skill_reviews ?? {}).flatMap(({ proposal }) => proposal ? [proposal] : [])
  return [...(match?.optimization?.suggestions ?? []), ...proposals]
}

const studioTabs = ['Content', 'Preview', 'Suggestions'] as const

export default function ResumeEditor({ match, busy, mutate, mode, children }: {
  match: Match; busy: boolean; mutate: Mutate; mode: 'analyzer' | 'studio' | 'combined'; children: ReactNode
}) {
  const studioId = useId()
  const [tab, setTab] = useState<(typeof studioTabs)[number]>(() => matchMedia('(min-width:1001px)').matches ? 'Suggestions' : 'Preview')
  const [zoom, setZoom] = useState(100)
  const [loaded, setLoaded] = useState<{ match: Match; view?: EditorView; error?: string }>()
  const [retry, setRetry] = useState(0)
  const view = loaded?.match === match ? loaded.view : undefined
  const error = loaded?.match === match ? loaded.error : undefined
  const keywords = [...match.matched_skills, ...match.partial_matches, ...match.missing_keywords]
  const gaps = [...match.partial_matches, ...match.missing_keywords]
  const suggestions = editorSuggestions(match)
  const path = `/${match.match_id}`

  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const response = await apiFetch(`/api/matches/${match.match_id}/editor`, { signal: controller.signal })
        if (!response.ok) throw new Error('Could not load the saved comparison content.')
        const view: EditorView = await response.json()
        if (!controller.signal.aborted) setLoaded({ match, view })
      } catch {
        if (!controller.signal.aborted) setLoaded({ match, error: 'Could not load the saved comparison content.' })
      }
    }
    void load()
    return () => controller.abort()
  }, [match, retry])

  return (
    <div className="mt-5 space-y-7">
      {error ? (
        <div className="flex flex-wrap items-center gap-2">
          <p role="alert" className="text-sm text-red-700">{error}</p>
          <button type="button" className={BUTTON} onClick={() => { setLoaded(undefined); setRetry((value) => value + 1) }}>Retry</button>
        </div>
      ) : !view && <LoadingSkeleton label="Loading saved comparison content" />}
      <div className="comparison-spread" hidden={mode === 'studio'}>
      <header className="spread-heading"><Icon name="scan"/><h2>Evidence map</h2></header>
      <SkillConstellation items={keywords} />
      <div className="comparison-grid">
        <section className={PANEL}>
          <h3 className="font-semibold">My Resume</h3>
          {view && (
            <div className="mt-3 space-y-3 text-sm">
              {Object.entries(view.skills).map(([section, skills]) => (
                <div key={section}>
                  <h4 className="font-semibold">{SECTION_LABELS[section] ?? section}</h4>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {skills.map((skill) => {
                      const keyword = match.matched_skills.find((item) => item.keyword === skill)
                      return keyword
                        ? <KeywordTag key={skill} item={keyword} showEvidence />
                        : <span key={skill} className="rounded-md bg-stone-100 px-2 py-1 text-stone-800">{skill}</span>
                    })}
                  </div>
                </div>
              ))}
              <details open>
                <summary className="cursor-pointer font-medium">Extracted technical skills</summary>
                <TextList items={view.original_sections.technical_skills ?? []} />
              </details>
              <details>
                <summary className="cursor-pointer font-medium">Extracted qualifications</summary>
                <TextList items={[...(view.original_sections.education ?? []), ...(view.original_sections.certifications ?? [])]} />
              </details>
            </div>
          )}
          <RelevantGroup title="Relevant projects" items={match.relevant_projects} />
          <RelevantGroup title="Relevant experience" items={match.relevant_experience} />
        </section>
        <section className={PANEL}>
          <h3 className="font-semibold">Job Description</h3>
          {match.job_title && <p className="mt-1 text-sm text-stone-600">{match.job_title}</p>}
          {(['required', 'preferred', 'mentioned'] as const).map((priority) => (
            <div key={priority} className="mt-3">
              <KeywordGroup title={priority === 'mentioned' ? 'Other JD keywords' : `${COVERAGE_FIELDS[priority]} skills`}
                items={keywords.filter((item) => item.priority === priority)} />
            </div>
          ))}
          {view && (
            <>
              <div className="mt-3">
                <h4 className="text-sm font-semibold">Requirements and qualifications</h4>
                <TextList items={[...view.job.experience_requirements, ...view.job.education_requirements, ...view.job.other_qualifications]} />
              </div>
              <div className="mt-3">
                <h4 className="text-sm font-semibold">Responsibilities</h4>
                <TextList items={view.job.responsibilities} />
              </div>
            </>
          )}
        </section>
      </div>
      <section className={`${PANEL} mt-4`}>
        <div className="comparison-heading">
          <div><h3 className="font-semibold">Skills Gap Analysis</h3>
            <p className="text-sm text-stone-600">Estimated match · <MatchScore value={match.keyword_coverage.percentage} /></p>
            {match.keyword_coverage.percentage != null && <progress className="match-progress"
              aria-label="Estimated keyword coverage" value={match.keyword_coverage.percentage} max={100} />}
          </div>

        </div>
        {view && <CoverageTable current={view.current_coverage} proposed={view.proposed_coverage} />}
        {!!match.required_qualifications_needing_review?.length && <div className="mt-3">
          <h4 className="text-sm font-semibold">Requirements to review</h4>
          <TextList items={match.required_qualifications_needing_review.map(({ requirement }) => requirement)} />
        </div>}
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <KeywordGroup title="Matched skills" items={match.matched_skills} />
          <KeywordGroup title="Partially matched skills" items={match.partial_matches} />
          <KeywordGroup title="Skills without resume evidence" items={match.missing_keywords} />
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          <RelevantGroup title="Relevant projects" items={match.relevant_projects} />
          <RelevantGroup title="Relevant experience" items={match.relevant_experience} />
        </div>
        {gaps.length > 0 && <div className="mt-4 space-y-2">
          <h4 className="text-sm font-semibold">Review skills that need evidence</h4>
          {gaps.map((item) => (
            <GapReview key={item.keyword} item={item} review={match.skill_reviews?.[item.keyword]} busy={busy}
              save={(body) => mutate(`${path}/skills`, send('PUT', { keyword: item.keyword, ...body }),
                'Saving skill review…', 'Skill review saved.')} />
          ))}
        </div>}
        {match.optimization?.suggestions.length ? (
          <details className="mt-3">
            <summary className="cursor-pointer text-sm font-semibold">Source-backed wording suggestions</summary>
            <TextList items={match.optimization.suggestions.map((item) => item.manual_replacement ?? item.suggested_replacement)} />
          </details>
        ) : !match.optimization && <p className="mt-3 text-sm text-stone-700">Generate improvements in the editor to see source-backed wording suggestions.</p>}
        <div className="comparison-heading mt-4">{mode === 'analyzer' && <a href="#resume-matching" className="primary-button"><Icon name="spark" size={17} /> Optimize Resume for This JD</a>}</div>
      </section>
      </div>
      <div hidden={mode === 'analyzer'} className={mode === 'studio' ? 'studio-workspace' : 'space-y-4'}>
      {mode === 'studio' && <header className="manuscript-toolbar"><div><Icon name="edit" /><strong>{match.job_title || 'Untitled comparison'}</strong></div>
        <div className="studio-tabs"  role="group" aria-label="Studio panels">
        {studioTabs.map((item) => <button key={item} type="button" aria-pressed={tab === item}
          aria-controls={`${studioId}-${item}`} onClick={() => setTab(item)}>{item}</button>)}
      </div></header>}
      <div className={mode === 'studio' ? 'studio-grid' : 'studio-combined'}>
      {mode === 'studio' && <section id={`${studioId}-Content`} data-active={tab === 'Content'} className={`${PANEL} studio-content`}>
        <h3 className="font-semibold">Resume Content</h3>
        <p className="text-sm text-stone-600 mt-1">Review the original sections. Edit source-backed wording in Suggestions.</p>
        {view && <ResumeSections sections={view.original_sections} />}
      </section>}
      <div id={`${studioId}-Preview`} data-active={tab === 'Preview'} className="studio-preview">
      <section className={PANEL}>
        <div className="comparison-heading"><h3 className="font-semibold">Resume Preview</h3>
          {mode === 'studio' && <div className="zoom-controls" role="group" aria-label="Resume text zoom">
            <button type="button" aria-label="Zoom out" disabled={zoom <= 80} onClick={() => setZoom((value) => value - 10)}>−</button>
            <output aria-label="Zoom level">{zoom}%</output>
            <button type="button" aria-label="Zoom in" disabled={zoom >= 150} onClick={() => setZoom((value) => value + 10)}>+</button>
          </div>}
        </div>
        <p className="mt-1 text-sm text-stone-600">This preview reflects saved approvals. Approved self-reported additions are available in the ATS-friendly export.</p>
        {view && <details className="mt-3" open={mode === 'studio' || undefined}>
          <summary className="cursor-pointer text-sm font-semibold">View approved resume content</summary>
          <div className="preview-paper" style={{ fontSize: `${zoom}%` }}><ResumeSections sections={view.preview_sections} selfReported={view.self_reported_additions} /></div>
        </details>}
      </section>
      {children}
      </div>
      <section id={`${studioId}-Suggestions`} data-active={tab === 'Suggestions'} data-default={tab === 'Preview'} className={`${PANEL} studio-suggestions`}>
        <h3 className="font-semibold">AI Resume Editor</h3>
        <p className="mt-1 text-sm text-stone-600">Gemini selects wording improvements from source-backed changes for your summary, skills, projects, and work experience.</p>
        {mode === 'combined' && view && <details className="mt-3">
          <summary className="cursor-pointer text-sm font-semibold">View original resume by section</summary>
          <ResumeSections sections={view.original_sections} />
        </details>}
        {mode === 'studio' && <div className="mt-4 space-y-3">
          <KeywordGroup title="Matching JD keywords" items={match.matched_skills} />
          <KeywordGroup title="Missing or partial skills" items={gaps} />
        </div>}
        {!match.optimization && <button type="button" className={`${PRIMARY} mt-3`} disabled={busy}
          onClick={() => void mutate(`${path}/optimize`, { method: 'POST' }, 'Generating improvements…', 'Suggestion review ready.')}>
          Generate AI Improvements
        </button>}
        {match.optimization && match.optimization.suggestions.length === 0 && (
          <p className="mt-3 text-sm text-stone-600">{match.optimization.status === 'no_candidates'
            ? 'No eligible wording edits were found.' : 'Gemini selected no wording edits.'}</p>
        )}
        {!!match.optimization?.discarded_suggestions && (
          <p className="mt-3 text-sm text-stone-600">Omitted {match.optimization.discarded_suggestions} unverified or duplicate suggestions.</p>
        )}
        {suggestions.map((item) => {
          const endpoint = `${path}/suggestions/${item.suggestion_id}`
          return <SuggestionCard key={item.suggestion_id} item={item} busy={busy}
            decide={(decision) => mutate(endpoint, send('PATCH', { approval_status: decision }), 'Saving decision…', 'Decision saved.')}
            save={(replacement) => mutate(`${endpoint}/edit`, send('PUT', { replacement }), 'Saving wording…', 'Wording saved. Review it again before accepting.')} />
        })}
      </section>
      </div>
      </div>
    </div>
  )
}

function TextList({ items }: { items: string[] }) {
  return items.length ? <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
    {items.map((item, index) => <li key={index}>{item}</li>)}
  </ul> : <p className="mt-1 text-sm text-stone-600">None identified</p>
}

function RelevantGroup({ title, items }: { title: string; items: RelevantLine[] }) {
  return <div className="mt-3">
    <h4 className="text-sm font-semibold">{title}</h4>
    <TextList items={items.map((item) => item.text)} />
  </div>
}

function KeywordGroup({ title, items }: { title: string; items: Keyword[] }) {
  return <div>
    <h4 className="text-sm font-semibold">{title}</h4>
    {items.length ? <div className="mt-2 flex flex-wrap gap-1.5">
      {items.map((item) => <KeywordTag key={item.keyword} item={item} />)}
    </div> : <p className="mt-1 text-sm text-stone-600">None identified</p>}
  </div>
}

function SkillConstellation({ items }: { items: Keyword[] }) {
  const [selected, setSelected] = useState('')
  const detail = items.find(item => item.keyword === selected)
  if (!items.length) return null
  return <section className="constellation surface">
    <p className="map-caption">Select a skill to inspect its source passages.</p>
    <div className="node-heading"><span>Resume evidence</span><span>JD requirements</span></div>
    <div className="node-field">{items.map(item => {
      const evidence = item.resume_evidence[0]
      const status = item.match_type === 'missing' ? 'Missing' : item.match_type === 'contextual' ? 'Partial' : 'Matched'
      const select = () => setSelected(selected === item.keyword ? '' : item.keyword)
      return <div key={item.keyword} className="node-row" data-selected={selected === item.keyword} data-state={status}>
        {evidence && status !== 'Missing' ? <>
          <button aria-pressed={selected === item.keyword} onClick={select}>{evidence.matched_text || evidence.text}</button>
          <svg viewBox="0 0 100 20" preserveAspectRatio="none" aria-hidden="true"><path d="M0 10 C35 10 35 2 50 8 S75 10 100 10" /></svg>
        </> : <><span /><span /></>}
        <button aria-pressed={selected === item.keyword} onClick={select}>{item.keyword}<small>{status}</small></button>
      </div>
    })}</div>
    {detail && <div className="node-evidence" aria-live="polite"><h4>{detail.keyword}</h4><div className="evidence-passages"><div><strong>Resume passage</strong>
      {detail.resume_evidence.map((line, index) => <p key={index}>Resume · {line.section} · {line.text}</p>)}
      {!detail.resume_evidence.length && <p>No supporting resume evidence.</p>}</div><div><strong>JD passage</strong>
      {detail.jd_evidence.map((line, index) => <p key={index}>JD · Line {line.line_number} · {line.snippet}</p>)}
      </div></div>
    </div>}
  </section>
}

function KeywordTag({ item, showEvidence = false }: { item: Keyword; showEvidence?: boolean }) {
  const status = item.match_type === 'missing' ? 'Missing' : item.match_type === 'contextual' ? 'Partial' : 'Matched'
  const styles = { Matched: 'bg-emerald-100 text-emerald-950', Partial: 'bg-amber-100 text-amber-950', Missing: 'bg-red-100 text-red-950' }
  return <details className={`min-w-0 rounded-md px-2 py-1 text-sm ${styles[status]}`}>
    <summary className="cursor-pointer">{item.keyword} <span className="text-xs">· {status}</span></summary>
    {item.resume_evidence.map((evidence, index) => (
      <p className="mt-1" key={index}>{SECTION_LABELS[evidence.section]} · {evidence.text}</p>
    ))}
    {!showEvidence && item.jd_evidence.map((evidence, index) => <p className="mt-1" key={index}>JD · {evidence.snippet}</p>)}
  </details>
}

function CoverageTable({ current, proposed }: { current: CoverageBreakdown; proposed: CoverageBreakdown }) {
  function format(value: Coverage) {
    return value.percentage === null ? '—' : `${value.percentage}% (${value.matched}/${value.total})`
  }
  return <div className="mt-3 overflow-x-auto">
    <table className="w-full text-left text-sm">
      <caption className="pb-2 text-left font-semibold">Internal keyword coverage</caption>
      <thead><tr className="border-b border-stone-200">
        <th scope="col" className="p-1">Keywords</th>
        <th scope="col" className="p-1">Current</th>
        <th scope="col" className="p-1">Proposed · source-verified</th>
      </tr></thead>
      <tbody>
        {(Object.keys(COVERAGE_FIELDS) as (keyof CoverageBreakdown)[]).map((field) => (
          <tr key={field} className="border-b border-stone-100">
            <th scope="row" className="p-1 font-medium">{COVERAGE_FIELDS[field]}</th>
            <td className="p-1">{format(current[field])}</td>
            <td className="p-1">{format(proposed[field])}</td>
          </tr>
        ))}
      </tbody>
    </table>
    <p className="mt-2 text-xs text-stone-600">The proposed figures use approved wording backed by the saved resume. Employers may assess resumes differently.</p>
  </div>
}

function ResumeSections({ sections, selfReported }: { sections: SectionContent; selfReported?: SectionContent }) {
  return <div className="mt-3 space-y-3">
    {Object.entries(sections).filter(([, lines]) => lines.length > 0).map(([key, lines]) => (
      <div key={key}>
        <h4 className="text-sm font-semibold">{SECTION_LABELS[key] ?? key.replaceAll('_', ' ')}</h4>
        <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
          {lines.map((line, index) => <li key={index}>{line}
            {selfReported?.[key]?.includes(line) && <span className="ml-2 text-xs text-stone-600">Self-reported</span>}
          </li>)}
        </ul>
      </div>
    ))}
  </div>
}

function GapReview({ item, review, busy, save }: {
  item: Keyword; review?: SkillReview; busy: boolean
  save: (body: { status: ReviewStatus; source?: EvidenceSource; evidence?: string }) => Promise<string | undefined>
}) {
  const [open, setOpen] = useState(false)
  const [source, setSource] = useState<EvidenceSource>('project')
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const existing = review?.proposal?.user_evidence
  const status = review?.status === 'already_know'
    ? `Self-reported evidence · ${review.proposal?.approval_status ?? 'pending'}`
    : review?.status === 'not_experienced' ? 'Not experienced' : 'Review later'

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const failure = await save({ status: 'already_know', source, evidence: text })
    setError(failure ?? '')
    if (!failure) setOpen(false)
  }

  async function setReview(status: 'not_experienced' | 'review_later') {
    const failure = await save({ status })
    setError(failure ?? '')
    if (!failure) setOpen(false)
  }

  return <div className="rounded-md border border-stone-200 p-3 text-sm">
    <p className="font-semibold">{item.keyword} · {item.priority}</p>
    <p className="mt-1 text-stone-600">{status}</p>
    <div className="mt-2 flex flex-wrap gap-2">
      <button type="button" className={BUTTON} disabled={busy} onClick={() => {
        setSource(existing?.source ?? 'project'); setText(existing?.text ?? ''); setError(''); setOpen(true)
      }}>{existing ? 'Edit Skill Evidence' : 'Already Know This Skill'}</button>
      <button type="button" className={BUTTON} disabled={busy} onClick={() => void setReview('not_experienced')}>Not Experienced</button>
      <button type="button" className={BUTTON} disabled={busy} onClick={() => void setReview('review_later')}>Review Later</button>
    </div>
    {open && <form className="mt-3 space-y-2" onSubmit={submit}>
      <label className="block font-medium">Evidence source
        <select className={FIELD} value={source} onChange={(event) => setSource(event.target.value as EvidenceSource)}>
          {Object.entries(EVIDENCE_LABELS).map(([value, name]) => <option key={value} value={value}>{name}</option>)}
        </select>
      </label>
      <label className="block font-medium">Describe how you used {item.keyword}
        <textarea className={FIELD} required rows={2} maxLength={700} value={text}
          onChange={(event) => setText(event.target.value)} />
      </label>
      <p className="text-xs text-stone-600">Write one resume-ready sentence. Saving creates a self-reported proposal for you to approve.</p>
      <div className="flex flex-wrap gap-2">
        <button type="submit" className={PRIMARY} disabled={busy || !text.trim()}>Save Evidence</button>
        <button type="button" className={BUTTON} disabled={busy} onClick={() => { setOpen(false); setError('') }}>Cancel</button>
      </div>
    </form>}
    {error && <p role="alert" className="mt-2 text-red-700">{error}</p>}
  </div>
}

function SuggestionCard({ item, busy, decide, save }: {
  item: Suggestion; busy: boolean
  decide: (decision: Decision) => Promise<string | undefined>
  save: (replacement: string | null) => Promise<string | undefined>
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const [error, setError] = useState('')
  const replacement = item.manual_replacement ?? item.suggested_replacement
  const terms = [...new Set([...item.relevant_jd_keywords, ...item.supporting_resume_evidence.map((source) => source.matched_text)])]
    .filter(Boolean).sort((a, b) => b.length - a.length)
    .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')
  const parts = terms ? replacement.split(new RegExp(`(?<![\\w+#])(${terms})(?![\\w+#])`, 'gi')) : [replacement]
  const selfReported = item.verification_status === 'user_attested'

  async function saveDraft(value: string | null) {
    const failure = await save(value)
    setError(failure ?? '')
    if (!failure) setDraft(null)
  }

  return (
    <article data-decision={item.approval_status} className="suggestion-card mt-3 rounded-md border border-stone-200 p-3 text-sm wrap-anywhere">
      <h4 className="font-semibold">{SECTION_LABELS[item.section]}</h4>
      <p className="mt-1 text-xs text-stone-600">
        {selfReported ? 'Self-reported evidence' : 'Verified against source'} · <span className="capitalize">{item.approval_status}</span>
      </p>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <div className="min-w-0 rounded-md bg-stone-50 p-2"><strong>Original</strong>
          <p>{item.original_text || 'New line from evidence you supplied'}</p>
        </div>
        <div className="min-w-0 rounded-md bg-teal-50 p-2"><strong>Suggested</strong><p>
          {parts.map((part, index) => index % 2 && !selfReported
            ? <mark key={index} className="bg-amber-100 text-stone-950">{part}</mark> : part)}
        </p></div>
      </div>
      <p className="mt-2">{item.reason}</p>
      {!selfReported && item.relevant_jd_keywords.length > 0 && (
        <p className="mt-1 text-xs text-stone-600">Relevant JD terms · {item.relevant_jd_keywords.join(', ')}</p>
      )}
      {item.user_evidence ? <p className="mt-1 text-xs text-stone-600">Source you provided · {EVIDENCE_LABELS[item.user_evidence.source]}</p>
        : <details className="mt-2">
          <summary className="cursor-pointer">Supporting resume evidence</summary>
          {item.supporting_resume_evidence.map((source, index) => <p key={index}>{source.matched_text} · {source.text}</p>)}
        </details>}
      {draft !== null ? <form className="mt-3 space-y-2" onSubmit={(event) => { event.preventDefault(); void saveDraft(draft) }}>
        <label className="block font-medium">Edit suggested text
          <textarea className={FIELD} rows={3} maxLength={700} required value={draft} onChange={(event) => setDraft(event.target.value)} />
        </label>
        <p className="text-xs text-stone-600">You can adjust spacing, the final period, and supported wording. Saving requires a new approval.</p>
        <div className="flex flex-wrap gap-2">
          <button type="submit" className={PRIMARY} disabled={busy || !draft.trim()}>Save Edit</button>
          <button type="button" className={BUTTON} disabled={busy} onClick={() => { setDraft(null); setError('') }}>Undo Draft</button>
        </div>
      </form> : <div className="mt-2 flex flex-wrap gap-2">
        <button type="button" className={BUTTON} disabled={busy || item.approval_status === 'accepted'}
          onClick={() => { void decide('accepted').then((failure) => setError(failure ?? '')) }}>Accept</button>
        <button type="button" className={BUTTON} disabled={busy || item.approval_status === 'rejected'}
          onClick={() => { void decide('rejected').then((failure) => setError(failure ?? '')) }}>Reject</button>
        {!selfReported && <button type="button" className={BUTTON} disabled={busy}
          onClick={() => { setDraft(replacement); setError('') }}>Edit</button>}
        {item.manual_replacement && <button type="button" className={BUTTON} disabled={busy}
          onClick={() => void saveDraft(null)}>Restore AI Wording</button>}
      </div>}
      {error && <p role="alert" className="mt-2 text-red-700">{error}</p>}
    </article>
  )
}
