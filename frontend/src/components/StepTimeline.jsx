const DEFAULT_STEPS = [
  { id: 1, name: 'Validate URL' },
  { id: 2, name: 'Clone Repository' },
  { id: 3, name: 'Analyze Codebase' },
  { id: 4, name: 'Generate Dockerfile' },
  { id: 5, name: 'Build Docker Image' },
  { id: 6, name: 'Run Container' },
  { id: 7, name: 'Done' },
]

const CLASS = {
  WAITING: 'is-waiting',
  RUNNING: 'is-running',
  DONE: 'is-done',
  ERROR: 'is-error',
}

function normalise(steps) {
  if (!steps || steps.length === 0) {
    return DEFAULT_STEPS.map(s => ({ ...s, status: 'WAITING', message: '' }))
  }
  return DEFAULT_STEPS.map(def => {
    const live = steps.find(s => s.id === def.id)
    return live
      ? { ...def, status: live.status, message: live.message ?? '' }
      : { ...def, status: 'WAITING', message: '' }
  })
}

function Node({ status, index }) {
  if (status === 'DONE') {
    return (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="m5 13 4 4L19 7" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    )
  }
  if (status === 'ERROR') {
    return (
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M6 6l12 12M18 6 6 18" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      </svg>
    )
  }
  if (status === 'RUNNING') {
    return (
      <svg className="spin" width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.25" />
        <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      </svg>
    )
  }
  return <span>{index}</span>
}

export default function StepTimeline({ steps }) {
  const items = normalise(steps)

  return (
    <div className="stepper">
      {items.map((step, i) => {
        const status = (step.status ?? 'WAITING').toUpperCase()
        return (
          <div key={step.id} className={`stp ${CLASS[status] ?? 'is-waiting'}`}>
            <div className="stp__rail">
              <div className="stp__node">
                <Node status={status} index={i + 1} />
              </div>
              <div className="stp__wire" />
            </div>
            <div className="stp__body">
              <div className="stp__name">{step.name}</div>
              {step.message ? <div className="stp__msg">{step.message}</div> : null}
            </div>
          </div>
        )
      })}
    </div>
  )
}
