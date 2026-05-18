/**
 * Module-level project cache for fast project_id → {color, icon, name} lookups
 * everywhere the project association needs to render (Command Center cards,
 * History column, RequestDetail header, StoryBoard breadcrumb).
 *
 * Why module-level: every chip on a page with N requests would otherwise
 * issue N copies of GET /projects, which is wasteful. One module-level
 * cache + a single fetch + a subscriber pattern means all chips share the
 * same data. Refresh happens on a 30s interval and on visibilitychange.
 *
 * Not using TanStack Query because the rest of the codebase doesn't —
 * keeping the dependency surface small.
 */

import { useEffect, useState } from "react"
import { api } from "../lib/api"
import { useAuthStore } from "../stores/auth"

export interface ProjectChipData {
  project_id: string
  name: string
  color: string
  icon: string
  default_team: string | null
  status: string
}

let _cache: Map<string, ProjectChipData> = new Map()
let _lastFetch = 0
const subscribers = new Set<() => void>()
let _inFlight: Promise<void> | null = null

const FALLBACK_UNASSIGNED: ProjectChipData = {
  project_id: "proj-unassigned",
  name: "Unassigned",
  color: "#8080a0",
  icon: "folder",
  default_team: null,
  status: "active",
}

const TTL_MS = 30_000  // refetch at most every 30s on hook mount

async function _fetch(): Promise<void> {
  if (_inFlight) return _inFlight
  _inFlight = (async () => {
    try {
      const res = await api.get("/projects?include_archived=true")
      const next = new Map<string, ProjectChipData>()
      for (const p of res.data || []) {
        next.set(p.project_id, {
          project_id: p.project_id,
          name: p.name,
          color: p.color,
          icon: p.icon,
          default_team: p.default_team,
          status: p.status,
        })
      }
      _cache = next
      _lastFetch = Date.now()
      subscribers.forEach((cb) => cb())
    } catch {
      // Silent fail — chips fall back to Unassigned styling. Auth failures
      // are handled by the api client itself (it redirects to /login).
    } finally {
      _inFlight = null
    }
  })()
  return _inFlight
}

/**
 * Read-only access to the cached projects map. Triggers a fetch if the
 * cache is stale or empty. Returns a fallback Unassigned project for any
 * id not in the cache (defensive: PM-47).
 */
export function useProjectsCache() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const [, force] = useState({})

  useEffect(() => {
    if (!isAuthenticated) return
    const stale = Date.now() - _lastFetch > TTL_MS
    if (_cache.size === 0 || stale) {
      void _fetch()
    }
    const sub = () => force({})
    subscribers.add(sub)
    return () => { subscribers.delete(sub) }
  }, [isAuthenticated])

  return {
    /** Lookup by id, falling back to the Unassigned placeholder if unknown. */
    byId: (project_id: string | null | undefined): ProjectChipData => {
      if (!project_id) return FALLBACK_UNASSIGNED
      return _cache.get(project_id) || FALLBACK_UNASSIGNED
    },
    /** All cached projects (whatever's loaded — may be empty during initial fetch). */
    all: (): ProjectChipData[] => Array.from(_cache.values()),
    /** Force a refresh — used by WebSocket subscribers on project.* events. */
    refresh: () => { void _fetch() },
  }
}

/**
 * Invalidate the cache from outside React (e.g. from a global WebSocket
 * handler). Triggers a refetch and notifies all subscribers.
 */
export function invalidateProjectsCache() {
  _lastFetch = 0
  void _fetch()
}
