import { useCallback, useEffect, useState } from 'react'
import { api, fmtTime, type AnalyzeConfig, type SuggestionsPayload } from '../api'

interface Props {
  videoId: string
  indexReady: boolean
  onJump: (start: number, end?: number) => void
  onEdit: (start: number, end: number) => void
}

export default function SuggestionsPanel({ videoId, indexReady, onJump, onEdit }: Props) {
  const [config, setConfig] = useState<AnalyzeConfig | null>(null)
  const [payload, setPayload] = useState<SuggestionsPayload | null>(null)
  const [prompt, setPrompt] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editingPrompt, setEditingPrompt] = useState(false)

  // Load config + any cached suggestions.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const [cfg, sug] = await Promise.all([
          api.analyzeConfig(),
          api.getSuggestions(videoId),
        ])
        if (cancelled) return
        setConfig(cfg)
        setPayload(sug)
        setPrompt(sug.prompt || cfg.default_prompt)
      } catch (e) {
        if (!cancelled) setError(String(e))
      }
    })()
    return () => { cancelled = true }
  }, [videoId])

  const regenerate = useCallback(async () => {
    if (!prompt.trim()) return
    setBusy(true); setError(null)
    try {
      const p = await api.regenerateSuggestions(videoId, prompt.trim())
      setPayload(p)
      setEditingPrompt(false)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }, [videoId, prompt])

  const resetPrompt = () => {
    if (config) setPrompt(config.default_prompt)
  }

  const promptChanged = payload && prompt.trim() !== payload.prompt.trim()
  const hasSuggestions = (payload?.suggestions?.length ?? 0) > 0

  return (
    <div className="card col" style={{ gap: 10 }}>
      <div className="row">
        <b>AI clip suggestions</b>
        {config?.active && (
          <span className="dim small" title={config.active.base_url ?? ''}>
            {config.active.provider === 'openai' ? 'local' : 'anthropic'} · {config.active.model}
          </span>
        )}
        <div className="spacer" />
        <button
          className="small"
          onClick={() => setEditingPrompt((v) => !v)}
          title="show/hide the analysis prompt"
        >
          {editingPrompt ? 'hide prompt' : 'prompt'}
        </button>
      </div>

      {!indexReady && (
        <div className="small muted">index the video first; then re-run analysis here</div>
      )}

      {config && !config.enabled && (
        <div className="small" style={{ color: 'var(--yellow)' }}>
          AI suggestions disabled — set <code>OPENAI_BASE_URL</code> +{' '}
          <code>MODEL_NAME</code> (for a local model) or{' '}
          <code>ANTHROPIC_API_KEY</code> on the server, then restart.
        </div>
      )}

      {editingPrompt && (
        <div className="col" style={{ gap: 6 }}>
          <textarea
            rows={8}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="describe the kinds of clips you want suggested"
            style={{ resize: 'vertical' }}
          />
          <div className="row small">
            <button className="small" onClick={resetPrompt} disabled={!config}>
              reset to default
            </button>
            <div className="spacer" />
            {payload?.usage && (
              <span className="dim small" title="last run">
                in {payload.usage.input_tokens ?? '?'} · out {payload.usage.output_tokens ?? '?'}
                {payload.usage.cache_read_input_tokens
                  ? ` · cached ${payload.usage.cache_read_input_tokens}` : ''}
              </span>
            )}
          </div>
        </div>
      )}

      <div className="row">
        <button
          className="primary"
          disabled={busy || !indexReady || !config?.enabled || !prompt.trim()}
          onClick={regenerate}
          title={hasSuggestions
            ? (promptChanged ? 're-run with the edited prompt' : 'regenerate suggestions')
            : 'generate suggestions for this video'}
        >
          {busy
            ? 'analyzing…'
            : (hasSuggestions ? (promptChanged ? '↻ re-run' : '↻ regenerate') : '✨ analyze')}
        </button>
        {payload?.generated_at && (
          <span className="dim small">
            last run {fmtAge(payload.generated_at)}
            {payload.elapsed_seconds ? ` · ${payload.elapsed_seconds.toFixed(1)}s` : ''}
          </span>
        )}
      </div>

      {error && <div className="small" style={{ color: 'var(--red)' }}>{error}</div>}

      {hasSuggestions && (
        <ul className="event-list" style={{ maxHeight: 360 }}>
          {payload!.suggestions.map((s, i) => (
            <li key={i}>
              <div className="row" style={{ gap: 6 }}>
                <span
                  className="ts"
                  onClick={() => onJump(s.start, s.end)}
                  style={{ cursor: 'pointer' }}
                >
                  {fmtTime(s.start)} → {fmtTime(s.end)}
                </span>
                <span className="dim small">({(s.end - s.start).toFixed(1)}s)</span>
                <div className="spacer" />
                <button className="small" onClick={() => onEdit(s.start, s.end)}>
                  load into editor
                </button>
              </div>
              <span className="desc"><b>{s.title}</b></span>
              {s.why && <span className="meta">{s.why}</span>}
            </li>
          ))}
        </ul>
      )}

      {!hasSuggestions && config?.enabled && indexReady && !busy && (
        <div className="small muted">
          no suggestions yet — click <b>analyze</b> to generate them from the
          current captions + transcript.
        </div>
      )}
    </div>
  )
}

function fmtAge(ts: number): string {
  const sec = (Date.now() / 1000) - ts
  if (sec < 60) return 'just now'
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`
  return `${Math.floor(sec / 86400)}d ago`
}
