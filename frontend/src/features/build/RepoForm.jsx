import { useState } from 'react'

export default function RepoForm({ onSubmit, isLoading }) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')

  function validate(value) {
    if (!value.trim()) {
      return 'Please enter a GitHub URL.'
    }
    if (!value.trim().startsWith('https://github.com/')) {
      return 'URL must start with https://github.com/'
    }
    return ''
  }

  function handleSubmit(e) {
    e.preventDefault()
    const err = validate(url)
    if (err) {
      setError(err)
      return
    }
    setError('')
    onSubmit(url.trim())
  }

  function handleChange(e) {
    setUrl(e.target.value)
    if (error) setError('')
  }

  return (
    <div className="forge-card">
      <form onSubmit={handleSubmit} noValidate>
        <label className="forge-card__label" htmlFor="repo-url">
          Repository URL
        </label>

        <div className="forge-field">
          <span className="forge-field__icon" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.34-3.37-1.34-.45-1.16-1.11-1.47-1.11-1.47-.9-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.9 1.53 2.36 1.09 2.94.83.09-.65.35-1.09.63-1.34-2.22-.25-4.55-1.11-4.55-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.65 0 0 .84-.27 2.75 1.02a9.5 9.5 0 0 1 5 0c1.91-1.29 2.75-1.02 2.75-1.02.55 1.38.2 2.4.1 2.65.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.69-4.57 4.94.36.31.68.92.68 1.85v2.74c0 .27.18.58.69.48A10 10 0 0 0 12 2Z" fill="currentColor"/>
            </svg>
          </span>
          <input
            id="repo-url"
            type="url"
            value={url}
            onChange={handleChange}
            placeholder="https://github.com/user/repo"
            disabled={isLoading}
            aria-label="GitHub repository URL"
            style={error ? { borderColor: 'var(--bad)', boxShadow: '0 0 0 4px var(--bad-tint)' } : undefined}
          />
        </div>

        {error && (
          <p className="forge-error">
            <span aria-hidden="true">⚠</span>
            {error}
          </p>
        )}

        <button type="submit" className="btn btn-fill" disabled={isLoading}>
          {isLoading ? (
            <>
              <svg className="spin" width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.25" />
                <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
              </svg>
              Building…
            </>
          ) : (
            <>Build container ↗</>
          )}
        </button>
      </form>

      <p className="forge-hint">Public repositories only · no sign-in required</p>
    </div>
  )
}
