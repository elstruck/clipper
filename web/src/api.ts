// Typed fetch wrappers for the clipper FastAPI backend.

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

export interface VideoIndex {
  video: string
  duration: number
  window: number
  overlap: number
  chunks: ChunkInfo[]
  scenes: string[]
  events: EventRow[]
}

export interface SearchHit {
  start: number
  end: number
  description: string
  score: number
  chunk_index: number
}

export interface SearchResponse {
  hits: SearchHit[]
  mode: 'text' | 'fanout'
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
  listVideos: () => fetch('/api/videos').then(json<Video[]>),

  getVideo: (id: string) => fetch(`/api/videos/${id}`).then(json<Video>),

  uploadVideo: (file: File, onProgress?: (frac: number) => void) =>
    new Promise<Video>((resolve, reject) => {
      const form = new FormData()
      form.append('file', file)
      const xhr = new XMLHttpRequest()
      xhr.open('POST', '/api/videos')
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

  deleteVideo: (id: string) =>
    fetch(`/api/videos/${id}`, { method: 'DELETE' }).then(json<{ deleted: string }>),

  startIndex: (id: string) =>
    fetch(`/api/videos/${id}/index`, { method: 'POST' }).then(
      json<{ job_id: string; video_id: string; status: string }>,
    ),

  getEvents: (id: string) => fetch(`/api/videos/${id}/events`).then(json<VideoIndex>),

  search: (id: string, query: string, fanout: boolean) =>
    fetch(`/api/videos/${id}/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, fanout, limit: 20 }),
    }).then(json<SearchResponse>),

  getJob: (id: string) => fetch(`/api/jobs/${id}`).then(json<Job>),

  listJobs: (video_id?: string) => {
    const q = video_id ? `?video_id=${video_id}` : ''
    return fetch(`/api/jobs${q}`).then(json<Job[]>)
  },

  createClip: (video_id: string, start: number, end: number, name?: string, reencode = false) =>
    fetch('/api/clips', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_id, start, end, name, reencode }),
    }).then(json<Clip & { duration: number }>),

  listClips: (video_id?: string) => {
    const q = video_id ? `?video_id=${video_id}` : ''
    return fetch(`/api/clips${q}`).then(json<Clip[]>)
  },

  deleteClip: (id: string) =>
    fetch(`/api/clips/${id}`, { method: 'DELETE' }).then(json<{ deleted: string }>),
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
