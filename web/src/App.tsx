import { useEffect, useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'
import { api, clearToken, getToken, setToken, type HealthInfo, type Stats, fmtBytes } from './api'

export default function App() {
  const loc = useLocation()
  const onVideo = loc.pathname.startsWith('/videos/')
  const [health, setHealth] = useState<HealthInfo | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [needToken, setNeedToken] = useState(false)
  const [tokenInput, setTokenInput] = useState('')
  const [tokenErr, setTokenErr] = useState<string | null>(null)

  // First, figure out if we need a token.
  useEffect(() => {
    api.health().then((h) => {
      setHealth(h)
      if (h.auth_required && !getToken()) setNeedToken(true)
    }).catch(() => setHealth({ status: 'unreachable', auth_required: false }))
  }, [])

  // Then, try to load stats — if it fails with 401, prompt for token.
  useEffect(() => {
    if (!health || needToken) return
    api.stats()
      .then(setStats)
      .catch((e) => {
        if (String(e).includes('401')) { clearToken(); setNeedToken(true) }
      })
  }, [health, needToken])

  const trySignIn = async () => {
    setToken(tokenInput.trim())
    try {
      const s = await api.stats()
      setStats(s)
      setNeedToken(false)
      setTokenErr(null)
    } catch (e) {
      setTokenErr(String(e))
      clearToken()
    }
  }

  if (needToken) {
    return (
      <div className="app">
        <div className="card col" style={{ maxWidth: 480, margin: '80px auto' }}>
          <h1 style={{ margin: 0 }}>Reel</h1>
          <p className="muted">This server requires an access token.</p>
          <input
            type="password"
            placeholder="paste your token"
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void trySignIn() }}
            autoFocus
          />
          {tokenErr && <div className="small" style={{ color: 'var(--red)' }}>{tokenErr}</div>}
          <button className="primary" onClick={trySignIn} disabled={!tokenInput.trim()}>
            sign in
          </button>
          <div className="small muted">
            Ask the person who runs this server for the token (the value of the <code>CLIPPER_TOKEN</code> env var on the host).
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="app">
      <header className="header">
        <Link to="/"><h1>Reel</h1></Link>
        {onVideo && <span className="crumb">/ video</span>}
        <div className="spacer" />
        {stats && (
          <span className="small muted">
            {stats.videos_count} videos · {stats.clips_count} clips · {fmtBytes(stats.total_bytes)} on disk
          </span>
        )}
        {health?.auth_required && (
          <button
            className="small"
            title="forget the saved access token"
            onClick={() => { clearToken(); setNeedToken(true) }}
          >
            sign out
          </button>
        )}
      </header>
      <Outlet />
    </div>
  )
}
