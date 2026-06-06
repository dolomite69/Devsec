import { useEffect, useRef } from 'react'

function lineTone(text) {
  const upper = text.toUpperCase()
  if (upper.includes('ERROR') || upper.includes('FAILED')) return 'bad'
  if (upper.includes('SUCCESS') || upper.includes('DONE')) return 'ok'
  if (upper.includes('WARNING')) return 'warn'
  return 'info'
}

export default function LogViewer({ logs = [], isStreaming = false }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  return (
    <div>
      <div className="console__head">
        <span className="console__dots"><i /><i /><i /></span>
        <span className="console__title">agent · live output</span>
        {isStreaming && (
          <span className="console__live"><i /> streaming</span>
        )}
      </div>

      <div className="console__body">
        {logs.length === 0 ? (
          <span className="console__empty">$ waiting for the agent to start…</span>
        ) : (
          logs.map((line, i) => (
            <div key={i} className={`logrow logrow--${lineTone(line)}`}>
              <span className="logrow__ln">{i + 1}</span>
              <span className="logrow__txt">{line}</span>
            </div>
          ))
        )}

        {isStreaming && <span className="caret">▍</span>}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
