import { useEffect, useRef, useState } from 'react'
import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

GlobalWorkerOptions.workerSrc = workerUrl

export default function PdfThumbnail({ url }: { url: string }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  useEffect(() => {
    let active = true
    setState('loading')
    const task = getDocument({ url, useSystemFonts: true })
    async function render() {
      const document = await task.promise
      const page = await document.getPage(1)
      if (!active || !canvas.current) return
      const bounds = page.getViewport({ scale: 1 })
      const viewport = page.getViewport({ scale: 440 / Math.max(bounds.width, bounds.height * .7) })
      canvas.current.width = Math.ceil(viewport.width)
      canvas.current.height = Math.ceil(viewport.height)
      await page.render({ canvas: canvas.current, viewport }).promise
      if (active) setState('ready')
    }
    void render().catch(() => { if (active) setState('error') }).finally(() => { if (active) void task.destroy() })
    return () => { active = false; void task.destroy() }
  }, [url])
  return <><canvas ref={canvas} hidden={state !== 'ready'}/>{state !== 'ready' && <small>{state === 'loading' ? 'Rendering page…' : 'Preview unavailable'}</small>}</>
}
