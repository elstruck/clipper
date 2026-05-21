import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  api, fmtTime, mediaUrl, type Clip, type Job, type SearchHit, type Video, type VideoIndex,
} from '../api'
import ClipEditor from '../components/ClipEditor'

type Mode = 'text' | 'fanout'
type EditorState = { start: number; end: number } | null

export default function VideoRoute() {
  const { id = '' } = useParams<{ id: string }>()
  const [video, setVideo] = useState<Video | null>(null)
  const [index, setIndex] = useState<VideoIndex | null>(null)
  const [clips, setClips] = useState<Clip[]>([])
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editor, setEditor] = useState<EditorState>(null)
  const videoRef = useRef<HTMLVideoElement>(null)

  const refreshVideo = useCallback(async () => {
    try { setVideo(await api.getVideo(id)) } catch (e) { setError(String(e)) }
  }, [id])

  const refreshIndex = useCallback(async () => {
    try { setIndex(await api.getEvents(id)) } catch { /* no index yet */ }
  }, [id])

  const refreshClips = useCallback(async () => {
    try { setClips(await api.listClips(id)) } catch (e) { setError(String(e)) }
  }, [id])

  useEffect(() => { void refreshVideo(); void refreshIndex(); void refreshClips() }, [
    refreshVideo, refreshIndex, refreshClips,
  ])

  useEffect(() => {
    if (video?.status !== 'indexing') { setJob(null); return }
    let unsub: (() => void) | null = null
    let cancelled = false
    void api.listJobs(id).then((jobs) => {
      if (cancelled) return
      const latest = jobs.find((j) => j.type === 'index')
      if (!latest) return
      setJob(latest)
      if (latest.status === 'done' || latest.status === 'failed') {
        void refreshVideo()
        void refreshIndex()
        return
      }
      unsub = api.subscribeJob(
        latest.id,
        (j) => { if (!cancelled) setJob(j) },
        () => { if (!cancelled) { void refreshVideo(); void refreshIndex() } },
      )
    })
    return () => { cancelled = true; unsub?.() }
  }, [id, video?.status, refreshVideo, refreshIndex])

  const startIndex = async () => {
    try { await api.startIndex(id); void refreshVideo() }
    catch (e) { alert(`index failed: ${e}`) }
  }

  const seekAndPlay = useCallback((start: number, end?: number) => {
    const v = videoRef.current
    if (!v) return
    v.currentTime = start
    v.play().catch(() => {})
    if (end !== undefined) {
      const stopAt = end
      const handler = () => {
        if (v.currentTime >= stopAt) {
          v.pause()
          v.removeEventListener('timeupdate', handler)
        }
      }
      v.addEventListener('timeupdate', handler)
    }
  }, [])

  const openEditor = useCallback((start: number, end: number) => {
    setEditor({ start, end })
    // Scroll editor into view after it renders.
    setTimeout(() => {
      document.getElementById('clip-editor')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }, 50)
  }, [])

  const saveClip = async (start: number, end: number, name?: string, reencode = false) => {
    await api.createClip(id, start, end, name, reencode)
    await refreshClips()
  }

  if (error) return <div className="card" style={{ color: 'var(--red)' }}>{error}</div>
  if (!video) return <div className="muted">loading…</div>

  return (
    <div className="video-layout">
      <div className="col">
        <div className="player-wrap">
          <video ref={videoRef} src={mediaUrl(`/media/videos/${id}`)} controls preload="metadata" />
        </div>
        <div className="row">
          <div className="col" style={{ gap: 4 }}>
            <div className="mono">{video.filename}</div>
            <div className="small muted">
              {video.duration ? fmtTime(video.duration) : '—'} · <span className={`status ${video.status}`}>{video.status}</span>
            </div>
          </div>
          <div className="spacer" />
          {video.status === 'uploaded' && <button className="primary" onClick={startIndex}>start indexing</button>}
          {video.status === 'failed' && <button onClick={startIndex}>retry indexing</button>}
          <button onClick={() => {
            const v = videoRef.current
            if (!v) return
            const t = v.currentTime
            const dur = video.duration ?? t + 5
            openEditor(Math.max(0, t - 2), Math.min(dur, t + 5))
          }}>
            + new clip
          </button>
        </div>
        {job && job.status === 'running' && (
          <div className="card col" style={{ gap: 6 }}>
            <div className="row">
              <span className={`status ${job.status}`}>{job.status}</span>
              <span className="small muted">{job.message ?? ''}</span>
            </div>
            <div className="progress">
              <div style={{
                width: `${job.progress_total ? (job.progress_current / job.progress_total) * 100 : 5}%`,
              }} />
            </div>
            <div className="small muted">
              chunk {job.progress_current} of {job.progress_total}
            </div>
          </div>
        )}

        {editor && video.duration && (
          <div id="clip-editor">
            <ClipEditor
              videoId={id}
              duration={video.duration}
              initialStart={editor.start}
              initialEnd={editor.end}
              videoEl={videoRef}
              onSave={saveClip}
              onClose={() => setEditor(null)}
            />
          </div>
        )}

        {index && (
          <EventsTimeline
            index={index}
            onJump={seekAndPlay}
            onEdit={openEditor}
            player={videoRef}
          />
        )}
      </div>

      <div className="col">
        <SearchPanel videoId={id} indexReady={!!index} onJump={seekAndPlay} onEdit={openEditor} />
        <ClipsPanel
          clips={clips}
          onJump={(c) => seekAndPlay(c.start, c.end)}
          onEdit={(c) => openEditor(c.start, c.end)}
          onDelete={async (clipId) => {
            if (!confirm('Delete this clip?')) return
            try { await api.deleteClip(clipId); void refreshClips() }
            catch (e) { alert(`delete failed: ${e}`) }
          }}
        />
      </div>
    </div>
  )
}

