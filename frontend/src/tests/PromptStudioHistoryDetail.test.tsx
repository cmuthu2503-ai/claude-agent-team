import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import PromptStudioHistoryDetail from "../pages/PromptStudioHistoryDetail";
import type { PromptStudioHistoryEntry } from "../types/promptStudio";

function makeEntry(
  texts: string[],
  overrides: Partial<PromptStudioHistoryEntry> = {},
): PromptStudioHistoryEntry {
  return {
    id: "entry-1",
    prompt: "the original idea",
    createdAt: "2026-05-01T12:00:00Z",
    variants: texts.map((text, i) => ({
      id: `v-${i}`,
      text,
      model: "claude-opus-4-7",
    })),
    ...overrides,
  };
}

describe("PromptStudioHistoryDetail (US-008 legacy multi-variant)", () => {
  afterEach(() => {
    cleanup();
  });

  // TC-026: legacy entry (variants.length > 1) renders only variants[0]
  it("renders only variants[0] when the entry has multiple variants", () => {
    const entry = makeEntry([
      "first variant text",
      "second variant text",
      "third variant text",
    ]);

    render(<PromptStudioHistoryDetail entry={entry} />);

    expect(screen.getByText("first variant text")).toBeInTheDocument();
    expect(
      screen.queryByText("second variant text"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("third variant text"),
    ).not.toBeInTheDocument();

    // Exactly one variant card on screen.
    expect(
      screen.getAllByRole("article", { name: /generated prompt/i }),
    ).toHaveLength(1);
  });

  // TC-027: legacy badge appears when variants.length > 1
  // and the badge text matches "legacy multi-variant"
  it("shows the legacy badge when variants.length > 1", () => {
    const entry = makeEntry([
      "first variant text",
      "second variant text",
      "third variant text",
    ]);

    render(<PromptStudioHistoryDetail entry={entry} />);

    const badge = screen.getByTestId("legacy-badge");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent(/legacy multi-variant/i);
  });

  // TC-028: no badge when variants.length === 1
  it("does not show the legacy badge when variants.length === 1", () => {
    const entry = makeEntry(["only variant text"]);

    render(<PromptStudioHistoryDetail entry={entry} />);

    expect(screen.getByText("only variant text")).toBeInTheDocument();
    expect(screen.queryByTestId("legacy-badge")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/legacy multi-variant/i),
    ).not.toBeInTheDocument();
  });
});
