"""KB-04 — structure-aware chunker tests (pure, no DB)."""

from __future__ import annotations

from src.knowledge.chunker import (
    Chunk,
    chunk_document,
    detect_kind,
    estimate_tokens,
)

# ── Detection ────────────────────────────────────────────────────────────


def test_detect_kind_by_source_type():
    assert detect_kind("code", None, "x") == "code"
    assert detect_kind("prd", None, "x") == "markdown"


def test_detect_kind_by_uri():
    assert detect_kind(None, "docs/architecture.md", "x") == "markdown"
    assert detect_kind(None, "src/main.py", "x") == "code"


def test_detect_kind_by_sniff():
    assert detect_kind(None, None, "# Title\n\nbody") == "markdown"
    code = "def a():\n  pass\ndef b():\n  pass\nclass C:\n  pass"
    assert detect_kind(None, None, code) == "code"
    assert detect_kind(None, None, "just some prose with no structure") == "plaintext"


# ── Empty / trivial ──────────────────────────────────────────────────────


def test_empty_returns_no_chunks():
    assert chunk_document("") == []
    assert chunk_document("   \n  ") == []


def test_ordinals_are_sequential():
    text = "\n\n".join(f"Paragraph number {i} with enough words here." for i in range(20))
    chunks = chunk_document(text, source_type="lesson", max_tokens=40, overlap_tokens=8)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert all(isinstance(c, Chunk) for c in chunks)


# ── Markdown ─────────────────────────────────────────────────────────────


def test_markdown_carries_heading_breadcrumb():
    md = (
        "# Architecture\n\nTop level intro.\n\n"
        "## Request lifecycle\n\nThe orchestrator dispatches the workflow.\n\n"
        "### Stages\n\nEach stage runs an agent."
    )
    chunks = chunk_document(md, source_type="repo_doc", max_tokens=20, overlap_tokens=4)
    # every contributing breadcrumb is captured across the chunks' headings lists
    all_headings = {h for c in chunks for h in c.metadata.get("headings", [])}
    assert "Architecture > Request lifecycle" in all_headings
    assert any("Stages" in h for h in all_headings)
    assert all(c.metadata["kind"] == "markdown" for c in chunks)


def test_markdown_splits_under_max_tokens():
    body = "\n\n".join(f"Section body paragraph {i}, fairly wordy content." for i in range(30))
    md = "# Big\n\n" + body
    chunks = chunk_document(md, source_type="repo_doc", max_tokens=50, overlap_tokens=10)
    assert len(chunks) > 1
    # each chunk respects budget (allowing small overlap slack)
    assert all(c.token_estimate <= 50 + 20 for c in chunks)


# ── Code ─────────────────────────────────────────────────────────────────


def test_code_splits_on_symbols():
    code = (
        "import os\n\n"
        "def alpha(x):\n    return x + 1\n\n"
        "def beta(y):\n    return y * 2\n\n"
        "class Gamma:\n    def m(self):\n        return 3\n"
    )
    chunks = chunk_document(code, source_type="code", max_tokens=1000, overlap_tokens=0)
    # symbols are captured (packed into one or more chunks); union covers all
    syms = {s for c in chunks for s in c.metadata.get("symbols", [])}
    assert "<module>" in syms
    assert "alpha" in syms and "beta" in syms and "Gamma" in syms
    assert all(c.metadata["kind"] == "code" for c in chunks)


def test_code_without_symbols_falls_back():
    code = "x = 1\ny = 2\nz = x + y\nprint(z)"
    chunks = chunk_document(code, source_type="code", max_tokens=1000)
    assert len(chunks) == 1  # no symbols → single plaintext-style unit


# ── Plaintext + oversized hard-split ─────────────────────────────────────


def test_oversized_unit_is_hard_split():
    huge = "word " * 2000  # one giant paragraph, no blank lines
    chunks = chunk_document(huge, source_type="lesson", max_tokens=100, overlap_tokens=10)
    assert len(chunks) > 1
    assert all(c.token_estimate <= 100 + 5 for c in chunks)


def test_overlap_present_between_chunks():
    # distinct sentences so we can detect carry-over
    paras = [f"Sentence {i} alpha beta gamma delta epsilon." for i in range(40)]
    text = "\n\n".join(paras)
    chunks = chunk_document(text, source_type="lesson", max_tokens=40, overlap_tokens=12)
    assert len(chunks) >= 2
    # the tail of chunk N should appear at the head of chunk N+1 (overlap seed)
    overlaps = 0
    for a, b in zip(chunks, chunks[1:], strict=False):
        tail_words = a.text.split()[-4:]
        if tail_words and " ".join(tail_words) in b.text:
            overlaps += 1
    assert overlaps >= 1


# ── Token estimate ───────────────────────────────────────────────────────


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("x" * 40) == 10
