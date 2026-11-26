import { useId, useRef, useState } from 'react'
import type { DragEvent, FormEvent } from 'react'
import ResumeExtraction from './ResumeExtraction'
import { apiFetch } from './resumeApi'
import { Icon } from './workspaceUI'

const MAX_RESUME_SIZE = 5 * 1024 * 1024
const FILE_ACCEPT = '.pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document'

type UploadState =
  | { status: 'idle' | 'loading' }
  | { status: 'error'; message: string }
  | { status: 'success'; message: string; resumeId: string }

function getFileType(file: File) {
  const name = file.name.toLowerCase()
  if (name.endsWith('.pdf')) return 'PDF'
  if (name.endsWith('.docx')) return 'DOCX'
  return null
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} ${bytes === 1 ? 'byte' : 'bytes'}`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

export default function ResumeUploader({ onUploaded, onExtracted }: {
  onUploaded: (id: string, originalFilename: string, file: File) => void; onExtracted: () => void
}) {
  const headingId = useId()
  const helpId = useId()
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [upload, setUpload] = useState<UploadState>({ status: 'idle' })
  const isUploading = upload.status === 'loading'

  function selectFiles(files: FileList | null) {
    if (!files?.length || isUploading) return

    setFile(null)
    const candidate = files[0]
    if (files.length !== 1) {
      setUpload({ status: 'error', message: 'Choose one resume at a time.' })
    } else if (!getFileType(candidate)) {
      setUpload({ status: 'error', message: 'Choose a PDF or DOCX file.' })
    } else if (candidate.size > MAX_RESUME_SIZE) {
      setUpload({ status: 'error', message: 'The resume must be 5 MB or smaller.' })
    } else if (candidate.size === 0) {
      setUpload({ status: 'error', message: 'The resume file is empty.' })
    } else {
      setFile(candidate)
      setUpload({ status: 'idle' })
    }
  }

  function dropFile(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    selectFiles(event.dataTransfer.files)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file || isUploading || upload.status === 'success') return

    const form = new FormData()
    form.append('file', file)
    setUpload({ status: 'loading' })

    try {
      const response = await apiFetch(`/api/resumes/upload`, {
        method: 'POST',
        body: form,
      })
      const result = await response.json().catch(() => null)
      if (response.status === 201 && typeof result?.resume_id === 'string' && typeof result.message === 'string') {
        setUpload({ status: 'success', message: result.message, resumeId: result.resume_id })
        onUploaded(result.resume_id, result.original_filename || file.name, file)
      } else {
        setUpload({
          status: 'error',
          message: typeof result?.detail === 'string' ? result.detail : 'Upload failed. Please try again.',
        })
      }
    } catch {
      setUpload({ status: 'error', message: 'Could not reach the upload service. Please try again.' })
    }
  }

  return (
    <form aria-labelledby={headingId} className="surface upload-panel" onSubmit={submit}>
      <h2 id={headingId} className="text-lg font-semibold">Upload your resume</h2>
      <div
        className="upload-dropzone mt-3 rounded-lg border border-dashed border-stone-400 p-4 sm:p-5"
        onDragOver={(event) => event.preventDefault()}
        onDrop={dropFile}
      >
        <span className="icon-chip"><Icon name="upload" size={19} /></span>
        <input
          ref={input}
          type="file"
          accept={FILE_ACCEPT}
          hidden
          disabled={isUploading}
          onChange={(event) => {
            selectFiles(event.currentTarget.files)
            event.currentTarget.value = ''
          }}
        />
        <button
          type="button"
          aria-describedby={helpId}
          disabled={isUploading}
          onClick={() => input.current?.click()}
          className="rounded-md border border-stone-300 px-3 py-2 text-sm font-semibold hover:bg-stone-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 disabled:opacity-50"
        >
          {file ? 'Change file' : 'Choose file'}
        </button>
        <p id={helpId} className="mt-2 text-sm text-stone-600">Or drag a PDF or DOCX here. Maximum 5 MB.</p>
        {file && (
          <dl className="mt-3 space-y-1 border-t border-stone-200 pt-3 text-sm">
            <div className="flex gap-2">
              <dt className="shrink-0 font-medium">File</dt>
              <dd className="wrap-anywhere">{file.name}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="font-medium">Size</dt>
              <dd>{formatFileSize(file.size)}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="font-medium">Type</dt>
              <dd>{getFileType(file)}</dd>
            </div>
          </dl>
        )}
      </div>
      <button
        type="submit"
        disabled={!file || isUploading || upload.status === 'success'}
        className="mt-4 rounded-md bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {isUploading ? 'Uploading…' : 'Upload Resume'}
      </button>
      <p role="status" className="mt-2 text-sm text-teal-800">
        {isUploading ? 'Uploading your resume.' : upload.status === 'success' ? upload.message : ''}
      </p>
      <p role="alert" className="text-sm text-red-700">
        {upload.status === 'error' ? upload.message : ''}
      </p>
      {upload.status === 'success' && <ResumeExtraction key={upload.resumeId} resumeId={upload.resumeId} onExtracted={onExtracted} />}
    </form>
  )
}
