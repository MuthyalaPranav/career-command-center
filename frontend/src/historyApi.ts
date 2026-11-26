import { apiFetch } from './resumeApi'

export type GenerationMode = 'ats' | 'original_layout'

export type ResumeVersion = {
  generation_id: string
  resume_id: string
  match_id: string
  job_id: string | null
  mode: GenerationMode
  original_filename?: string | null
  job_title: string | null
  created_at: string | null
  page_count: number
  skills: string[] | null
  keywords: string[] | null
  status: 'available' | 'unavailable'
  source: 'generated' | 'legacy'
  name: string | null
  favorite: boolean
}

export type VersionDetail = ResumeVersion & {
  snapshot: { content: { sections: Record<string, string[]> } | null }
}

export type ActiveMaster = { resume_id: string }
export type ResumeOption = { id: string; label: string; original_filename?: string | null }

export function uploadLabel(id: string, originalFilename?: string) {
  return originalFilename?.replace(/\.(pdf|docx)$/i, '') || `Resume - ${id.slice(0, 8)}`
}

export function versionName(version: { name: string | null; job_title: string | null; resume_id?: string; original_filename?: string | null }, options: ResumeOption[] = []) {
  const original = options.find(({ id }) => id === version.resume_id)?.original_filename
  return version.name ?? version.original_filename ?? original ?? version.job_title ?? 'Untitled resume'
}

export function versionDate(value: string | null) {
  return value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(value)) : 'Date unavailable'
}

export const HISTORY_API = '/api/resumes/history'

export async function historyRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await apiFetch(path, options)
  if (response.status === 204) return undefined as T
  const data = await response.json().catch(() => undefined)
  if (!response.ok || data === undefined) {
    throw new Error(typeof data?.detail === 'string' ? data.detail : 'Could not complete the request. Try again.')
  }
  return data as T
}

export function historyJSON(method: 'PATCH' | 'POST' | 'PUT', value: object): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) }
}

export async function historyPage(query: URLSearchParams, signal: AbortSignal) {
  const response = await apiFetch(`${HISTORY_API}?${query}`, { signal })
  if (!response.ok) throw new Error('Could not load saved resumes. Try again.')
  const versions: ResumeVersion[] = await response.json()
  return { versions, total: Number(response.headers.get('X-Total-Count') ?? versions.length) }
}
