/**
 * KB-10 — Knowledge Base store.
 *
 * Single Zustand store backing the Knowledge Base page (Upload / Tag &
 * Bucket / Buckets / Ground-a-Task) plus the Command Center grounding
 * selector and the Request-detail Grounding Report.
 *
 * Soft-fail aware: every read tolerates `kb_available=false` (the backend
 * returns empty payloads + a meta flag when the subsystem is down). The UI
 * reads `available` to switch between the live screens and an "offline"
 * banner — it never throws the user into an error state just because the
 * platform booted without a Voyage key.
 */

import { create } from "zustand"
import { api } from "../lib/api"

// ── Wire types (mirror src/api/routes/knowledge.py) ───────────────────────

export interface KbBucket {
  bucket_id: string
  name: string
  slug: string
  description: string
  is_system: boolean
  created_by: string
  created_at: string | null
  doc_count: number
  chunk_count: number
}

export interface KbDocument {
  doc_id: string
  namespace: string
  title: string
  uri: string | null
  source_type: string
  sensitivity: string
  status: string // pending | approved | superseded | retired
  version: number
  curated_by: string | null
  approved_at: string | null
  created_at: string | null
  bucket_ids: string[]
  chunk_count?: number
}

export interface KbStatus {
  available: boolean
  reason: string
  namespace: string | null
  embed_model: string | null
  rerank: boolean | null
}

export interface GroundingRetrieval {
  audit_id: string
  agent_id: string
  namespace: string
  query: string
  bucket_ids: string[]
  returned_chunk_ids: string[]
  cited_chunk_ids: string[]
  created_at: string | null
}

export interface GroundingCitation {
  chunk_id: string
  doc_id: string | null
  title: string | null
  uri: string | null
  snippet: string
  // KB-23 — provenance of the source document (claim → chunk → document → source).
  source_type?: string | null
  version?: number | null
  status?: string | null
  approved_by?: string | null
  approved_at?: string | null
  ingested_at?: string | null
}

export interface GroundingDecision {
  decision_id: string
  agent_id: string
  project_id: string | null
  summary: string
  retrieved_chunk_ids: string[]
  recalled_memory_ids: string[]
  inputs_digest: string | null
  created_at: string | null
}

export interface GroundingReport {
  request_id: string
  buckets: string[]
  retrievals: GroundingRetrieval[]
  citations: GroundingCitation[]
  decisions: GroundingDecision[]
}

// KB-PL — one de-duplicated search hit (best chunk per document) from
// POST /knowledge/search. `uri` is the original source link the UI renders.
export interface KbSearchResult {
  doc_id: string
  title: string
  snippet: string
  score: number
  uri: string | null
  namespace: string
  metadata: Record<string, unknown>
  more_matches?: number
}

// KB-PL — result envelope from an ingest-url / ingest-text call.
export interface KbIngestResult {
  doc_id: string
  status: string // approved | pending
  skipped: boolean
  chunks: number
  title: string
  uri: string | null
  bucket_ids: string[]
}

interface KnowledgeState {
  status: KbStatus | null
  buckets: KbBucket[]
  documents: KbDocument[]
  loading: boolean
  error: string | null

  fetchStatus: () => Promise<void>
  fetchBuckets: () => Promise<void>
  fetchDocuments: (opts?: { status?: string; bucketId?: string; projectId?: string }) => Promise<void>
  createBucket: (name: string, description?: string) => Promise<KbBucket | null>
  renameBucket: (bucketId: string, name: string, description?: string) => Promise<void>
  deleteBucket: (bucketId: string) => Promise<void>
  uploadDocument: (
    file: File, bucketIds: string[], projectId?: string,
  ) => Promise<KbDocument | null>
  approveDocument: (docId: string) => Promise<void>
  retireDocument: (docId: string) => Promise<void>
  purgeDocument: (docId: string) => Promise<void>
  setDocumentBuckets: (docId: string, bucketIds: string[]) => Promise<void>
  reindexPlatform: () => Promise<Record<string, unknown> | null>
  fetchGrounding: (requestId: string) => Promise<GroundingReport | null>
  // KB-PL — personal knowledge library: ingest by URL / paste, and search.
  ingestUrl: (
    url: string, bucketIds: string[], title?: string,
  ) => Promise<KbIngestResult | null>
  ingestText: (
    text: string, title: string, bucketIds: string[], sourceUrl?: string,
  ) => Promise<KbIngestResult | null>
  searchLibrary: (
    query: string, bucketIds?: string[], topK?: number,
  ) => Promise<KbSearchResult[]>
  clearError: () => void
}

