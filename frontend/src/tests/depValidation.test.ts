/**
 * BPD-36 — unit tests for the depends_on validator pinned to its
 * extracted pure module. The TaskListEditor's inline chip is a
 * thin render of the output map, so locking down this contract
 * is what keeps the UI correct.
 */
import { describe, it, expect } from "vitest"
import {
  validateDepends,
  type DepValidatorTaskInput,
} from "../components/projects/_depValidation"

const T = (
  task_id: string,
  ordinal: number,
  depends_on: string[] = [],
  title: string = `task ${task_id}`,
): DepValidatorTaskInput => ({ task_id, ordinal, title, depends_on })

describe("validateDepends", () => {
  it("returns empty map for empty input", () => {
    expect(validateDepends([]).size).toBe(0)
  })

  it("returns empty map when all tasks have clean back-pointing deps", () => {
    const result = validateDepends([
      T("T-001", 1, []),
      T("T-002", 2, ["T-001"]),
      T("T-003", 3, ["T-002", "T-001"]),
    ])
    expect(result.size).toBe(0)
  })

  it("flags self_reference with severity=error", () => {
    const result = validateDepends([T("T-001", 1, ["T-001"])])
    const issues = result.get("T-001") || []
    expect(issues).toHaveLength(1)
    expect(issues[0].kind).toBe("self_reference")
    expect(issues[0].severity).toBe("error")
    expect(issues[0].bad_ref).toBe("T-001")
    expect(issues[0].fix_hint).toMatch(/self-reference/i)
  })

  it("flags dangling reference with severity=error", () => {
    const result = validateDepends([
      T("T-001", 1, ["T-MISSING"]),
      T("T-002", 2, []),
    ])
    const issues = result.get("T-001") || []
    expect(issues).toHaveLength(1)
    expect(issues[0].kind).toBe("dangling")
    expect(issues[0].severity).toBe("error")
    expect(issues[0].bad_ref).toBe("T-MISSING")
  })

  it("flags forward_ref (dep on later ordinal) with severity=warn", () => {
    const result = validateDepends([
      T("T-001", 1, ["T-002"]),  // T-002 has ordinal 2, > 1 → forward
      T("T-002", 2, []),
    ])
    const issues = result.get("T-001") || []
    expect(issues).toHaveLength(1)
    expect(issues[0].kind).toBe("forward_ref")
    expect(issues[0].severity).toBe("warn")
    expect(issues[0].bad_ref).toBe("T-002")
    expect(issues[0].bad_ref_title).toBe("task T-002")
  })

  it("flags same-ordinal dep as forward_ref (ordinal must strictly decrease)", () => {
    const result = validateDepends([
      T("T-001", 5, ["T-002"]),
      T("T-002", 5, []),  // same ordinal — also illegal
    ])
    expect(result.get("T-001")?.[0].kind).toBe("forward_ref")
  })

  it("detects a 2-node cycle and marks BOTH participants", () => {
    const result = validateDepends([
      T("T-001", 2, ["T-002"]),  // back-ref → forward (warn)
      T("T-002", 1, ["T-001"]),  // forward-ref + cycle (warn + error)
    ])
    // Both nodes participate in the cycle
    const i1 = result.get("T-001") || []
    const i2 = result.get("T-002") || []
    expect(i1.some((i) => i.kind === "cycle")).toBe(true)
    expect(i2.some((i) => i.kind === "cycle")).toBe(true)
  })

  it("detects a 3-node cycle correctly", () => {
    const result = validateDepends([
      T("T-001", 1, ["T-003"]),
      T("T-002", 2, ["T-001"]),
      T("T-003", 3, ["T-002"]),
    ])
    for (const id of ["T-001", "T-002", "T-003"]) {
      expect(result.get(id)?.some((i) => i.kind === "cycle")).toBe(true)
    }
  })

  it("does NOT flag self-edge as cycle (reported under self_reference)", () => {
    const result = validateDepends([T("T-001", 1, ["T-001"])])
    const issues = result.get("T-001") || []
    expect(issues.every((i) => i.kind !== "cycle")).toBe(true)
    expect(issues.some((i) => i.kind === "self_reference")).toBe(true)
  })

  it("reports multiple kinds on one task in one map entry", () => {
    const result = validateDepends([
      T("T-001", 5, ["T-001", "T-MISSING"]),  // self + dangling
    ])
    const issues = result.get("T-001") || []
    expect(issues).toHaveLength(2)
    const kinds = issues.map((i) => i.kind).sort()
    expect(kinds).toEqual(["dangling", "self_reference"])
  })

  it("does NOT flag a clean task even when other tasks have issues", () => {
    const result = validateDepends([
      T("T-001", 1, []),
      T("T-002", 2, ["T-MISSING"]),
      T("T-003", 3, ["T-001"]),
    ])
    expect(result.get("T-001")).toBeUndefined()
    expect(result.get("T-003")).toBeUndefined()
    expect(result.get("T-002")).toBeDefined()
  })

  it("handles null/undefined depends_on as empty (no false positives)", () => {
    const result = validateDepends([
      // @ts-expect-error — testing tolerance of API quirks
      { task_id: "T-001", ordinal: 1, title: "x", depends_on: null },
      { task_id: "T-002", ordinal: 2, title: "y" },  // depends_on undefined
    ])
    expect(result.size).toBe(0)
  })

  it("fix_hint includes the bad ref for actionable copy", () => {
    const r1 = validateDepends([T("T-001", 1, ["T-MISSING"])])
    expect(r1.get("T-001")?.[0].fix_hint).toContain("T-MISSING")

    const r2 = validateDepends([
      T("T-001", 1, ["T-002"]),
      T("T-002", 2, []),
    ])
    expect(r2.get("T-001")?.[0].fix_hint).toContain("T-002")
    expect(r2.get("T-001")?.[0].fix_hint).toContain("ordinal 2")
  })
})
