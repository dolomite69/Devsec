import { useMemo, useState } from 'react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'

const LANG_MAP = { docker: 'docker', yaml: 'yaml', nginx: 'nginx' }

export default function ResultPanel({ dockerfile, files, buildId, previewUrl, projectName }) {
  const [activeIdx, setActiveIdx] = useState(0)
  const [copied, setCopied] = useState(false)

  // Prefer the multi-file `files` array; fall back to the single Dockerfile.
  const fileList = useMemo(() => {
    if (Array.isArray(files) && files.length > 0) return files
    if (dockerfile) return [{ path: 'Dockerfile', content: dockerfile, language: 'docker' }]
    return []
  }, [files, dockerfile])

  if (fileList.length === 0) return null

  const safeIdx = Math.min(activeIdx, fileList.length - 1)
  const active = fileList[safeIdx]
  const isStack = fileList.length > 1

  function handleCopy() {
    navigator.clipboard.writeText(active.content).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  function handleDownload() {
    const blob = new Blob([active.content], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = active.path.split('/').pop() || 'Dockerfile'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="output">
      {previewUrl && (
        <a
          className="preview"
          href={previewUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          <span className="preview__pulse" aria-hidden="true" />
          <span className="preview__text">
            <span className="preview__label">{isStack ? 'Frontend is live' : 'App is live'}</span>
            <span className="preview__url">{previewUrl}</span>
          </span>
          <span className="preview__cta">Open running app ↗</span>
        </a>
      )}

      <div className="output__bar">
        <div className="output__left">
          <span className="output__badge" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path d="M4 7h4v4H4V7Zm5 0h4v4H9V7Zm5 0h4v4h-4V7Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
              <path d="M3 14c2.5 2.5 6 3.5 9 3.5S18.5 16 20 13c.5 1.5 1 .5 1-1" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <div>
            <div className="output__title">{isStack ? 'Generated deployment' : 'Generated Dockerfile'}</div>
            <div className="output__job">{projectName ? `${projectName} · ` : ''}build · {buildId || '—'}</div>
          </div>
        </div>

        <div className="output__actions">
          <button className="btn btn-ghost" onClick={handleCopy}>
            {copied ? 'Copied ✓' : 'Copy'}
          </button>
          <button className="btn btn-fill" onClick={handleDownload}>
            ↓ Download
          </button>
        </div>
      </div>

      {isStack && (
        <div className="filetabs" role="tablist">
          {fileList.map((f, i) => (
            <button
              key={f.path}
              role="tab"
              aria-selected={i === safeIdx}
              className={`filetab ${i === safeIdx ? 'is-active' : ''}`}
              onClick={() => setActiveIdx(i)}
            >
              {f.path}
            </button>
          ))}
        </div>
      )}

      <div className="output__code">
        <SyntaxHighlighter
          language={LANG_MAP[active.language] || 'docker'}
          style={oneDark}
          customStyle={{
            margin: 0,
            background: 'transparent',
            fontSize: '0.82rem',
            lineHeight: '1.7',
            padding: '1.25rem 1.4rem',
          }}
          showLineNumbers
        >
          {active.content}
        </SyntaxHighlighter>
      </div>
    </div>
  )
}
