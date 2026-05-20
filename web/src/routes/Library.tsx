import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtBytes, fmtTime, type Video } from '../api'

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
  const [uploading, setUploading] = useState<{ name: string; frac: number } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleFile = useCallback(async (file: File) => {
    setUploading({ name: file.name, frac: 0 })
    try {
      await api.uploadVideo(file, (frac) => setUploading({ name: file.name, frac }))
      onUploaded()
    } catch (e) {
      alert(`upload failed: ${e}`)
    } finally {
      setUploading(null)
    }
  }, [onUploaded])

  return (
    <div
      className={`uploader ${dragging ? 'drag' : ''}`}
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        const f = e.dataTransfer.files?.[0]
        if (f) void handleFile(f)
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
        <div className="col" style={{ gap: 8 }}>
          <div>uploading <span className="mono">{uploading.name}</span></div>
          <div className="progress"><div style={{ width: `${uploading.frac * 100}%` }} /></div>
          <div className="small muted">{Math.round(uploading.frac * 100)}%</div>
        </div>
      ) : (
        <div className="col" style={{ gap: 4 }}>
          <div><b>drop a video here</b>, or click to choose</div>
          <div className="small muted">mp4 / mov / mkv / webm / m4v / avi</div>
        </div>
      )}
    </div>
  )
}

function VideoCard({ v, onChange }: { v: Video; onChange: () => void }) {
  const [starting, setStarting] = useState(false)
  const onStart = async () => {
    setStarting(true)
    try { await api.startIndex(v.id); onChange() }
    catch (e) { alert(`start failed: ${e}`) }
    finally { setStarting(false) }
  }
  const onDelete = async () => {
    if (!confirm(`Delete ${v.filename}? This also removes the index and uploaded file.`)) return
    try { await api.deleteVideo(v.id); onChange() } catch (e) { alert(`delete failed: ${e}`) }
  }

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
