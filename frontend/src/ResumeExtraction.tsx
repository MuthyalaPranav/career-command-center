import { useEffect, useRef, useState } from 'react'
import { apiFetch } from './resumeApi'
import { LoadingSkeleton } from './workspaceUI'

const SECTION_LABELS = {
  contact_information: 'Contact Information',
  professional_summary: 'Professional Summary',
  technical_skills: 'Technical Skills',
  work_experience: 'Work Experience',
  projects: 'Projects',
  education: 'Education',
  certifications: 'Certifications',
  unclassified: 'Unclassified',
}

type ExtractionResult = {
  resume_id: string
  raw_text: string
  sections: Record<string, string[]>
  extraction_metadata: { image_only_pages?: number[] }
}

type ExtractionState =
  | { status: 'idle' | 'loading' }
  | { status: 'error'; message: string }
  | { status: 'success'; result: ExtractionResult }

export default function ResumeExtraction({ resumeId, autoLoad = false, onExtracted }: {
  resumeId: string; autoLoad?: boolean; onExtracted?: () => void
}) {
  const [state, setState] = useState<ExtractionState>({ status: 'idle' })
  const pendingRequest = useRef<AbortController | null>(null)
  const isExtracting = state.status === 'loading'

  useEffect(() => {
    if (autoLoad && state.status !== 'success') void extract()
    return () => {
      pendingRequest.current?.abort()
      pendingRequest.current = null
    }
  }, [resumeId, autoLoad])

  async function extract() {
    if (pendingRequest.current) return

    const request = new AbortController()
    pendingRequest.current = request
    setState({ status: 'loading' })

    try {
      const response = await apiFetch(`/api/resumes/${encodeURIComponent(resumeId)}/extract`, {
        signal: request.signal,
      })
      const result = await response.json().catch(() => null)
      if (request.signal.aborted) return

      if (response.ok && result?.resume_id === resumeId && typeof result.raw_text === 'string'
        && result.sections && result.extraction_metadata) {
        setState({ status: 'success', result })
        onExtracted?.()
      } else {
        setState({
          status: 'error',
          message: typeof result?.detail === 'string' ? result.detail : 'Extraction failed. Please try again.',
        })
      }
    } catch {
      if (!request.signal.aborted) {
        setState({ status: 'error', message: 'Could not reach the extraction service. Please try again.' })
      }
    } finally {
      if (pendingRequest.current === request) pendingRequest.current = null
    }
  }

  return (
    <div className="mt-4 border-t border-stone-200 pt-4">
      {(!autoLoad || state.status === 'error') && <button
        type="button"
        onClick={extract}
        disabled={isExtracting || state.status === 'success'}
        className="rounded-md bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {isExtracting ? 'Extracting…' : autoLoad ? 'Retry extraction' : 'Extract Resume'}
      </button>}
      <p role="status" className="mt-2 text-sm text-teal-800">
        {isExtracting ? 'Extracting and organizing resume text.' : state.status === 'success' ? 'Resume extracted.' : ''}
      </p>
      {isExtracting && autoLoad && <LoadingSkeleton label="Loading extracted resume sections" />}
      <p role="alert" className="text-sm text-red-700">
        {state.status === 'error' ? state.message : ''}
      </p>
      {state.status === 'success' && <details className="result-disclosure"><summary>Preview extracted resume text</summary><ExtractionResults result={state.result} /></details>}
    </div>
  )
}

function ExtractionResults({ result }: { result: ExtractionResult }) {
  const imageOnlyPages = result.extraction_metadata.image_only_pages ?? []

  return (
    <div className="mt-5 space-y-4">
      <h2 className="text-lg font-semibold">Extracted resume</h2>
      {imageOnlyPages.length > 0 && (
        <p className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
          Image-only pages without extracted text ({imageOnlyPages.join(', ')}). OCR is not yet supported.
        </p>
      )}
      {Object.entries(SECTION_LABELS).map(([key, label]) => {
        const lines = result.sections[key]
        if (!lines?.length) return null

        return (
          <section key={key} className="rounded-md border border-stone-200 bg-white p-4">
            <h3 className="text-sm font-semibold text-stone-950">{label}</h3>
            <p className="mt-2 text-sm leading-relaxed wrap-anywhere whitespace-pre-wrap text-stone-700">
              {lines.join('\n')}
            </p>
          </section>
        )
      })}
      <details className="rounded-md border border-stone-200 bg-white p-4">
        <summary className="cursor-pointer rounded-sm text-sm font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700">
          View complete raw text
        </summary>
        <pre className="mt-3 text-sm leading-relaxed wrap-anywhere whitespace-pre-wrap text-stone-700">
          {result.raw_text}
        </pre>
      </details>
    </div>
  )
}
