import { useEffect, useRef, useState } from 'react'
import { ArrowUpRight, Expand, FileText, Plus, X } from 'lucide-react'
import { historyRequest } from './historyApi'
import type { ResumeOption } from './historyApi'

type SourceText = { raw_text: string; sections: Record<string, string[]> }

function DocumentText({ source }: { source: SourceText }) {
  const sections = Object.entries(source.sections).filter(([, lines]) => lines.length)
  return <>{sections.length ? sections.map(([name, lines]) => <section key={name}>
    <h3>{name.replaceAll('_', ' ')}</h3>{lines.map((line, i) => <p key={i}>{line}</p>)}
  </section>) : <p>{source.raw_text || 'No searchable text was extracted from this document.'}</p>}</>
}

export default function MasterFolio({ id, resume, status }: { id: string | null; resume?: ResumeOption; status: string }) {
  const [source, setSource] = useState<SourceText>()
  const [failed, setFailed] = useState(false)
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    if (!id) return
    const controller = new AbortController()
    historyRequest<SourceText>(`/api/resumes/${encodeURIComponent(id)}/extract`, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setSource(data) })
      .catch(() => { if (!controller.signal.aborted) setFailed(true) })
    return () => controller.abort()
  }, [id])
  const name = resume?.original_filename || resume?.label || 'Master resume'
  const empty = !id && status === 'Not set'
  const page = <><span className="summary-file-icon"><FileText size={30}/></span><span>
    <strong>{id ? name : empty ? 'Add your master resume' : status}</strong>
    <small>{id ? 'Preview extracted content' : 'Upload or choose a PDF or DOCX'}</small></span>
    {id ? <Expand size={18}/> : <Plus size={18}/>}</>
  return <section className="master-summary" aria-labelledby="folio-title">
    <header><h2 id="folio-title">Master resume</h2><span>{id ? 'Active master' : status}</span></header>
    {id ? <button className="folio-object summary-file" onClick={() => dialog.current?.showModal()}>{page}</button>
      : <a className="folio-object summary-file" href="#active-master">{page}</a>}
    <a className="desk-link" href="#active-master">Manage resumes <ArrowUpRight size={15}/></a>
    <dialog ref={dialog} className="folio-dialog" aria-label="Master resume reading view" onClick={e => { if (e.target === dialog.current) dialog.current.close() }}>
      <header><strong>{name}</strong><button aria-label="Close reading view" className="icon-button" onClick={() => dialog.current?.close()}><X/></button></header>
      <article>{source ? <DocumentText source={source}/> : <p role="status">{failed ? 'Source text unavailable.' : 'Opening source text…'}</p>}</article>
      <a className="text-action" href="#active-master">Open master resume</a>
    </dialog>
  </section>
}
