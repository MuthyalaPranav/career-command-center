const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '')
const TOKEN_KEY = 'resume-workspace-token'

export function setWorkspaceToken(token: string) {
  if (token) sessionStorage.setItem(TOKEN_KEY, token)
  else sessionStorage.removeItem(TOKEN_KEY)
}

export function hasWorkspaceToken() {
  return Boolean(sessionStorage.getItem(TOKEN_KEY))
}

export function apiFetch(path: string, options?: RequestInit) {
  const headers = new Headers(options?.headers)
  const token = sessionStorage.getItem(TOKEN_KEY)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return fetch(`${API_BASE_URL}${path}`, { ...options, headers, redirect: 'error' })
}
