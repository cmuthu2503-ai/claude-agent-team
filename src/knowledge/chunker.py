"""Structure-aware chunker (KB-04).

Turns a document into retrievable ``Chunk``s sized for embedding (~512–1024
tokens) with a little overlap so context isn't lost at boundaries. Three
strategies, dispatched by content kind:

  - **markdown** — split on headings; each block carries its heading
    breadcrumb (``Architecture > Request lifecycle``) in metadata, so a
    retrieved chunk knows where it came from.
  - **code** — split on top-level symbol boundaries (``def`` / ``class`` /
    ``function`` / ``export`` …) via a language-agnostic heuristic. Full
    tree-sitter parsing is deferred (Phase 1 corpus is markdown-dominant);
    this can be swapped behind ``_split_code_units`` later without touching
    the packer.
  - **plaintext** — paragraph split, then a token-window fallback.

All three feed one greedy **packer** that accumulates units up to
``max_tokens``, hard-splits any single oversized unit, and seeds each new
chunk with an overlap tail from the previous one. Pure-Python, no native
deps — fully unit-testable.

Token counts are an **estimate** (≈4 chars/token); swap ``estimate_tokens``
for tiktoken/the embedder's tokenizer if exactness ever matters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

Kind = Literal["markdown", "code", "plaintext"]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
# Top-level-ish symbol starts across common languages (low indentation).
_SYMBOL_RE = re.compile(
    r"^[ \t]{0,4}("
    r"(?:async\s+)?def\s+\w+"                 # python
    r"|class\s+\w+"                            # python / ts / java
    r"|(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+\w+"  # js/ts
    r"|(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\("  # js arrow fns
    r"|(?:public|private|protected|static|func)\s+\w+"  # java/go/swift-ish
    r")"
)
_CODE_HINT_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rs", ".c", ".cpp", ".rb"}


@dataclass
class Chunk:
    text: str
    ordinal: int
    token_estimate: int
    metadata: dict[str, Any] = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Floor of 1 for non-empty."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def detect_kind(source_type: str | None, uri: str | None, text: str) -> Kind:
    """Best-effort content classification. ``source_type``/``uri`` win when
    informative; otherwise sniff the text."""
    st = (source_type or "").lower()
    if st in {"code", "repo_code"}:
        return "code"
    if st in {"prd", "research_output", "lesson", "repo_doc", "build_chat"}:
        return "markdown"
    if uri:
        low = uri.lower()
        if any(low.endswith(e) for e in _CODE_HINT_EXT):
            return "code"
        if low.endswith((".md", ".markdown")):
            return "markdown"
    # Sniff: a markdown heading near the top → markdown; many symbol lines → code.
    head_lines = text.splitlines()[:40]
    if any(_HEADING_RE.match(ln) for ln in head_lines):
        return "markdown"
    symbol_lines = sum(1 for ln in text.splitlines() if _SYMBOL_RE.match(ln))
    if symbol_lines >= 3:
        return "code"
    return "plaintext"


def chunk_document(
    text: str,
    *,
    source_type: str | None = None,
    uri: str | None = None,
    max_tokens: int = 1024,
    overlap_tokens: int = 80,
) -> list[Chunk]:
    """Chunk ``text`` into ~``max_tokens`` windows with ``overlap_tokens``
    of carry-over. Returns ordinal-stamped ``Chunk``s (empty list for empty
    input)."""
    if not text or not text.strip():
        return []
    kind = detect_kind(source_type, uri, text)
    if kind == "markdown":
        units = _split_markdown_units(text)
    elif kind == "code":
        units = _split_code_units(text)
    else:
        units = _split_plaintext_units(text)
    return _pack(units, kind=kind, max_tokens=max_tokens, overlap_tokens=overlap_tokens)


# ── Unit splitters → list of (text, meta) ───────────────────────────────


def _split_markdown_units(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Blocks separated by blank lines, each tagged with the breadcrumb of
    headings above it. Headings update the breadcrumb but don't emit their
    own unit (they prefix the following content)."""
    units: list[tuple[str, dict[str, Any]]] = []
    breadcrumb: list[str] = []  # (level, title) stack flattened to titles
    levels: list[int] = []
    buf: list[str] = []

    def flush() -> None:
        block = "\n".join(buf).strip()
        buf.clear()
        if block:
            crumb = " > ".join(breadcrumb)
            units.append((block, {"heading": crumb}))

    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            # pop deeper-or-equal levels, then push
            while levels and levels[-1] >= level:
                levels.pop()
                breadcrumb.pop()
            levels.append(level)
            breadcrumb.append(title)
        else:
            if line.strip() == "" and buf:
                flush()
            elif line.strip() != "":
                buf.append(line)
    flush()
    return units


