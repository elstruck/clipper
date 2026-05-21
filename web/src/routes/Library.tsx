import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtBytes, fmtTime, type Video } from '../api'

function fmtBps(bps: number): string {
  if (!isFinite(bps) || bps <= 0) return ''
  return `${fmtBytes(bps)}/s`
}

export default function Library() {
  const [videos, setVideos] = useState<Video[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setVideos(await api.listVideos())
      setError(null)
    } catch (e) {
      setError(String(e))
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  // Poll while anything is indexing.
  useEffect(() => {
    if (!videos?.some((v) => v.status === 'indexing')) return
    const t = setInterval(refresh, 2000)
    return () => clearInterval(t)
  }, [videos, refresh])

  return (
    <div className="col">
      <Uploader onUploaded={refresh} />
      {error && <div className="card" style={{ color: 'var(--red)' }}>{error}</div>}
      {videos === null ? (
        <div className="muted">loading…</div>
      ) : videos.length === 0 ? (
        <div className="muted">no videos yet — drop one above to get started</div>
      ) : (
        <div className="video-grid">
          {videos.map((v) => <VideoCard key={v.id} v={v} onChange={refresh} />)}
        </div>
      )}
    </div>
  )
}

function Uploader({ onUploaded }: { onUploaded: () => void }) {
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState<
    { name: string; size: number; frac: number; bps: number; error?: string } | null
  >(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const handleFile = useCallback(async (file: File) => {
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    setUploading({ name: file.name, size: file.size, frac: 0, bps: 0 })
    try {
      await api.uploadResumable(
        file,
        (frac, bps) => setUploading({ name: file.name, size: file.size, frac, bps }),
        ctrl.signal,
      )
      onUploaded()
      setUploading(null)
    } catch (e) {
      const msg = String(e)
      if (msg.includes('cancelled')) setUploading(null)
      else setUploading((u) => u ? { ...u, error: msg } : null)
    }
  }, [onUploaded])

  const cancel = () => { abortRef.current?.abort(); setUploading(null) }

  return (
    <div
      className={`uploader ${dragging ? 'drag' : ''}`}
      onClick={() => { if (!uploading) inputRef.current?.click() }}
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        const f = e.dataTransfer.files?.[0]
        if (f && !uploading) void handleFile(f)
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept="video/mp4,video/quicktime,video/x-matroska,video/webm,video/x-m4v,video/x-msvideo"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) void handleFile(f)
        }}
      />
      {uploading ? (
        <div className="col" style={{ gap: 8 }} onClick={(e) => e.stopPropagation()}>
          <div className="row">
            <div className="col" style={{ gap: 2, alignItems: 'flex-start' }}>
              <div>uploading <span className="mono">{uploading.name}</span></div>
              <div className="small muted">
                {fmtBytes(Math.round(uploading.size * uploading.frac))} of {fmtBytes(uploading.size)}
                {uploading.bps > 0 && ` · ${fmtBps(uploading.bps)}`}
              </div>
            </div>
            <div className="spacer" />
            <button onClick={cancel}>cancel</button>
          </div>
          <div className="progress"><div style={{ width: `${uploading.frac * 100}%` }} /></div>
          <div className="small muted">{Math.round(uploading.frac * 100)}%</div>
          {uploading.error && (
            <div className="small" style={{ color: 'var(--red)' }}>
              {uploading.error} — try again or pick a different file
            </div>
          )}
        </div>
      ) : (
        <div className="col" style={{ gap: 4 }}>
          <div><b>drop a video here</b>, or click to choose</div>
          <div className="small muted">mp4 / mov / mkv / webm / m4v / avi · chunked upload, retries on transient errors</div>
        </div>
      )}
    </div>
  )
}

function VideoCard({ v, onChange }: { v: Video; onChange: () => void }) {
  const [starting, setStarting] = useState(false)
  const [withCaptions, setWithCaptions] = useState(false)

  const onStart = async () => {
    setStarting(true)
    try { await api.startIndex(v.id, { caption: withCaptions, transcribe: true }); onChange() }
    catch (e) { alert(`start failed: ${e}`) }
    finally { setStarting(false) }
  }
  const onDelete = async () => {
    if (!confirm(`Delete ${v.filename}? This also removes the index and uploaded file.`)) return
    try { await api.deleteVideo(v.id); onChange() } catch (e) { alert(`delete failed: ${e}`) }
  }

  const showOptions = v.status === 'uploaded' || v.status === 'failed'

  return (
    <div className="card col" style={{ gap: 10 }}>
      <div className="row">
        <Link to={`/videos/${v.id}`} className="mono small">{v.filename}</Link>
        <div className="spacer" />
        <span className={`status ${v.status}`}>{v.status}</span>
      </div>
      <div className="row small muted">
        <span>{v.duration ? fmtTime(v.duration) : '—'}</span>
        <span>·</span>
        <span>{fmtBytes(v.size_bytes)}</span>
      </div>
      {showOptions && (
        <label
          className="row small muted"
          style={{ gap: 6, cursor: 'pointer' }}
          title="Also run Marlin visual captioning. Slower but useful for silent or visual-driven content."
        >
          <input
            type="checkbox"
            checked={withCaptions}
            onChange={(e) => setWithCaptions(e.target.checked)}
            style={{ width: 'auto', margin: 0 }}
          />
          + visual captions (Marlin, slower)
        </label>
      )}
      <div className="row" style={{ marginTop: 4 }}>
        <Link to={`/videos/${v.id}`}><button>open</button></Link>
        {v.status === 'uploaded' && (
          <button className="primary" onClick={onStart} disabled={starting}>
            {starting ? 'starting…' : 'index'}
          </button>
        )}
        {v.status === 'failed' && (
          <button onClick={onStart} disabled={starting}>retry</button>
        )}
        <div className="spacer" />
        <button className="danger" onClick={onDelete}>delete</button>
      </div>
    </div>
  )
}
