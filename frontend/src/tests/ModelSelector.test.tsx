/**
 * PAM-18 — ModelSelector pure-helper contract.
 *
 * The full DOM-render contract (chip vs button, dropdown open/close,
 * onChange/onReset wiring) is verified by the E2E smoke test PAM-19
 * adds against the real backend. Here we lock down the pure helpers
 * the component depends on — those are where the actual business
 * logic lives (tier ordering, in-tier sort, display fallback) and
 * where a regression silently mis-orders the dropdown.
 *
 * Frontend container ships vitest WITHOUT jsdom or
 * @testing-library/react — installing them was out of scope for
 * PAM-18, so component-render coverage lives in PAM-19's smoke
 * test against the live app instead.
 */
import { describe, expect, it } from "vitest"

import {
  TIER_ORDER,
  TIER_LABEL,
  groupByTier,
  modelLabel,
} from "../components/ui/_modelSelectorUtils"
import type { Model } from "../stores/models"

// ── Fixtures ──────────────────────────────────────────────────────────

function M(
  id: string,
  tier: string,
  display_name: string,
  provider_type = "anthropic_aws",
): Model {
  return {
    id,
    provider_type,
    model_id: id,
    display_name,
    tier,
    tool_calling_mode: "native",
    base_url: null,
    pricing_per_million: { input: 0, output: 0 },
  }
}

// ── modelLabel ────────────────────────────────────────────────────────

describe("modelLabel", () => {
  it("uses display_name when present", () => {
    expect(modelLabel(M("x", "fast", "Pretty Name"))).toBe("Pretty Name")
  })

  it("falls back to id when display_name is empty / whitespace", () => {
    expect(modelLabel(M("x", "fast", ""))).toBe("x")
    expect(modelLabel(M("x", "fast", "   "))).toBe("x")
  })
})

// ── groupByTier ───────────────────────────────────────────────────────

describe("groupByTier", () => {
  it("orders groups frontier → workhorse → fast → local → other", () => {
    const groups = groupByTier([
      M("a", "local", "A"),
      M("b", "fast", "B"),
      M("c", "frontier", "C"),
      M("d", "workhorse", "D"),
      M("e", "unknown_tier", "E"),
    ])
    expect(groups.map((g) => g.tier)).toEqual([
      "frontier", "workhorse", "fast", "local", "other",
    ])
  })

  it("uses TIER_LABEL for known tiers and 'Other' for unknown", () => {
    const groups = groupByTier([
      M("a", "frontier", "A"),
      M("b", "weird", "B"),
    ])
    const labels = Object.fromEntries(groups.map((g) => [g.tier, g.label]))
    expect(labels.frontier).toBe(TIER_LABEL.frontier)
    expect(labels.other).toBe("Other")
  })

  it("within a tier, sorts by provider_type then display name", () => {
    const groups = groupByTier([
      M("z", "frontier", "Zebra", "openai"),
      M("a", "frontier", "Alpha", "anthropic_aws"),
      M("b", "frontier", "Beta",  "anthropic_aws"),
    ])
    expect(groups).toHaveLength(1)
    expect(groups[0].models.map((m) => m.id)).toEqual(["a", "b", "z"])
    // anthropic_aws comes before openai alphabetically; within
    // anthropic_aws Alpha comes before Beta.
  })

  it("returns empty array on empty input", () => {
    expect(groupByTier([])).toEqual([])
  })

  it("handles all models in one tier without dropping any", () => {
    const groups = groupByTier([
      M("a", "frontier", "A"),
      M("b", "frontier", "B"),
      M("c", "frontier", "C"),
    ])
    expect(groups).toHaveLength(1)
    expect(groups[0].models.map((m) => m.id)).toEqual(["a", "b", "c"])
  })

  it("ordering is stable across calls (idempotent sort)", () => {
    const input = [
      M("a", "fast", "A"),
      M("b", "frontier", "B"),
      M("c", "workhorse", "C"),
    ]
    const first = groupByTier(input)
    const second = groupByTier(input)
    expect(first).toEqual(second)
  })
})

// ── TIER_ORDER constants ──────────────────────────────────────────────

describe("TIER_ORDER", () => {
  it("has the four canonical tiers in priority order", () => {
    expect(TIER_ORDER.frontier).toBe(0)
    expect(TIER_ORDER.workhorse).toBe(1)
    expect(TIER_ORDER.fast).toBe(2)
    expect(TIER_ORDER.local).toBe(3)
    // No accidental extras — protects against silently adding a new
    // tier without updating TIER_LABEL too.
    expect(Object.keys(TIER_ORDER).sort()).toEqual(
      ["fast", "frontier", "local", "workhorse"],
    )
  })
})
