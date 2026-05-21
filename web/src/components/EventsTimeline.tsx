import { useEffect, useMemo, useState } from 'react'
import { fmtTime, type VideoIndex } from '../api'
import { groupCaptions, groupTranscript } from '../grouping'

type Track = 'captions' | 'transcript'
type ViewMode = 'segments' | 'reading'

interface Props {
  index: VideoIndex
  onJump: (start: number, end?: number) => void
  onEdit: (start: number, end: number) => void
  player: React.RefObject<HTMLVideoElement | null>
}

export default function EventsTimeline({ index, onJump, onEdit, player }: Props) {
  const [t, setT] = useState(0)
  const [track, setTrack] = useState<Track>('captions')
  const [mode, setMode] = useState<ViewMode>('reading')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [lastClicked, setLastClicked] = useState<number | null>(null)

  const transcript = index.transcript
  const segs = transcript?.segments ?? []
  const hasTranscript = segs.length > 0

  // Reset selection when track or mode changes (index space changes).
  useEffect(() => { setSelected(new Set()); setLastClicked(null) }, [track, mode])

  // Track playhead for active-row highlighting.
  useEffect(() => {
    const v = player.current
    if (!v) return
    const handler = () => setT(v.currentTime)
    v.addEventListener('timeupdate', handler)
    return () => v.removeEventListener('timeupdate', handler)
  }, [player])

  const paragraphs = useMemo(() => groupTranscript(segs), [segs])
  const scenes = useMemo(() => groupCaptions(index.events), [index.events])

  const total = track === 'captions' ? index.events.length : segs.length
  const groupedCount = track === 'captions' ? scenes.length : paragraphs.length

  const toggleSelect = (i: number, e: React.MouseEvent | React.ChangeEvent) => {
    const shift = (e as React.MouseEvent).shiftKey
    const next = new Set(selected)
    if (shift && lastClicked !== null) {
      const [from, to] = [lastClicked, i].sort((a, b) => a - b)
      const target = !next.has(i)
      for (let j = from; j <= to; j++) {
        if (target) next.add(j); else next.delete(j)
      }
    } else {
      if (next.has(i)) next.delete(i); else next.add(i)
    }
    setSelected(next)
    setLastClicked(i)
  }

  // Compute the span of the current selection — for the action bar.
  const selectionSpan = useMemo(() => {
    if (selected.size === 0) return null
    let start = Infinity
    let end = -Infinity
    if (track === 'captions') {
      for (const i of selected) {
        const ev = index.events[i]
        if (!ev) continue
        start = Math.min(start, ev.start)
        end = Math.max(end, ev.end)
      }
    } else {
      for (const i of selected) {
        const s = segs[i]
        if (!s) continue
        start = Math.min(start, s.start)
        end = Math.max(end, s.end)
      }
    }
    if (!isFinite(start) || !isFinite(end)) return null
    return { start, end }
  }, [selected, track, index.events, segs])

  return (
    <div className="card col" style={{ position: 'relative' }}>
      <div className="tabs">
        <button
          className={track === 'captions' ? 'active' : ''}
          onClick={() => setTrack('captions')}
        >
          captions <span className="dim small">({index.events.length})</span>
        </button>
        <button
          className={track === 'transcript' ? 'active' : ''}
          onClick={() => setTrack('transcript')}
          disabled={!hasTranscript}
          title={hasTranscript ? undefined : 'no transcript — video may have no audio'}
        >
          transcript <span className="dim small">({segs.length})</span>
        </button>
        <div className="spacer" />
        <div className="seg-toggle small">
          <button
            className={mode === 'reading' ? 'active' : ''}
            onClick={() => setMode('reading')}
            title="grouped, scrollable paragraphs"
          >
            reading
          </button>
          <button
            className={mode === 'segments' ? 'active' : ''}
            onClick={() => setMode('segments')}
            title="each model output as its own row (multi-selectable)"
          >
            segments
          </button>
        </div>
      </div>

      {track === 'transcript' && transcript?.language && (
        <div className="small muted">
          {transcript.language} · {Math.round((transcript.language_probability ?? 0) * 100)}% conf
          {mode === 'reading' && ` · ${paragraphs.length} paragraphs (from ${total} segments)`}
          {mode === 'segments' && ` · ${total} segments`}
        </div>
      )}
      {track === 'captions' && mode === 'reading' && (
        <div className="small muted">{scenes.length} scenes (from {index.events.length} events)</div>
      )}

      {mode === 'reading' ? (
        <ReadingList
          track={track}
          paragraphs={paragraphs}
          scenes={scenes}
          playheadT={t}
          onJump={onJump}
          onEdit={onEdit}
          transcriptError={transcript?.error}
          hasAny={total > 0}
        />
      ) : (
        <SegmentList
          track={track}
          events={index.events}
          segs={segs}
          playheadT={t}
          onJump={onJump}
          onEdit={onEdit}
          selected={selected}
          toggleSelect={toggleSelect}
          transcriptError={transcript?.error}
        />
      )}

      {selectionSpan && (
        <SelectionBar
          count={selected.size}
          span={selectionSpan}
          onMakeClip={() => onEdit(selectionSpan.start, selectionSpan.end)}
          onClear={() => { setSelected(new Set()); setLastClicked(null) }}
        />
      )}

      {/* Tiny placeholder so we never use groupedCount unused */}
      <span style={{ display: 'none' }}>{groupedCount}</span>
    </div>
  )
}

