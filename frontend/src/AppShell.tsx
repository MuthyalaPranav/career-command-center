import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Icon } from './workspaceUI'
import type { IconName } from './workspaceUI'

export const pages = {
  'how-it-works': ['How it works', 'From master resume to saved export', 'file'],
  dashboard: ['Overview', 'Your workspace at a glance', 'dashboard'],
  'active-master': ['My Resumes', 'Manage your source resume and extracted content', 'file'],
  jobs: ['Job Descriptions', 'Analyze and manage saved roles', 'folder'],
  'match-studio': ['JD Match Studio', 'Compare resume evidence with job requirements', 'target'],
  'export-center': ['Export Center', 'Prepare and download your approved document', 'file'],
  'jd-analyzer': ['JD Analyzer', 'Understand requirements and compare your experience', 'scan'],
  'resume-matching': ['AI Resume Editor', 'Review suggestions and prepare your next resume', 'spark'],
  'my-resumes': ['Resume Library', 'Find and manage your saved resumes', 'folder'],
  'resume-history': ['Resume History', 'Browse earlier resume exports', 'history'],
  settings: ['Settings', 'Manage workspace appearance and access', 'settings'],
  tailor: ['Tailor resume', 'Upload, analyze, compare, and export in one view', 'file'],
} satisfies Record<string, [string, string, IconName]>
export type Page = keyof typeof pages

const navigation: Page[] = ['dashboard', 'active-master', 'jobs', 'match-studio', 'resume-matching', 'export-center', 'resume-history']

export function ThemeToggle() {
  const [theme, setTheme] = useState(document.documentElement.dataset.theme ?? 'dark')
  useEffect(() => {
    const refresh = () => setTheme(document.documentElement.dataset.theme ?? 'dark')
    window.addEventListener('career-theme-change', refresh)
    return () => window.removeEventListener('career-theme-change', refresh)
  }, [])
  const next = theme === 'dark' ? 'light' : 'dark'
  return <button type="button" className="icon-button" aria-label={`Switch to ${next} theme`} title={`Switch to ${next} theme`}
    onClick={() => {
      document.documentElement.dataset.theme = next
      try { localStorage.setItem('career-workspace-theme', next) } catch { /* Theme still applies to this page. */ }
      window.dispatchEvent(new Event('career-theme-change'))
    }}><Icon name={theme === 'dark' ? 'sun' : 'moon'} size={18} /></button>
}

function Brand() {
  return <a href="#dashboard" className="workspace-brand" aria-label="Career Command Center dashboard">
    <span className="brand-mark"><Icon name="spark" size={21} /></span>
    <span className="brand-text"><strong>Career Command</strong><small>CENTER</small></span>
  </a>
}

function Sidebar({ page, onNavigate }: { page: Page; onNavigate?: () => void }) {
  function link(key: Page) {
    const [title, , icon] = pages[key]
    return <a key={key} href={`#${key}`} title={title} aria-current={key === page ? 'page' : undefined} onClick={onNavigate}>
      <Icon name={icon} size={19} /><span>{title}</span>
    </a>
  }
  return <>
    <div className="sidebar-brand"><Brand /></div>
    <nav className="sidebar-nav" aria-label="Workspace">
      <p>WORKSPACE</p>
      {navigation.map(link)}
      <div className="sidebar-settings">{link('settings')}</div>
    </nav>
    <div className="sidebar-footer"><span className="icon-chip"><Icon name="lock" size={15} /></span>
      <span>Private workspace</span>
    </div>
  </>
}

export default function AppShell({ page, onLock, onSearch, children }: {
  page: Page; onLock?: () => void; onSearch: (query: string) => void; children: ReactNode
}) {
  const [collapsed, setCollapsed] = useState(true)
  const [query, setQuery] = useState('')
  const dialog = useRef<HTMLDialogElement>(null)
  const menu = useRef<HTMLDetailsElement>(null)
  const [title] = pages[page]

  useEffect(() => {
    dialog.current?.close()
    if (menu.current) menu.current.open = false
  }, [page])

  useEffect(() => {
    const desktop = matchMedia('(min-width: 761px)')
    const closeDrawer = () => { if (desktop.matches) dialog.current?.close() }
    desktop.addEventListener('change', closeDrawer)
    return () => desktop.removeEventListener('change', closeDrawer)
  }, [])

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onSearch(query.trim())
  }

  return <div className={`app-shell${collapsed ? ' sidebar-collapsed' : ''}`}>
    <a className="skip-link" href="#workspace-main" onClick={(event) => {
      event.preventDefault()
      document.getElementById('workspace-main')?.focus()
    }}>Skip to workspace</a>
    <aside className="desktop-sidebar"><Sidebar page={page} onNavigate={() => setCollapsed(true)} /></aside>
    <dialog ref={dialog} id="mobile-navigation" className="mobile-navigation" aria-label="Workspace navigation"
      onClick={(event) => { if (event.target === event.currentTarget) dialog.current?.close() }}>
      <div className="drawer-content">
        <button type="button" className="icon-button drawer-close" aria-label="Close navigation"
          onClick={() => dialog.current?.close()}><Icon name="close" /></button>
        <Sidebar page={page} onNavigate={() => dialog.current?.close()} />
      </div>
    </dialog>
    <div className="shell-content">
      <header className="topbar">
        <div className="topbar-context">
          <button type="button" className="icon-button desktop-collapse" aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-expanded={!collapsed} onClick={() => setCollapsed(!collapsed)}><Icon name="panel" size={19} /></button>
          <button type="button" className="icon-button mobile-menu" aria-label="Open navigation" aria-haspopup="dialog"
            aria-controls="mobile-navigation" onClick={() => dialog.current?.showModal()}><Icon name="menu" /></button>
          <div className="app-wordmark"><strong>Career Command Center</strong><span>{title}</span></div>
        </div>
        <form className="global-search" role="search" onSubmit={search}>
          <button type="submit" aria-label="Search"><Icon name="search" size={17} /></button>
          <input aria-label="Search saved resumes" type="search" maxLength={255} placeholder="Search resumes" value={query}
            onChange={(event) => setQuery(event.target.value)} />
        </form>
        <div className="topbar-controls">
          <ThemeToggle />
          <details ref={menu} className="profile-menu" onKeyDown={(event) => {
            if (event.key === 'Escape' && event.currentTarget.open) {
              event.currentTarget.open = false
              event.currentTarget.querySelector('summary')?.focus()
            }
          }}>
            <summary aria-label="Workspace profile"><span className="avatar"><Icon name="user" size={17} /></span>
              <Icon name="chevron" size={13} /></summary>
            <div className="profile-dropdown">
              <strong>Workspace profile</strong>
              <a href="#my-resumes" onClick={() => { if (menu.current) menu.current.open = false }}>Search saved resumes</a>
              <a href="#settings" onClick={() => { if (menu.current) menu.current.open = false }}>Settings</a>
              {onLock && <button type="button" onClick={onLock}><Icon name="lock" size={15} />Lock workspace</button>}
            </div>
          </details>
        </div>
      </header>
      <main className="workspace-main" id="workspace-main" tabIndex={-1}>{children}</main>
    </div>
  </div>
}