function EventsTimeline({
  index, onJump, onEdit, player,
}: {
  index: VideoIndex
  onJump: (s: number, e?: number) => void
  onEdit: (s: number, e: number) => void
  player: React.RefObject<HTMLVideoElement | null>
}) {
  const [t, setT] = useState(0)
  useEffect(() => {
    const v = player.current
    if (!v) return
    const handler = () => setT(v.currentTime)
    v.addEventListener('timeupdate', handler)
    return () => v.removeEventListener('timeupdate', handler)
  }, [player])

  return (
    <div className="card col">
      <div className="row">
        <b>events</b>
        <span className="small muted">{index.events.length} from {index.chunks.length} chunks</span>
      </div>
      <ul className="event-list">
        {index.events.map((ev, i) => {
          const active = t >= ev.start && t < ev.end
          return (
            <li key={i} className={active ? 'active' : ''}>
              <div className="row" style={{ gap: 6 }}>
                <span className="ts" onClick={() => onJump(ev.start, ev.end)} style={{ cursor: 'pointer' }}>
                  {fmtTime(ev.start)} → {fmtTime(ev.end)}
                </span>
                <div className="spacer" />
                <button
                  className="small"
                  onClick={(e) => { e.stopPropagation(); onEdit(ev.start, ev.end) }}
                >
                  edit clip
                </button>
              </div>
              <span className="desc">{ev.description}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function SearchPanel({
  videoId, indexReady, onJump, onEdit,
}: {
  videoId: string
  indexReady: boolean
  onJump: (s: number, e?: number) => void
  onEdit: (s: number, e: number) => void
}) {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<Mode>('text')
  const [hits, setHits] = useState<SearchHit[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [minScore, setMinScore] = useState(0.3)
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())

  const submit = async () => {
    if (!query.trim()) return
    setBusy(true); setErr(null); setDismissed(new Set())
    try {
      const r = await api.search(videoId, query.trim(), mode === 'fanout')
      setHits(r.hits)
    } catch (e) {
      setErr(String(e))
    } finally { setBusy(false) }
  }

  const placeholder = useMemo(
    () => mode === 'text'
      ? 'search captions (e.g. "color bars")'
      : 'describe a moment (e.g. "person waves at the camera")',
    [mode],
  )

  const visible = useMemo(() => {
    if (!hits) return null
    return hits.filter((h) => {
      const key = `${h.chunk_index}:${h.start}:${h.end}`
      if (dismissed.has(key)) return false
      if (mode === 'fanout' && h.score < minScore) return false
      return true
    })
  }, [hits, mode, minScore, dismissed])

  const dismiss = (h: SearchHit) => {
    const next = new Set(dismissed)
    next.add(`${h.chunk_index}:${h.start}:${h.end}`)
    setDismissed(next)
  }

  return (
    <div className="card col">
      <div className="tabs">
        <button className={mode === 'text' ? 'active' : ''} onClick={() => setMode('text')}>text</button>
        <button className={mode === 'fanout' ? 'active' : ''} onClick={() => setMode('fanout')}>find</button>
      </div>
      <div className="search-row">
        <input
          placeholder={placeholder}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void submit() }}
          disabled={!indexReady}
        />
        <button className="primary" disabled={!indexReady || busy || !query.trim()} onClick={submit}>
          {busy ? '…' : 'go'}
        </button>
      </div>
      {!indexReady && <div className="small muted">index the video to enable search</div>}
      {mode === 'fanout' && indexReady && (
        <div className="small muted">
          runs find() on every chunk — slower, but works around caption vocabulary mismatch.
          Confidence drops for spans that cover most of their chunk (likely false positives).
        </div>
      )}
      {mode === 'fanout' && hits && hits.length > 0 && (
        <div className="row small" style={{ gap: 8 }}>
          <span className="muted" style={{ minWidth: 80 }}>min confidence</span>
          <input
            type="range" min={0} max={1} step={0.05}
            value={minScore}
            onChange={(e) => setMinScore(parseFloat(e.target.value))}
            style={{ width: 120 }}
          />
          <span className="mono" style={{ minWidth: 32 }}>{minScore.toFixed(2)}</span>
          <span className="muted">·</span>
          <span className="muted">
            {visible?.length ?? 0} of {hits.length} shown
          </span>
        </div>
      )}
      {err && <div className="small" style={{ color: 'var(--red)' }}>{err}</div>}
      {visible && (
        <ul className="event-list">
          {visible.length === 0 ? <li className="muted">no matches above threshold</li> : visible.map((h, i) => (
            <li key={i}>
              <div className="row" style={{ gap: 6 }}>
                <span className="ts" onClick={() => onJump(h.start, h.end)} style={{ cursor: 'pointer' }}>
                  {fmtTime(h.start)} → {fmtTime(h.end)}
                </span>
                <div className="spacer" />
                <button className="small" onClick={() => onEdit(h.start, h.end)}>edit clip</button>
                <button className="small danger" title="hide this hit" onClick={() => dismiss(h)}>×</button>
              </div>
              <span className="desc">{h.description}</span>
              <span className="meta">
                chunk {h.chunk_index} · {(h.end - h.start).toFixed(1)}s
                {mode === 'fanout' && ` · conf ${h.score.toFixed(2)}`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function ClipsPanel({
  clips, onJump, onEdit, onDelete,
}: {
  clips: Clip[]
  onJump: (c: Clip) => void
  onEdit: (c: Clip) => void
  onDelete: (id: string) => void
}) {
  return (
    <div className="card col">
      <div className="row">
        <b>clips</b>
        <span className="small muted">{clips.length}</span>
      </div>
      {clips.length === 0 ? (
        <div className="small muted">no clips yet — open one of the events or hits with “edit clip”</div>
      ) : (
        <ul className="clip-list">
          {clips.map((c) => (
            <li key={c.id}>
              <div className="row" style={{ gap: 6 }}>
                <span className="ts mono small" onClick={() => onJump(c)} style={{ cursor: 'pointer' }}>
                  {fmtTime(c.start)} → {fmtTime(c.end)}
                </span>
                <div className="spacer" />
                <button className="small" onClick={() => onEdit(c)}>edit</button>
                <a href={mediaUrl(`/media/clips/${c.id}`)} download className="small">↓</a>
                <button className="danger small" onClick={() => onDelete(c.id)}>×</button>
              </div>
              {c.name && <div className="desc">{c.name}</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