function errMessage(e: unknown, fallback: string): string {
  if (e instanceof Error) {
    const m = e.message.match(/^\d{3}:\s*(.*)$/s)
    if (m) {
      try {
        const parsed = JSON.parse(m[1])
        if (parsed && typeof parsed.detail === "string") return parsed.detail
      } catch {
        /* not JSON */
      }
      return m[1].trim() || fallback
    }
    return e.message || fallback
  }
  return fallback
}

export const useKnowledgeStore = create<KnowledgeState>((set, get) => ({
  status: null,
  buckets: [],
  documents: [],
  loading: false,
  error: null,

  fetchStatus: async () => {
    try {
      const res = await api.get<{ data: KbStatus }>("/knowledge")
      set({ status: res.data })
    } catch (e) {
      set({ error: errMessage(e, "Failed to load KB status") })
    }
  },

  fetchBuckets: async () => {
    set({ loading: true, error: null })
    try {
      const res = await api.get<{ data: KbBucket[] }>("/knowledge/buckets")
      set({ buckets: res.data ?? [], loading: false })
    } catch (e) {
      set({ loading: false, error: errMessage(e, "Failed to load buckets") })
    }
  },

  fetchDocuments: async (opts) => {
    set({ loading: true, error: null })
    const params = new URLSearchParams()
    if (opts?.status) params.set("status", opts.status)
    if (opts?.bucketId) params.set("bucket_id", opts.bucketId)
    if (opts?.projectId) params.set("project_id", opts.projectId)
    const qs = params.toString()
    try {
      const res = await api.get<{ data: KbDocument[] }>(
        `/knowledge/documents${qs ? `?${qs}` : ""}`,
      )
      set({ documents: res.data ?? [], loading: false })
    } catch (e) {
      set({ loading: false, error: errMessage(e, "Failed to load documents") })
    }
  },

  createBucket: async (name, description = "") => {
    try {
      const res = await api.post<{ data: KbBucket }>("/knowledge/buckets", {
        name,
        description,
      })
      set({ buckets: [...get().buckets, res.data] })
      return res.data
    } catch (e) {
      set({ error: errMessage(e, "Failed to create bucket") })
      return null
    }
  },

  renameBucket: async (bucketId, name, description) => {
    try {
      const res = await api.patch<{ data: KbBucket }>(
        `/knowledge/buckets/${encodeURIComponent(bucketId)}`,
        { name, description },
      )
      set({
        buckets: get().buckets.map((b) => (b.bucket_id === bucketId ? res.data : b)),
      })
    } catch (e) {
      set({ error: errMessage(e, "Failed to rename bucket") })
    }
  },

  deleteBucket: async (bucketId) => {
    const snapshot = get().buckets
    set({ buckets: snapshot.filter((b) => b.bucket_id !== bucketId) })
    try {
      await api.delete(`/knowledge/buckets/${encodeURIComponent(bucketId)}`)
    } catch (e) {
      set({ buckets: snapshot, error: errMessage(e, "Failed to delete bucket") })
    }
  },

  uploadDocument: async (file, bucketIds, projectId) => {
    set({ error: null })
    const fd = new FormData()
    fd.append("file", file)
    fd.append("bucket_ids", JSON.stringify(bucketIds))
    // KB-16/scoping — when a project is selected, ingest into its isolated
    // kb_project_<id> namespace instead of the platform corpus.
    if (projectId) fd.append("project_id", projectId)
    try {
      const res = await api.postForm<{ data: KbDocument & { skipped: boolean } }>(
        "/knowledge/documents",
        fd,
      )
      await get().fetchDocuments(projectId ? { projectId } : undefined)
      await get().fetchBuckets()
      return res.data
    } catch (e) {
      set({ error: errMessage(e, `Failed to upload ${file.name}`) })
      return null
    }
  },

  approveDocument: async (docId) => {
    try {
      await api.post(`/knowledge/documents/${encodeURIComponent(docId)}/approve`)
      set({
        documents: get().documents.map((d) =>
          d.doc_id === docId ? { ...d, status: "approved" } : d,
        ),
      })
      await get().fetchBuckets()
    } catch (e) {
      set({ error: errMessage(e, "Failed to approve document") })
    }
  },

  retireDocument: async (docId) => {
    try {
      await api.post(`/knowledge/documents/${encodeURIComponent(docId)}/retire`, {
        status: "superseded",
      })
      set({
        documents: get().documents.map((d) =>
          d.doc_id === docId ? { ...d, status: "superseded" } : d,
        ),
      })
      await get().fetchBuckets()
    } catch (e) {
      set({ error: errMessage(e, "Failed to retire document") })
    }
  },

  purgeDocument: async (docId) => {
    const snapshot = get().documents
    set({ documents: snapshot.filter((d) => d.doc_id !== docId) })
    try {
      await api.delete(`/knowledge/documents/${encodeURIComponent(docId)}`)
      await get().fetchBuckets()
    } catch (e) {
      set({ documents: snapshot, error: errMessage(e, "Failed to purge document") })
    }
  },

  setDocumentBuckets: async (docId, bucketIds) => {
    try {
      await api.put(`/knowledge/documents/${encodeURIComponent(docId)}/buckets`, {
        bucket_ids: bucketIds,
      })
      set({
        documents: get().documents.map((d) =>
          d.doc_id === docId ? { ...d, bucket_ids: bucketIds } : d,
        ),
      })
      await get().fetchBuckets()
    } catch (e) {
      set({ error: errMessage(e, "Failed to update document buckets") })
    }
  },

  reindexPlatform: async () => {
    set({ error: null })
    try {
      const res = await api.post<{ data: Record<string, unknown> }>("/knowledge/reindex")
      await get().fetchBuckets()
      await get().fetchDocuments()
      return res.data
    } catch (e) {
      set({ error: errMessage(e, "Reindex failed") })
      return null
    }
  },

  fetchGrounding: async (requestId) => {
    try {
      const res = await api.get<{ data: GroundingReport }>(
        `/knowledge/grounding/${encodeURIComponent(requestId)}`,
      )
      return res.data
    } catch (e) {
      set({ error: errMessage(e, "Failed to load grounding report") })
      return null
    }
  },

  // ── KB-PL — personal knowledge library ────────────────────────────────────

  ingestUrl: async (url, bucketIds, title) => {
    set({ error: null })
    try {
      const res = await api.post<{ data: KbIngestResult }>("/knowledge/ingest-url", {
        url,
        bucket_ids: bucketIds,
        ...(title ? { title } : {}),
      })
      // Refresh the personal library view + bucket counts after ingest.
      await get().fetchDocuments()
      await get().fetchBuckets()
      return res.data
    } catch (e) {
      set({ error: errMessage(e, "Failed to ingest URL") })
      return null
    }
  },

  ingestText: async (text, title, bucketIds, sourceUrl) => {
    set({ error: null })
    try {
      const res = await api.post<{ data: KbIngestResult }>("/knowledge/ingest-text", {
        text,
        title,
        bucket_ids: bucketIds,
        ...(sourceUrl ? { source_url: sourceUrl } : {}),
      })
      await get().fetchDocuments()
      await get().fetchBuckets()
      return res.data
    } catch (e) {
      set({ error: errMessage(e, "Failed to ingest text") })
      return null
    }
  },

  searchLibrary: async (query, bucketIds, topK = 10) => {
    set({ error: null })
    try {
      const res = await api.post<{ data: KbSearchResult[] }>("/knowledge/search", {
        query,
        bucket_ids: bucketIds ?? [],
        top_k: topK,
      })
      return res.data ?? []
    } catch (e) {
      set({ error: errMessage(e, "Search failed") })
      return []
    }
  },

  clearError: () => set({ error: null }),
}))
