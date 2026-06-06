const DEFAULT_STAGES = [
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

function normalise(stages) {
  if (!stages || stages.length === 0) {
    return DEFAULT_STAGES.map(s => ({ ...s, status: 'WAITING', message: '' }))
  }
  return DEFAULT_STAGES.map(def => {
    const live = stages.find(s => s.id === def.id)
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

export default function PipelineSteps({ stages }) {
  const items = normalise(stages)

  return (
    <div className="stepper">
      {items.map((stage, i) => {
        const status = (stage.status ?? 'WAITING').toUpperCase()
        return (
          <div key={stage.id} className={`stp ${CLASS[status] ?? 'is-waiting'}`}>
            <div className="stp__rail">
              <div className="stp__node">
                <Node status={status} index={i + 1} />
              </div>
              <div className="stp__wire" />
            </div>
            <div className="stp__body">
              <div className="stp__name">{stage.name}</div>
              {stage.message ? <div className="stp__msg">{stage.message}</div> : null}
            </div>
          </div>
        )
      })}
    </div>
  )
}
