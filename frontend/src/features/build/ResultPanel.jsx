import { useState } from 'react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'

export default function ResultPanel({ dockerfile, buildId }) {
  const [copied, setCopied] = useState(false)

  if (!dockerfile) return null

  function handleCopy() {
    navigator.clipboard.writeText(dockerfile).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  function handleDownload() {
    const blob = new Blob([dockerfile], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'Dockerfile'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="output">
      <div className="output__bar">
        <div className="output__left">
          <span className="output__badge" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path d="M4 7h4v4H4V7Zm5 0h4v4H9V7Zm5 0h4v4h-4V7Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
              <path d="M3 14c2.5 2.5 6 3.5 9 3.5S18.5 16 20 13c.5 1.5 1 .5 1-1" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <div>
            <div className="output__title">Generated Dockerfile</div>
            <div className="output__job">build · {buildId || '—'}</div>
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

      <div className="output__code">
        <SyntaxHighlighter
          language="docker"
          style={oneLight}
          customStyle={{
            margin: 0,
            background: 'transparent',
            fontSize: '0.82rem',
            lineHeight: '1.7',
            padding: '1.25rem 1.4rem',
          }}
          showLineNumbers
        >
          {dockerfile}
        </SyntaxHighlighter>
      </div>
    </div>
  )
}
