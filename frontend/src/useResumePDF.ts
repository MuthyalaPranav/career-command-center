import { useEffect, useState } from 'react'
import { HISTORY_API } from './historyApi'
import { apiFetch } from './resumeApi'

async function fetchPDF(path: string, signal?: AbortSignal) {
  const response = await apiFetch(path, { signal })
  if (!response.ok || response.headers.get('Content-Type') !== 'application/pdf') {
    throw new Error('Could not load this saved PDF.')
  }
  return response.blob()
}

export async function downloadSavedResume(id: string) {
  const blob = await fetchPDF(`${HISTORY_API}/${encodeURIComponent(id)}/download`)
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `resume-${id}.pdf`
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function useResumePDF(id: string | undefined, source: 'generated' | 'history' = 'generated') {
  const [loaded, setLoaded] = useState<{ id: string; url: string; error: string }>()

  useEffect(() => {
    if (!id) return
    const generationId = id
    const controller = new AbortController()
    let url = ''
    async function load() {
      try {
        const base = source === 'history' ? HISTORY_API : '/api/resumes/generated'
        const blob = await fetchPDF(`${base}/${encodeURIComponent(generationId)}/download?inline=true`, controller.signal)
        if (controller.signal.aborted) return
        url = URL.createObjectURL(blob)
        setLoaded({ id: generationId, url, error: '' })
      } catch {
        if (!controller.signal.aborted) setLoaded({ id: generationId, url: '', error: 'Could not load this saved PDF.' })
      }
    }
    void load()
    return () => {
      controller.abort()
      if (url) URL.revokeObjectURL(url)
    }
  }, [id, source])

  return loaded?.id === id ? loaded : undefined
}