def _split_code_units(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Split at top-level symbol boundaries; each symbol + its body is a
    unit. Leading module text (imports/header) becomes the first unit."""
    lines = text.splitlines()
    starts: list[int] = [i for i, ln in enumerate(lines) if _SYMBOL_RE.match(ln)]
    if not starts:
        return _split_plaintext_units(text)
    units: list[tuple[str, dict[str, Any]]] = []
    # preamble before the first symbol
    if starts[0] > 0:
        pre = "\n".join(lines[: starts[0]]).strip()
        if pre:
            units.append((pre, {"symbol": "<module>"}))
    bounds = starts + [len(lines)]
    for a, b in zip(bounds, bounds[1:], strict=False):
        block = "\n".join(lines[a:b]).rstrip()
        if block.strip():
            sym = _symbol_name(lines[a])
            units.append((block, {"symbol": sym}))
    return units


def _split_plaintext_units(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Paragraphs separated by blank lines."""
    paras = re.split(r"\n\s*\n", text)
    return [(p.strip(), {}) for p in paras if p.strip()]


def _symbol_name(line: str) -> str:
    m = re.search(r"\b(\w+)\s*[\(:=]", line)
    return m.group(1) if m else line.strip()[:40]


# ── Greedy packer (shared) ──────────────────────────────────────────────


def _pack(
    units: list[tuple[str, dict[str, Any]]],
    *,
    kind: Kind,
    max_tokens: int,
    overlap_tokens: int,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    cur_parts: list[str] = []
    cur_headings: list[str] = []
    cur_symbols: list[str] = []
    cur_tok = 0
    ordinal = 0

    def _meta() -> dict[str, Any]:
        m: dict[str, Any] = {"kind": kind}
        if cur_headings:
            m["headings"] = list(cur_headings)
            m["heading"] = cur_headings[-1]   # deepest/most-specific
        if cur_symbols:
            m["symbols"] = list(cur_symbols)
            m["symbol"] = cur_symbols[0]
        return m

    def _track(umeta: dict[str, Any]) -> None:
        h = umeta.get("heading")
        if h and h not in cur_headings:
            cur_headings.append(h)
        s = umeta.get("symbol")
        if s and s not in cur_symbols:
            cur_symbols.append(s)

    def emit() -> str:
        """Flush the current accumulator into a Chunk; return its overlap
        tail to seed the next chunk."""
        nonlocal cur_parts, cur_headings, cur_symbols, cur_tok, ordinal
        body = "\n\n".join(cur_parts).strip()
        if not body:
            cur_parts, cur_headings, cur_symbols, cur_tok = [], [], [], 0
            return ""
        chunks.append(Chunk(
            text=body, ordinal=ordinal, token_estimate=estimate_tokens(body),
            metadata=_meta(),
        ))
        ordinal += 1
        tail = _overlap_tail(body, overlap_tokens)
        cur_parts, cur_headings, cur_symbols, cur_tok = [], [], [], 0
        return tail

    for utext, umeta in units:
        utok = estimate_tokens(utext)
        # Oversized single unit → hard-split into windows.
        if utok > max_tokens:
            if cur_parts:
                emit()
            for piece in _window_split(utext, max_tokens, overlap_tokens):
                pm: dict[str, Any] = {"kind": kind}
                if umeta.get("heading"):
                    pm["heading"] = umeta["heading"]
                    pm["headings"] = [umeta["heading"]]
                if umeta.get("symbol"):
                    pm["symbol"] = umeta["symbol"]
                    pm["symbols"] = [umeta["symbol"]]
                chunks.append(Chunk(
                    text=piece, ordinal=ordinal,
                    token_estimate=estimate_tokens(piece), metadata=pm,
                ))
                ordinal += 1
            continue
        # Would overflow → flush, seed next with overlap tail.
        if cur_tok + utok > max_tokens and cur_parts:
            tail = emit()
            if tail:
                cur_parts.append(tail)
                cur_tok += estimate_tokens(tail)
        _track(umeta)
        cur_parts.append(utext)
        cur_tok += utok

    emit()
    return chunks


def _overlap_tail(text: str, overlap_tokens: int) -> str:
    """Return the trailing ~overlap_tokens of text (whole-word), for
    carry-over into the next chunk."""
    if overlap_tokens <= 0:
        return ""
    approx_chars = overlap_tokens * 4
    if len(text) <= approx_chars:
        return text
    tail = text[-approx_chars:]
    # snap to a word boundary
    sp = tail.find(" ")
    return tail[sp + 1:] if sp != -1 else tail


def _window_split(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Hard-split an oversized unit into overlapping char windows."""
    win = max_tokens * 4
    step = max(1, win - overlap_tokens * 4)
    out: list[str] = []
    i = 0
    while i < len(text):
        out.append(text[i:i + win].strip())
        i += step
    return [p for p in out if p]
