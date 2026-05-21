// Typed fetch wrappers for the clipper FastAPI backend.

// ---------- auth token (shared header for fetch, query param for embedded URLs) ----------

const TOKEN_KEY = 'clipper.token'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? ''
}

export function setToken(t: string) {
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

function withTokenHeader(init?: RequestInit): RequestInit {
  const token = getToken()
  if (!token) return init ?? {}
  const headers = new Headers(init?.headers)
  headers.set('X-API-Token', token)
  return { ...(init ?? {}), headers }
}

function authedFetch(url: string, init?: RequestInit): Promise<Response> {
  return fetch(url, withTokenHeader(init))
}

/** Append `?token=` to a URL meant for browser-embedded use (`<video src>`, `<a download>`). */
export function mediaUrl(path: string): string {
  const token = getToken()
  if (!token) return path
  const sep = path.includes('?') ? '&' : '?'
  return `${path}${sep}token=${encodeURIComponent(token)}`
}

export interface HealthInfo { status: string; auth_required: boolean }
export interface Stats {
  uploads_bytes: number
  clips_bytes: number
  thumbs_bytes: number
  total_bytes: number
  videos_count: number
  clips_count: number
  auth_enabled: boolean
}

export type VideoStatus = 'uploaded' | 'indexing' | 'indexed' | 'failed'

export interface Video {
  id: string
  filename: string
  path: string
  duration: number | null
  size_bytes: number
  status: VideoStatus
  uploaded_at: number
  indexed_at: number | null
}

export interface Job {
  id: string
  video_id: string
  type: 'index' | 'find_fanout'
  status: 'queued' | 'running' | 'done' | 'failed'
  progress_current: number
  progress_total: number
  message: string | null
  result: string | null
  error: string | null
  started_at: number | null
  finished_at: number | null
  created_at: number
}

export interface EventRow {
  start: number
  end: number
  description: string
  chunk_index: number
}

export interface ChunkInfo {
  index: number
  start: number
  end: number
}

export interface TranscriptSegment {
  start: number
  end: number
  text: string
  no_speech_prob?: number
}

export interface Transcript {
  language?: string
  language_probability?: number
  duration?: number
  segments: TranscriptSegment[]
  error?: string
}

export interface VideoIndex {
  video: string
  duration: number
  window: number
  overlap: number
  chunks: ChunkInfo[]
  scenes: string[]
  events: EventRow[]
  transcript: Transcript | null
}

export type HitSource = 'caption' | 'transcript' | 'find'

export interface SearchHit {
  start: number
  end: number
  description: string
  score: number
  chunk_index: number
  source: HitSource
}

export interface SearchResponse {
  hits: SearchHit[]
  mode: 'text' | 'fanout'
}

export interface ClipSuggestion {
  start: number
  end: number
  title: string
  why: string
}

export interface SuggestionsPayload {
  prompt: string
  suggestions: ClipSuggestion[]
  generated_at: number | null
  model: string | null
  elapsed_seconds?: number
  usage?: {
    input_tokens?: number
    output_tokens?: number
    cache_creation_input_tokens?: number
    cache_read_input_tokens?: number
  }
}

export interface AnalyzeConfig {
  enabled: boolean
  model: string
  default_prompt: string
}

export interface Clip {
  id: string
  video_id: string
  name: string | null
  start: number
  end: number
  path: string
  size_bytes: number
  reencoded: number
  created_at: number
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status} ${res.statusText}: ${detail}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => fetch('/health').then(json<HealthInfo>),

  stats: () => authedFetch('/api/stats').then(json<Stats>),

  listVideos: () => authedFetch('/api/videos').then(json<Video[]>),

  getVideo: (id: string) => authedFetch(`/api/videos/${id}`).then(json<Video>),

  /** One-shot multipart upload — fine for small files, but no retry/resume. */
  uploadVideo: (file: File, onProgress?: (frac: number) => void) =>
    new Promise<Video>((resolve, reject) => {
      const form = new FormData()
      form.append('file', file)
      const xhr = new XMLHttpRequest()
      xhr.open('POST', '/api/videos')
      const token = getToken()
      if (token) xhr.setRequestHeader('X-API-Token', token)
      if (onProgress) {
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) onProgress(e.loaded / e.total)
        }
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText))
        else reject(new Error(`${xhr.status} ${xhr.statusText}: ${xhr.responseText}`))
      }
      xhr.onerror = () => reject(new Error('upload network error'))
      xhr.send(form)
    }),

  /** Resumable chunked upload. Retries each chunk with backoff; if the server
   * has more bytes than the client thinks, resyncs the offset and continues. */
  uploadResumable: async (
    file: File,
    onProgress?: (frac: number, bps: number) => void,
    signal?: AbortSignal,
  ): Promise<Video> => {
    type Init = { upload_id: string; chunk_size: number; bytes_received: number; total_size: number }
    type ChunkResp = { bytes_received: number; total_size: number }

    const init = await authedFetch('/api/uploads', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name, size: file.size }),
    }).then((r) => json<Init>(r))

    let offset = init.bytes_received
    const chunkSize = init.chunk_size
    const total = file.size
    const startedAt = performance.now()

    const putChunk = async (chunkOffset: number, blob: Blob): Promise<ChunkResp> => {
      const ATTEMPTS = 4
      let lastErr: unknown
      for (let attempt = 1; attempt <= ATTEMPTS; attempt++) {
        if (signal?.aborted) throw new Error('cancelled')
        try {
          const resp = await authedFetch(
            `/api/uploads/${init.upload_id}?offset=${chunkOffset}`,
            { method: 'PUT', body: blob, signal },
          )
          if (resp.status === 409) {
            // Offset mismatch — resync from server.
            const cur = await authedFetch(`/api/uploads/${init.upload_id}`).then((r) => r.json())
            offset = cur.bytes_received
            return { bytes_received: cur.bytes_received, total_size: cur.total_size }
          }
          if (!resp.ok) {
            const detail = await resp.text().catch(() => resp.statusText)
            throw new Error(`${resp.status}: ${detail}`)
          }
          return resp.json() as Promise<ChunkResp>
        } catch (e) {
          lastErr = e
          if (signal?.aborted) throw e
          // exponential backoff: 0.5s, 1s, 2s
          await new Promise((res) => setTimeout(res, 500 * 2 ** (attempt - 1)))
        }
      }
      throw lastErr ?? new Error('chunk upload failed')
    }

    while (offset < total) {
      if (signal?.aborted) throw new Error('cancelled')
      const end = Math.min(offset + chunkSize, total)
      const blob = file.slice(offset, end)
      const r = await putChunk(offset, blob)
      offset = r.bytes_received
      if (onProgress) {
        const elapsed = (performance.now() - startedAt) / 1000
        const bps = elapsed > 0 ? offset / elapsed : 0
        onProgress(offset / total, bps)
      }
    }

    return authedFetch(`/api/uploads/${init.upload_id}/finalize`, { method: 'POST' })
      .then((r) => json<Video>(r))
  },

  deleteVideo: (id: string) =>
    authedFetch(`/api/videos/${id}`, { method: 'DELETE' }).then(json<{ deleted: string }>),

  startIndex: (id: string) =>
    authedFetch(`/api/videos/${id}/index`, { method: 'POST' }).then(
      json<{ job_id: string; video_id: string; status: string }>,
    ),

  getEvents: (id: string) => authedFetch(`/api/videos/${id}/events`).then(json<VideoIndex>),

  search: (id: string, query: string, fanout: boolean) =>
    authedFetch(`/api/videos/${id}/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, fanout, limit: 20 }),
    }).then(json<SearchResponse>),

  getJob: (id: string) => authedFetch(`/api/jobs/${id}`).then(json<Job>),

  /** Subscribe to SSE updates for a job. Returns an unsubscribe function. */
  subscribeJob: (id: string, onUpdate: (j: Job) => void, onEnd?: () => void) => {
    const es = new EventSource(mediaUrl(`/api/jobs/${id}/events`))
    es.onmessage = (e) => {
      try {
        const j = JSON.parse(e.data) as Job
        onUpdate(j)
        if (j.status === 'done' || j.status === 'failed') {
          es.close()
          onEnd?.()
        }
      } catch { /* ignore parse errors */ }
    }
    es.onerror = () => {
      // EventSource auto-reconnects; only treat as terminal if explicitly closed.
      if (es.readyState === EventSource.CLOSED) onEnd?.()
    }
    return () => es.close()
  },

  listJobs: (video_id?: string) => {
    const q = video_id ? `?video_id=${video_id}` : ''
    return authedFetch(`/api/jobs${q}`).then(json<Job[]>)
  },

  createClip: (video_id: string, start: number, end: number, name?: string, reencode = false) =>
    authedFetch('/api/clips', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_id, start, end, name, reencode }),
    }).then(json<Clip & { duration: number }>),

  listClips: (video_id?: string) => {
    const q = video_id ? `?video_id=${video_id}` : ''
    return authedFetch(`/api/clips${q}`).then(json<Clip[]>)
  },

  deleteClip: (id: string) =>
    authedFetch(`/api/clips/${id}`, { method: 'DELETE' }).then(json<{ deleted: string }>),

  analyzeConfig: () => authedFetch('/api/analyze/config').then(json<AnalyzeConfig>),

  getSuggestions: (videoId: string) =>
    authedFetch(`/api/videos/${videoId}/suggestions`).then(json<SuggestionsPayload>),

  regenerateSuggestions: (videoId: string, prompt: string) =>
    authedFetch(`/api/videos/${videoId}/suggestions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }),
    }).then(json<SuggestionsPayload>),
}

export function fmtTime(seconds: number): string {
  if (!isFinite(seconds)) return '--:--'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  const tenths = Math.floor((seconds - Math.floor(seconds)) * 10)
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${tenths}`
  return `${m}:${String(s).padStart(2, '0')}.${tenths}`
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}
