// Group raw caption events and transcript segments into longer, readable blocks.
// Pure functions — Video.tsx calls these client-side at render time.

import type { EventRow, TranscriptSegment } from './api'

export interface TranscriptParagraph {
  start: number
  end: number
  text: string
  count: number          // number of source segments folded in
  source_indices: number[]
}

export interface CaptionScene {
  start: number
  end: number
  description: string    // representative (longest) description in the group
  variations: string[]   // unique additional descriptions seen
  count: number
  source_indices: number[]
}

const TOKEN_RE = /[a-z0-9]+/g

function tokens(s: string): Set<string> {
  return new Set(s.toLowerCase().match(TOKEN_RE) ?? [])
}

function jaccard(a: Set<string>, b: Set<string>): number {
  if (a.size === 0 || b.size === 0) return 0
  let inter = 0
  for (const t of a) if (b.has(t)) inter++
  const uni = a.size + b.size - inter
  return uni === 0 ? 0 : inter / uni
}

/** Fold transcript segments into paragraphs separated by silence gaps. */
export function groupTranscript(
  segments: TranscriptSegment[],
  maxGap = 1.5,
  minLen = 4.0,
): TranscriptParagraph[] {
  if (segments.length === 0) return []
  const out: TranscriptParagraph[] = []
  let curIdx: number[] = []

  const flush = () => {
    if (curIdx.length === 0) return
    const first = segments[curIdx[0]]
    const last = segments[curIdx[curIdx.length - 1]]
    out.push({
      start: first.start,
      end: last.end,
      text: curIdx.map((i) => segments[i].text).join(' ').replace(/\s+/g, ' ').trim(),
      count: curIdx.length,
      source_indices: curIdx,
    })
    curIdx = []
  }

  for (let i = 0; i < segments.length; i++) {
    const s = segments[i]
    if (curIdx.length === 0) {
      curIdx.push(i)
      continue
    }
    const prev = segments[curIdx[curIdx.length - 1]]
    const gap = s.start - prev.end
    const span = s.end - segments[curIdx[0]].start
    if (gap > maxGap && span >= minLen) {
      flush()
    }
    curIdx.push(i)
  }
  flush()
  return out
}

/** Collapse runs of adjacent caption events with similar descriptions into scenes. */
export function groupCaptions(
  events: EventRow[],
  maxGap = 2.0,
  simThreshold = 0.35,
): CaptionScene[] {
  if (events.length === 0) return []
  const out: CaptionScene[] = []
  let curIdx: number[] = []
  let curTokens: Set<string> | null = null

  const flush = () => {
    if (curIdx.length === 0) return
    const evs = curIdx.map((i) => events[i])
    const first = evs[0]
    const last = evs[evs.length - 1]
    // Representative description: pick the longest one (most informative).
    const sortedByLen = [...evs].sort((a, b) => b.description.length - a.description.length)
    const seen = new Set<string>()
    const variations: string[] = []
    for (const ev of evs) {
      if (!seen.has(ev.description)) {
        seen.add(ev.description)
        variations.push(ev.description)
      }
    }
    out.push({
      start: first.start,
      end: last.end,
      description: sortedByLen[0].description,
      variations: variations.filter((d) => d !== sortedByLen[0].description),
      count: evs.length,
      source_indices: curIdx,
    })
    curIdx = []
    curTokens = null
  }

  for (let i = 0; i < events.length; i++) {
    const ev = events[i]
    const evTokens = tokens(ev.description)
    if (curIdx.length === 0) {
      curIdx.push(i)
      curTokens = evTokens
      continue
    }
    const prev = events[curIdx[curIdx.length - 1]]
    const gap = ev.start - prev.end
    const sim = curTokens ? jaccard(curTokens, evTokens) : 0
    if (gap > maxGap || sim < simThreshold) {
      flush()
      curIdx.push(i)
      curTokens = evTokens
    } else {
      curIdx.push(i)
      // Average the running token set (union-ish) so a drifting scene still groups.
      curTokens = new Set([...(curTokens ?? []), ...evTokens])
    }
  }
  flush()
  return out
}