function ReadingList(props: {
  track: Track
  paragraphs: ReturnType<typeof groupTranscript>
  scenes: ReturnType<typeof groupCaptions>
  playheadT: number
  onJump: (s: number, e?: number) => void
  onEdit: (s: number, e: number) => void
  transcriptError?: string
  hasAny: boolean
}) {
  const { track, paragraphs, scenes, playheadT, onJump, onEdit, transcriptError, hasAny } = props

  if (track === 'transcript') {
    if (transcriptError) {
      return <div className="small" style={{ color: 'var(--yellow)' }}>transcript failed: {transcriptError}</div>
    }
    if (!hasAny) {
      return <div className="muted">no spoken content detected</div>
    }
    return (
      <div className="reading-list">
        {paragraphs.map((p, i) => {
          const active = playheadT >= p.start && playheadT < p.end
          return (
            <div key={i} className={`para ${active ? 'active' : ''}`}>
              <div className="row">
                <button
                  className="small ts-pill"
                  onClick={() => onJump(p.start, p.end)}
                  title={`${(p.end - p.start).toFixed(1)}s · ${p.count} segments`}
                >
                  {fmtTime(p.start)} → {fmtTime(p.end)}
                </button>
                <div className="spacer" />
                <button className="small" onClick={() => onEdit(p.start, p.end)}>edit clip</button>
              </div>
              <p className="para-text">{p.text}</p>
            </div>
          )
        })}
      </div>
    )
  }

  if (scenes.length === 0) return <div className="muted">no caption scenes</div>

  return (
    <div className="reading-list">
      {scenes.map((s, i) => {
        const active = playheadT >= s.start && playheadT < s.end
        return (
          <div key={i} className={`para ${active ? 'active' : ''}`}>
            <div className="row">
              <button
                className="small ts-pill"
                onClick={() => onJump(s.start, s.end)}
                title={`${(s.end - s.start).toFixed(1)}s · ${s.count} caption events`}
              >
                {fmtTime(s.start)} → {fmtTime(s.end)}
              </button>
              <span className="dim small">scene · {s.count} events</span>
              <div className="spacer" />
              <button className="small" onClick={() => onEdit(s.start, s.end)}>edit clip</button>
            </div>
            <p className="para-text">{s.description}</p>
            {s.variations.length > 0 && (
              <details className="small dim variations">
                <summary>{s.variations.length} more variation{s.variations.length === 1 ? '' : 's'}</summary>
                <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
                  {s.variations.map((v, j) => <li key={j}>{v}</li>)}
                </ul>
              </details>
            )}
          </div>
        )
      })}
    </div>
  )
}

function SegmentList(props: {
  track: Track
  events: VideoIndex['events']
  segs: NonNullable<VideoIndex['transcript']>['segments']
  playheadT: number
  onJump: (s: number, e?: number) => void
  onEdit: (s: number, e: number) => void
  selected: Set<number>
  toggleSelect: (i: number, e: React.MouseEvent | React.ChangeEvent) => void
  transcriptError?: string
}) {
  const { track, events, segs, playheadT, onJump, onEdit, selected, toggleSelect, transcriptError } = props

  if (track === 'transcript' && transcriptError) {
    return <div className="small" style={{ color: 'var(--yellow)' }}>transcript failed: {transcriptError}</div>
  }
  if (track === 'transcript' && segs.length === 0) {
    return <div className="muted">no spoken content detected</div>
  }
  if (track === 'captions' && events.length === 0) {
    return <div className="muted">no caption events</div>
  }

  const rows = track === 'captions'
    ? events.map((ev, i) => ({ i, start: ev.start, end: ev.end, text: ev.description }))
    : segs.map((s, i) => ({ i, start: s.start, end: s.end, text: s.text }))

  return (
    <ul className="event-list">
      {rows.map((r) => {
        const active = playheadT >= r.start && playheadT < r.end
        const isSelected = selected.has(r.i)
        return (
          <li key={r.i} className={`${active ? 'active' : ''} ${isSelected ? 'selected' : ''}`}>
            <div className="row" style={{ gap: 6 }}>
              <input
                type="checkbox"
                checked={isSelected}
                onClick={(e) => toggleSelect(r.i, e)}
                onChange={() => { /* handled in onClick to capture shiftKey */ }}
                style={{ width: 'auto', margin: 0 }}
                title="select (Shift+click for range)"
              />
              <span className="ts" onClick={() => onJump(r.start, r.end)} style={{ cursor: 'pointer' }}>
                {fmtTime(r.start)} → {fmtTime(r.end)}
              </span>
              <div className="spacer" />
              <button className="small" onClick={(e) => { e.stopPropagation(); onEdit(r.start, r.end) }}>
                edit clip
              </button>
            </div>
            <span className="desc">{r.text}</span>
          </li>
        )
      })}
    </ul>
  )
}

function SelectionBar(props: {
  count: number
  span: { start: number; end: number }
  onMakeClip: () => void
  onClear: () => void
}) {
  const { count, span, onMakeClip, onClear } = props
  const dur = span.end - span.start
  return (
    <div className="selection-bar">
      <span className="small"><b>{count}</b> selected</span>
      <span className="muted small">·</span>
      <span className="mono small">{fmtTime(span.start)} → {fmtTime(span.end)}</span>
      <span className="muted small">({dur.toFixed(1)}s)</span>
      <div className="spacer" />
      <button className="primary small" onClick={onMakeClip}>make one clip</button>
      <button className="small" onClick={onClear}>clear</button>
    </div>
  )
}
