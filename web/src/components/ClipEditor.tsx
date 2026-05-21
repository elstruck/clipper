import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fmtTime, mediaUrl } from '../api'

interface ThumbnailMeta {
  url: string
  count: number
  interval: number
  width: number
  height: number
  thumb_width: number
  thumb_height: number
}

interface Props {
  videoId: string
  duration: number
  initialStart: number
  initialEnd: number
  videoEl: React.RefObject<HTMLVideoElement | null>
  onSave: (start: number, end: number, name?: string, reencode?: boolean) => Promise<void>
  onClose: () => void
}

type DragMode = null | 'in' | 'out' | 'playhead'

export default function ClipEditor(props: Props) {
  const { videoId, duration, initialStart, initialEnd, videoEl, onSave, onClose } = props

  const [inT, setInT] = useState(Math.max(0, initialStart))
  const [outT, setOutT] = useState(Math.min(duration, Math.max(initialStart + 0.1, initialEnd)))
  const [playhead, setPlayhead] = useState(initialStart)
  const [looping, setLooping] = useState(true)
  const [name, setName] = useState('')
  const [reencode, setReencode] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [meta, setMeta] = useState<ThumbnailMeta | null>(null)
  const [sprite, setSprite] = useState<HTMLImageElement | null>(null)

  const canvasRef = useRef<HTMLCanvasElement>(null)
  const dragRef = useRef<DragMode>(null)

  // Fetch thumbnail metadata (lazily generates sprite if needed) + load image.
  useEffect(() => {
    let cancelled = false
    setMeta(null); setSprite(null); setError(null)
    const token = localStorage.getItem('clipper.token') ?? ''
    fetch(`/api/videos/${videoId}/thumbnails`, token ? { headers: { 'X-API-Token': token } } : undefined)
      .then(async (r) => {
        if (!r.ok) throw new Error(`thumbnails: ${r.status} ${await r.text()}`)
        return r.json() as Promise<ThumbnailMeta>
      })
      .then((m) => {
        if (cancelled) return
        setMeta(m)
        const img = new Image()
        img.src = mediaUrl(m.url)
        img.onload = () => { if (!cancelled) setSprite(img) }
        img.onerror = () => { if (!cancelled) setError('failed to load thumbnail sprite') }
      })
      .catch((e) => { if (!cancelled) setError(String(e)) })
    return () => { cancelled = true }
  }, [videoId])

  // Sync playhead state from the <video> element so we can render it.
  useEffect(() => {
    const v = videoEl.current
    if (!v) return
    const onTime = () => {
      setPlayhead(v.currentTime)
      if (looping && !v.paused && v.currentTime >= outT) v.currentTime = inT
    }
    v.addEventListener('timeupdate', onTime)
    return () => v.removeEventListener('timeupdate', onTime)
  }, [videoEl, looping, outT, inT])

  // Seek the player whenever in moves, so the preview starts at the new in.
  const playPreview = useCallback(() => {
    const v = videoEl.current
    if (!v) return
    v.currentTime = inT
    v.play().catch(() => {})
  }, [videoEl, inT])

  const pause = useCallback(() => { videoEl.current?.pause() }, [videoEl])
  const isPlaying = useCallback(() => !!videoEl.current && !videoEl.current.paused, [videoEl])

  // Drawing.
  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    const cssW = canvas.clientWidth
    const cssH = canvas.clientHeight
    if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
      canvas.width = Math.round(cssW * dpr)
      canvas.height = Math.round(cssH * dpr)
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, cssW, cssH)

    const RULER_H = 18
    const thumbY = 0
    // Fill the available height — the sprite is 80 thumbs at 12800px wide, so
    // preserving aspect would squish the strip to ~5px. Stretch vertically;
    // a slightly distorted nav strip is way better than an invisible one.
    const thumbH = Math.max(40, cssH - RULER_H - 4)
    const rulerY = thumbH + 4

    // Background
    ctx.fillStyle = '#0a0d12'
    ctx.fillRect(0, 0, cssW, cssH)

    // Thumbnail strip
    if (sprite && meta) {
      try {
        ctx.imageSmoothingQuality = 'high'
        ctx.drawImage(sprite, 0, 0, meta.width, meta.height, 0, thumbY, cssW, thumbH)
      } catch { /* sprite not ready */ }
    } else {
      ctx.fillStyle = '#1c2230'
      ctx.fillRect(0, thumbY, cssW, thumbH)
      ctx.fillStyle = '#8a93a6'
      ctx.font = '12px ui-sans-serif, system-ui'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(error ? error : 'generating thumbnails…', cssW / 2, thumbY + thumbH / 2)
    }

    const xIn = (inT / duration) * cssW
    const xOut = (outT / duration) * cssW

    // Darken regions outside the selection (vs. tinting the selection itself —
    // it's easier to read the actual frames inside the in/out range when we
    // dim the OUTSIDE rather than blue-cast everything inside it).
    ctx.fillStyle = 'rgba(8, 10, 14, 0.72)'
    if (xIn > 0) ctx.fillRect(0, thumbY, xIn, thumbH)
    if (xOut < cssW) ctx.fillRect(xOut, thumbY, cssW - xOut, thumbH)

    // Selection bracket — thin top/bottom + brighter border, no fill so the
    // thumbnails stay readable.
    ctx.strokeStyle = '#8ebdff'
    ctx.lineWidth = 2
    ctx.strokeRect(xIn, thumbY + 1, Math.max(0, xOut - xIn), thumbH - 2)

    // Handles — chunky and high-contrast.
    const drawHandle = (x: number, label: string, side: 'left' | 'right') => {
      const w = 12
      const handleX = side === 'left' ? x - w : x
      // outer glow
      ctx.fillStyle = '#8ebdff'
      ctx.fillRect(handleX, thumbY, w, thumbH)
      // inner darker stripe so the label reads
      ctx.fillStyle = '#3a6ec7'
      ctx.fillRect(handleX + (side === 'left' ? 1 : 0), thumbY + 1, w - 1, thumbH - 2)
      // label
      ctx.fillStyle = '#fff'
      ctx.font = 'bold 11px ui-sans-serif, system-ui'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(label, handleX + w / 2, thumbY + thumbH / 2)
      // grip dots
      ctx.fillStyle = 'rgba(255,255,255,0.5)'
      for (let i = -1; i <= 1; i++) {
        ctx.fillRect(handleX + w / 2 - 1, thumbY + thumbH / 2 + i * 8 - 0.5, 2, 1)
      }
    }
    drawHandle(xIn, 'IN', 'right')   // grip hangs to the right of the in line
    drawHandle(xOut, 'OUT', 'left')  // grip hangs to the left of the out line

    // Playhead
    const xP = (playhead / duration) * cssW
    ctx.strokeStyle = '#ff7a7a'
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(xP, thumbY); ctx.lineTo(xP, thumbY + thumbH)
    ctx.stroke()
    // playhead caret
    ctx.fillStyle = '#ff7a7a'
    ctx.beginPath()
    ctx.moveTo(xP - 5, thumbY)
    ctx.lineTo(xP + 5, thumbY)
    ctx.lineTo(xP, thumbY + 6)
    ctx.closePath()
    ctx.fill()

    // Ruler background + ticks
    ctx.fillStyle = '#16191f'
    ctx.fillRect(0, rulerY, cssW, RULER_H)
    ctx.fillStyle = '#a3acbd'
    ctx.font = '10px ui-monospace, monospace'
    ctx.textBaseline = 'middle'
    const tickPx = 110
    const ticks = Math.max(2, Math.floor(cssW / tickPx))
    for (let i = 0; i <= ticks; i++) {
      const x = (i / ticks) * cssW
      const t = (i / ticks) * duration
      // small tick mark
      ctx.fillRect(x, rulerY, 1, 4)
      ctx.textAlign = i === 0 ? 'left' : (i === ticks ? 'right' : 'center')
      const padX = i === 0 ? 4 : (i === ticks ? -4 : 0)
      ctx.fillText(fmtTime(t), x + padX, rulerY + RULER_H / 2 + 2)
    }
  }, [sprite, meta, duration, inT, outT, playhead, error])

  // Redraw whenever inputs change.
  useEffect(() => {
    let raf = 0
    const loop = () => { draw(); raf = requestAnimationFrame(loop) }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [draw])

  // Pointer interactions on the canvas.
  const xToTime = useCallback((x: number) => {
    const canvas = canvasRef.current
    if (!canvas) return 0
    const w = canvas.clientWidth
    return Math.max(0, Math.min(duration, (x / w) * duration))
  }, [duration])

  const hitTest = useCallback((x: number) => {
    const canvas = canvasRef.current
    if (!canvas) return null
    const w = canvas.clientWidth
    const xIn = (inT / duration) * w
    const xOut = (outT / duration) * w
    if (Math.abs(x - xIn) <= 8) return 'in'
    if (Math.abs(x - xOut) <= 8) return 'out'
    return 'playhead'
  }, [inT, outT, duration])

  const onMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    const mode = hitTest(x)
    dragRef.current = mode
    const t = xToTime(x)
    if (mode === 'in') setInT(Math.min(t, outT - 0.05))
    else if (mode === 'out') setOutT(Math.max(t, inT + 0.05))
    else { setPlayhead(t); if (videoEl.current) videoEl.current.currentTime = t }
  }

  const onMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!dragRef.current) return
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    const t = xToTime(x)
    if (dragRef.current === 'in') setInT(Math.min(t, outT - 0.05))
    else if (dragRef.current === 'out') setOutT(Math.max(t, inT + 0.05))
    else { setPlayhead(t); if (videoEl.current) videoEl.current.currentTime = t }
  }

  const onMouseUp = () => { dragRef.current = null }

  // Keyboard.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      const step = e.shiftKey ? 1.0 : 0.1
      if (e.key === 'ArrowLeft') {
        e.preventDefault()
        const v = videoEl.current
        if (v) { v.currentTime = Math.max(0, v.currentTime - step) }
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        const v = videoEl.current
        if (v) { v.currentTime = Math.min(duration, v.currentTime + step) }
      } else if (e.key === 'i' || e.key === 'I') {
        const v = videoEl.current
        if (v) setInT(Math.min(v.currentTime, outT - 0.05))
      } else if (e.key === 'o' || e.key === 'O') {
        const v = videoEl.current
        if (v) setOutT(Math.max(v.currentTime, inT + 0.05))
      } else if (e.key === ' ') {
        e.preventDefault()
        if (isPlaying()) pause(); else playPreview()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [videoEl, duration, inT, outT, isPlaying, pause, playPreview])

  const dur = outT - inT
  const canSave = dur > 0.05 && !saving

  const onSubmit = async () => {
    if (!canSave) return
    setSaving(true); setError(null)
    try {
      await onSave(inT, outT, name.trim() || undefined, reencode)
      onClose()
    } catch (e) {
      setError(String(e))
    } finally { setSaving(false) }
  }

  const help = useMemo(() => [
    '← / → : nudge playhead 0.1 s (Shift = 1 s)',
    'I / O : set in / out to playhead',
    'Space : play / pause preview',
    'drag handles or click strip to scrub',
  ], [])

  return (
    <div className="card col" style={{ gap: 12 }}>
      <div className="row">
        <b>clip editor</b>
        <span className="muted small">{fmtTime(inT)} → {fmtTime(outT)} · {dur.toFixed(2)}s</span>
        <div className="spacer" />
        <button onClick={onClose}>close</button>
      </div>

      <canvas
        ref={canvasRef}
        style={{
          width: '100%', height: 160, borderRadius: 6,
          cursor: dragRef.current ? 'grabbing' : 'pointer',
          border: '1px solid var(--border)',
        }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      />

      <div className="row">
        <button onClick={() => (isPlaying() ? pause() : playPreview())}>
          {isPlaying() ? 'pause' : '▶ preview'}
        </button>
        <label className="row small" style={{ gap: 6 }}>
          <input type="checkbox" checked={looping} onChange={(e) => setLooping(e.target.checked)} style={{ width: 'auto' }} />
          loop in / out
        </label>
        <label className="row small" style={{ gap: 6 }}>
          <input type="checkbox" checked={reencode} onChange={(e) => setReencode(e.target.checked)} style={{ width: 'auto' }} />
          frame-accurate (slower)
        </label>
        <div className="spacer" />
      </div>

      <div className="row" style={{ gap: 8 }}>
        <input placeholder="clip name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="primary" disabled={!canSave} onClick={onSubmit}>
          {saving ? 'saving…' : 'save clip'}
        </button>
      </div>

      {error && <div className="small" style={{ color: 'var(--red)' }}>{error}</div>}

      <div className="small dim col" style={{ gap: 2 }}>
        {help.map((h, i) => <span key={i}>{h}</span>)}
      </div>
    </div>
  )
}
