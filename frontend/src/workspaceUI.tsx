import { useEffect, useId, useRef } from 'react'
import type { ReactNode } from 'react'
import {
  ChevronDown, FileText, FolderOpen, History, LayoutDashboard, List, LockKeyhole, Menu, Moon,
  PanelLeft, PencilLine, Plus, ScanLine, Search, Settings, Sparkles, Sun, Target, Upload, UserRound, X,
} from 'lucide-react'

const icons = {
  dashboard: LayoutDashboard, file: FileText, scan: ScanLine, spark: Sparkles, folder: FolderOpen,
  history: History, settings: Settings, search: Search, sun: Sun, moon: Moon, panel: PanelLeft,
  menu: Menu, close: X, user: UserRound, chevron: ChevronDown, target: Target, plus: Plus,
  edit: PencilLine, list: List, lock: LockKeyhole, upload: Upload,
}

export type IconName = keyof typeof icons

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const Glyph = icons[name]
  return <Glyph aria-hidden="true" size={size} strokeWidth={1.65} />
}

export function EmptyState({ icon, title, children }: { icon: IconName; title: string; children?: ReactNode }) {
  return <div className="empty-state">
    <span className="icon-chip"><Icon name={icon} size={21} /></span>
    <strong>{title}</strong>
    {children && <div>{children}</div>}
  </div>
}

export function LoadingSkeleton({ label }: { label: string }) {
  return <div className="loading-skeleton" role="status" aria-label={label}>
    <span /><span /><span />
  </div>
}

export function MatchScore({ value }: { value: number | null | undefined }) {
  if (value == null) return <span>—</span>
  return <span title="Estimated keyword coverage">{value}%</span>
}

export function ResumePreview({ url, title }: { url: string; title: string }) {
  return <iframe title={title} src={url} loading="lazy" className="pdf-preview" />
}

export function ConfirmDialog({ title, children, busy, onCancel, onConfirm }: {
  title: string; children: ReactNode; busy: boolean; onCancel: () => void; onConfirm: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const heading = useId()
  useEffect(() => {
    const element = dialog.current
    element?.showModal()
    return () => element?.close()
  }, [])
  return <dialog ref={dialog} className="confirm-dialog surface" aria-labelledby={heading} onCancel={(event) => {
    event.preventDefault()
    if (!busy) onCancel()
  }}>
    <h4 id={heading}>{title}</h4>
    <p>{children}</p>
    <div>
      <button type="button" className="secondary-button" onClick={onCancel} disabled={busy} autoFocus>Cancel</button>
      <button type="button" className="primary-button confirm-action" onClick={onConfirm} disabled={busy}>Confirm deletion</button>
    </div>
  </dialog>
}
