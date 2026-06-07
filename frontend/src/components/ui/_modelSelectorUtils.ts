/**
 * PAM-18 — pure helpers extracted from ModelSelector.tsx so the
 * grouping/labeling logic can be unit-tested without a DOM env
 * (the frontend container ships vitest without jsdom).
 */
import type { Model } from "../../stores/models"

export const TIER_ORDER: Record<string, number> = {
  frontier: 0,
  workhorse: 1,
  fast: 2,
  local: 3,
}

export const TIER_LABEL: Record<string, string> = {
  frontier: "Frontier",
  workhorse: "Workhorse",
  fast: "Fast",
  local: "Local",
}

export interface ModelGroup {
  tier: string
  label: string
  models: Model[]
}

/**
 * Group models by tier (frontier > workhorse > fast > local > other),
 * sort within each tier by provider_type then display name. Stable
 * ordering across renders is required so React's keyed list doesn't
 * thrash + the visible order matches what the operator expects.
 */
export function groupByTier(models: Model[]): ModelGroup[] {
  const buckets = new Map<string, Model[]>()
  for (const m of models) {
    const t = m.tier in TIER_ORDER ? m.tier : "other"
    if (!buckets.has(t)) buckets.set(t, [])
    buckets.get(t)!.push(m)
  }
  const groups: ModelGroup[] = []
  for (const [tier, list] of buckets) {
    list.sort(
      (a, b) =>
        a.provider_type.localeCompare(b.provider_type) ||
        (a.display_name || a.id).localeCompare(b.display_name || b.id),
    )
    groups.push({
      tier,
      label: TIER_LABEL[tier] ?? "Other",
      models: list,
    })
  }
  groups.sort(
    (a, b) =>
      (TIER_ORDER[a.tier] ?? 99) - (TIER_ORDER[b.tier] ?? 99),
  )
  return groups
}

/**
 * Display-name with fallback to catalog id. Legacy alias entries
 * sometimes lack a display_name; we never want to render an empty
 * pill in the UI.
 */
export function modelLabel(m: Model): string {
  return m.display_name?.trim() || m.id
}
