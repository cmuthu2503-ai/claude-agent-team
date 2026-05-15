import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
  cleanup,
} from "@testing-library/react";
import PromptStudio from "../pages/PromptStudio";
import { usePromptStudioStore } from "../stores/promptStudio";
import { api } from "../lib/api";

vi.mock("../lib/api", () => ({
  api: {
    post: vi.fn(),
    get: vi.fn(),
    postForm: vi.fn(),
  },
}));

const mockedPost = api.post as unknown as ReturnType<typeof vi.fn>;

function resetStore() {
  usePromptStudioStore.setState({
    variants: [],
    isLoading: false,
    error: null,
    lastPrompt: "",
  });
}

function typePrompt(text: string) {
  const textarea = screen.getByLabelText(/your idea/i);
  fireEvent.change(textarea, { target: { value: text } });
}

function clickGenerate() {
  const btn = screen.getByRole("button", { name: /generate|regenerate/i });
  fireEvent.click(btn);
}

describe("PromptStudio page", () => {
  beforeEach(() => {
    mockedPost.mockReset();
    resetStore();
  });

  afterEach(() => {
    cleanup();
  });

  // TC-014: exactly one variant card rendered
  it("renders exactly one variant card after a successful generation", async () => {
    mockedPost.mockResolvedValueOnce({
      variants: [{ text: "the one prompt", model: "claude-opus-4-7" }],
    });

    render(<PromptStudio />);
    typePrompt("write a haiku");
    clickGenerate();

    await waitFor(() => {
      expect(screen.getByText("the one prompt")).toBeInTheDocument();
    });

    const cards = screen.getAllByRole("article", {
      name: /generated prompt/i,
    });
    expect(cards).toHaveLength(1);
  });

  // TC-015: no "Variant 1/2/3" or "Compare" text in DOM
  it("does not render 'Variant N' labels or a Compare control", async () => {
    mockedPost.mockResolvedValueOnce({
      variants: [{ text: "a prompt", model: "claude-opus-4-7" }],
    });

    render(<PromptStudio />);
    typePrompt("hello");
    clickGenerate();

    await waitFor(() => {
      expect(screen.getByText("a prompt")).toBeInTheDocument();
    });

    expect(screen.queryByText(/Variant\s*1/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Variant\s*2/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Variant\s*3/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Compare/i)).not.toBeInTheDocument();
  });

  // TC-020: exactly one skeleton during loading
  // TC-021: skeleton replaced in place (no layout shift / no extra skeletons)
  it("shows exactly one skeleton while loading and replaces it with the card", async () => {
    let resolveFn: (v: unknown) => void = () => undefined;
    mockedPost.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFn = resolve;
      }),
    );

    render(<PromptStudio />);
    typePrompt("hello");
    clickGenerate();

    // During loading: exactly one skeleton, no card yet.
    await waitFor(() => {
      expect(screen.getAllByTestId("skeleton-card")).toHaveLength(1);
    });
    expect(
      screen.queryByRole("article", { name: /generated prompt/i }),
    ).not.toBeInTheDocument();

    // Resolve — skeleton goes away, exactly one card appears.
    await act(async () => {
      resolveFn({
        variants: [{ text: "final prompt", model: "claude-opus-4-7" }],
      });
    });

    await waitFor(() => {
      expect(screen.getByText("final prompt")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("skeleton-card")).not.toBeInTheDocument();
    expect(
      screen.getAllByRole("article", { name: /generated prompt/i }),
    ).toHaveLength(1);
  });

  // TC-022: error replaces skeleton in single card slot
  it("replaces the skeleton with an error card when the request fails", async () => {
    let rejectFn: (e: unknown) => void = () => undefined;
    mockedPost.mockReturnValueOnce(
      new Promise((_resolve, reject) => {
        rejectFn = reject;
      }),
    );

    render(<PromptStudio />);
    typePrompt("hello");
    clickGenerate();

    await waitFor(() => {
      expect(screen.getByTestId("skeleton-card")).toBeInTheDocument();
    });

    await act(async () => {
      rejectFn(new Error("network boom"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("error-card")).toBeInTheDocument();
    });

    // Skeleton gone, no variant card rendered.
    expect(screen.queryByTestId("skeleton-card")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("article", { name: /generated prompt/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/network boom/i)).toBeInTheDocument();
  });

  // TC-023: regenerate replaces card (no append)
  it("replaces the existing card on regenerate — never appends", async () => {
    mockedPost
      .mockResolvedValueOnce({
        variants: [{ text: "first prompt", model: "claude-opus-4-7" }],
      })
      .mockResolvedValueOnce({
        variants: [{ text: "second prompt", model: "claude-opus-4-7" }],
      });

    render(<PromptStudio />);
    typePrompt("hello");
    clickGenerate();

    await waitFor(() => {
      expect(screen.getByText("first prompt")).toBeInTheDocument();
    });

    // Click Regenerate.
    fireEvent.click(screen.getByRole("button", { name: /regenerate/i }));

    await waitFor(() => {
      expect(screen.getByText("second prompt")).toBeInTheDocument();
    });

    expect(screen.queryByText("first prompt")).not.toBeInTheDocument();
    expect(
      screen.getAllByRole("article", { name: /generated prompt/i }),
    ).toHaveLength(1);
  });

  // TC-024: double-click Regenerate fires only one request
  it("ignores duplicate clicks while a request is in flight", async () => {
    let resolveFirst: (v: unknown) => void = () => undefined;
    mockedPost.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFirst = resolve;
      }),
    );

    render(<PromptStudio />);
    typePrompt("hello");

    const btn = screen.getByRole("button", { name: /generate/i });
    fireEvent.click(btn);
    // Second click while loading should be a no-op.
    fireEvent.click(btn);
    fireEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByTestId("skeleton-card")).toBeInTheDocument();
    });

    expect(mockedPost).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFirst({
        variants: [{ text: "only prompt", model: "claude-opus-4-7" }],
      });
    });

    await waitFor(() => {
      expect(screen.getByText("only prompt")).toBeInTheDocument();
    });

    expect(mockedPost).toHaveBeenCalledTimes(1);
  });

  // TC-025: failed regen → error replaces stale content
  it("regen failure replaces stale successful content with the error card", async () => {
    mockedPost
      .mockResolvedValueOnce({
        variants: [{ text: "good prompt", model: "claude-opus-4-7" }],
      })
      .mockRejectedValueOnce(new Error("rate limited"));

    render(<PromptStudio />);
    typePrompt("hello");
    clickGenerate();

    await waitFor(() => {
      expect(screen.getByText("good prompt")).toBeInTheDocument();
    });

    // Trigger regenerate which will fail.
    fireEvent.click(screen.getByRole("button", { name: /regenerate/i }));

    await waitFor(() => {
      expect(screen.getByTestId("error-card")).toBeInTheDocument();
    });

    // Stale successful content is gone — only the error card remains.
    expect(screen.queryByText("good prompt")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("article", { name: /generated prompt/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/rate limited/i)).toBeInTheDocument();
  });
});
