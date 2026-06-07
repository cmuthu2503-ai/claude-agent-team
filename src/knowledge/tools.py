"""Agent-facing knowledge tools (KB-08).

Two tools the agent calls in its ReAct loop:
  - ``knowledge_search(query)`` — hybrid, bucket-scoped retrieval.
  - ``knowledge_get(doc_id)``   — pull a full document after a search hit.

**The grounding guarantee (FR-023):** neither tool's input schema exposes a
bucket / namespace / scope parameter. The agent only supplies ``query`` /
``doc_id``. The scope (namespace + bucket_ids) is the ``kb_scope`` the
executor injects from the Request via the tool registry — so the agent
*cannot widen* what the Request granted. Same property as filesystem
``project_root`` injection.

Registration is deferred to KB-10 (when the subsystem is on ``app.state``);
``register_knowledge_tools`` is the helper main.py's lifespan will call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()

# Cap how much a single citation drawer / get returns into the prompt.
_MAX_SNIPPET_CHARS = 700
_MAX_DOC_CHARS = 6000


@dataclass
class KbScope:
    """The grounding scope injected from the Request. The agent never sets
    this — the executor derives it (KB-09/15) and the registry threads it.

    ``namespace`` is the **facts** scope (citeable). ``craft_namespace`` (KB-17)
    is an optional secondary scope for **craft** — format/method/tone guidance
    that is retrieved to inform the agent but is NEVER citeable as a substantive
    fact (§5.1). For a project Request under ``scope=auto`` it's the platform
    namespace; otherwise None."""

    namespace: str = "kb_platform"
    bucket_ids: list[str] = field(default_factory=list)
    agent_id: str = ""
    request_id: str | None = None
    craft_namespace: str | None = None
    # KB-19 — True when ``namespace`` is a per-project namespace (a real app),
    # so the agent flow can detect a SPARSE app KB and degrade gracefully.
    is_project: bool = False
    # KB-20 — the project this work belongs to (for the decision ledger).
    project_id: str | None = None
    # KB-24/25 — the episodic-memory namespace (``mem_project_<id>``) this work
    # can recall from. None for platform-only Requests (no per-app memory).
    memory_namespace: str | None = None
    # KB-32 — per-Request retrieval budget: max knowledge_search calls this
    # agent may make for this Request (from the agent's YAML retrieval config).
    # None / 0 = unlimited.
    max_searches: int | None = None


def _snippet(text: str, n: int = _MAX_SNIPPET_CHARS) -> str:
    t = text.strip().replace("\r", "")
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + " …"


class KnowledgeSearchTool:
    """Hybrid bucket-scoped retrieval, formatted for the agent prompt with
    citation pointers ([KB#chunk_id] · doc title)."""

    name = "knowledge_search"

    def __init__(self, retriever: Any) -> None:
        self._retriever = retriever
        # KB-32 — per-(request, agent) search counter enforcing max_searches.
        # Bounded so a long-running process can't leak unbounded keys.
        self._counts: dict[tuple[str, str], int] = {}

    def _over_budget(self, scope: KbScope) -> bool:
        """Count this search against the Request's budget; True if it should be
        refused (already at ``max_searches``)."""
        budget = scope.max_searches or 0
        if budget <= 0 or not scope.request_id:
            return False  # unlimited / no request context
        key = (scope.request_id, scope.agent_id)
        used = self._counts.get(key, 0)
        if used >= budget:
            return True
        if len(self._counts) > 5000:  # cheap leak guard
            self._counts.clear()
        self._counts[key] = used + 1
        return False

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Search the knowledge base for relevant, grounded information. "
                "Returns ranked snippets with citation ids. The search is "
                "automatically scoped to the buckets this task is grounded in — "
                "you do not (and cannot) choose the scope."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look up."},
                    "top_k": {
                        "type": "integer",
                        "description": "Max results (optional; default 8).",
                    },
                },
                "required": ["query"],
            },
        }

    async def execute(
        self, params: dict[str, Any], *, kb_scope: KbScope | None = None, **_: Any
    ) -> str:
        if self._retriever is None:
            return "Knowledge base is unavailable; proceed without retrieval."
        query = str(params.get("query", "")).strip()
        if not query:
            return "knowledge_search requires a non-empty 'query'."
        scope = kb_scope or KbScope()
        # KB-32 — enforce the per-Request retrieval budget. When exhausted, tell
        # the agent to synthesize from what it already retrieved rather than
        # burning more embedding/LLM cost on additional searches.
        if self._over_budget(scope):
            return (
                f"Retrieval budget reached ({scope.max_searches} searches) for this "
                "task. Synthesize your answer from the results you already have; "
                "if a claim still can't be grounded, flag it rather than searching more."
            )
        top_k = int(params.get("top_k") or 0) or None
        try:
            hits = await self._retriever.retrieve(
                query, scope.namespace,
                bucket_ids=scope.bucket_ids or None,
                agent_id=scope.agent_id, request_id=scope.request_id, top_k=top_k,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("knowledge_search_failed", error=str(e))
            return f"knowledge_search error: {e}"
        if not hits:
            scope_txt = (
                f"bucket(s) {', '.join(scope.bucket_ids)}" if scope.bucket_ids
                else f"namespace {scope.namespace}"
            )
            return (
                f"No results in {scope_txt} for '{query}'. "
                f"If a claim can't be grounded here, flag it rather than asserting."
            )
        lines = [f"{len(hits)} result(s) for '{query}':\n"]
        for h in hits:
            src = h.title or h.uri or h.doc_id
            lines.append(
                f"[KB#{h.chunk_id}] (doc: {src}, score {h.score:.2f})\n"
                f"{_snippet(h.text)}\n"
            )
        lines.append(
            "Cite sources as [KB#<chunk_id>]; call knowledge_get(<doc_id>) for "
            "full context. doc_ids: " + ", ".join(sorted({h.doc_id for h in hits}))
        )
        return "\n".join(lines)


class KnowledgeGetTool:
    """Fetch a full document after a search hit. Scoped: only returns a doc in
    the request's namespace (so a doc_id from another tenant can't be pulled)."""

    name = "knowledge_get"

    def __init__(self, store: Any) -> None:
        self._store = store

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Fetch the full text of a knowledge-base document by its doc_id "
                "(from a knowledge_search result) for deeper context."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Document id to fetch."}
                },
                "required": ["doc_id"],
            },
        }

    async def execute(
        self, params: dict[str, Any], *, kb_scope: KbScope | None = None, **_: Any
    ) -> str:
        if self._store is None:
            return "Knowledge base is unavailable."
        doc_id = str(params.get("doc_id", "")).strip()
        if not doc_id:
            return "knowledge_get requires a 'doc_id'."
        scope = kb_scope or KbScope()
        try:
            doc = await self._store.get_document_full(doc_id)
        except Exception as e:  # noqa: BLE001
            return f"knowledge_get error: {e}"
        if not doc:
            return f"No document '{doc_id}' found."
        # Namespace isolation: never return a doc outside the request's scope.
        if doc["namespace"] != scope.namespace:
            logger.warning(
                "knowledge_get_cross_namespace_blocked",
                doc_id=doc_id, doc_ns=doc["namespace"], scope_ns=scope.namespace,
            )
            return f"Document '{doc_id}' is not in scope for this task."
        if doc["status"] != "approved":
            return f"Document '{doc_id}' is not approved for retrieval."
        text = doc["text"]
        if len(text) > _MAX_DOC_CHARS:
            text = text[:_MAX_DOC_CHARS] + "\n\n[… truncated]"
        return f"# {doc['title']}\n(source: {doc.get('uri') or doc_id})\n\n{text}"


class RecordDecisionTool:
    """KB-20 — let an agent record a decision into the append-only
    ``decision_ledger``: its conclusion + the chunks it relied on. This is the
    provenance spine — months later you can answer "why did the agent conclude
    this?" The request/agent/project are injected from ``kb_scope`` (the agent
    can't spoof whose decision this is)."""

    name = "record_decision"

    def __init__(self, store: Any) -> None:
        self._store = store

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Record your conclusion + the sources it rests on into the "
                "decision ledger (provenance). Call this once you've reached a "
                "substantive conclusion. Pass the chunk ids you actually relied "
                "on (from knowledge_search results) so the reasoning is "
                "traceable."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Your conclusion / decision in 1-3 sentences.",
                    },
                    "cited_chunk_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Chunk ids ([KB#id]) that justify the conclusion.",
                    },
                },
                "required": ["summary"],
            },
        }

    async def execute(
        self, params: dict[str, Any], *, kb_scope: KbScope | None = None, **_: Any
    ) -> str:
        if self._store is None:
            return "Decision ledger unavailable."
        summary = str(params.get("summary", "")).strip()
        if not summary:
            return "record_decision requires a non-empty 'summary'."
        scope = kb_scope or KbScope()
        if not scope.request_id:
            return "record_decision is only available within a request."
        cited = params.get("cited_chunk_ids") or []
        cited = [str(c) for c in cited] if isinstance(cited, list) else []
        try:
            decision_id = await self._store.record_decision(
                request_id=scope.request_id, agent_id=scope.agent_id,
                summary=summary[:2000], project_id=scope.project_id,
                retrieved_chunk_ids=cited,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("record_decision_failed", error=str(e))
            return f"record_decision error: {e}"
        return f"Decision recorded ({decision_id}) citing {len(cited)} source(s)."


class KnowledgeCiteTool:
    """KB-21 — the mechanical enforcement of the §5.1 grounding rule. An agent
    cites the Knowledge chunks that back a substantive claim; this records them
    into the retrieval audit (so they surface in the Grounding Report) and
    emits footnotes. **Only Knowledge is citeable** — a memory id, a fabricated
    id, or a chunk from another namespace is REJECTED, not silently accepted."""

    name = "knowledge_cite"

    def __init__(self, store: Any) -> None:
        self._store = store

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Cite the knowledge chunk(s) that support a substantive claim. "
                "Pass the [KB#id] ids from knowledge_search results. Records the "
                "citation (for the audit/grounding report) and returns footnotes. "
                "Only Knowledge chunks are citeable — memory/recall ids and "
                "anything outside this task's scope are rejected."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "chunk_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Knowledge chunk ids ([KB#id]) backing the claim.",
                    },
                },
                "required": ["chunk_ids"],
            },
        }

    async def execute(
        self, params: dict[str, Any], *, kb_scope: KbScope | None = None, **_: Any
    ) -> str:
        if self._store is None:
            return "Knowledge base unavailable; cannot record citations."
        raw = params.get("chunk_ids") or []
        ids = [str(c).strip() for c in raw if str(c).strip()] if isinstance(raw, list) else []
        if not ids:
            return "knowledge_cite requires a non-empty 'chunk_ids' array."
        scope = kb_scope or KbScope()

        # 1. Memory/recall ids are never citeable as fact (§5.1) — reject by id
        #    shape before touching the store.
        rejected_memory = [i for i in ids if i.lower().startswith(("mem-", "mem_", "memory"))]
        candidates = [i for i in ids if i not in rejected_memory]

        # 2. Resolve against real Knowledge chunks, scoped to THIS task's
        #    namespace (can't cite another app's chunk).
        hydrated = await self._store.get_chunks_by_ids(candidates) if candidates else {}
        accepted: list[str] = []
        out_of_scope: list[str] = []
        unknown: list[str] = []
        for cid in candidates:
            row = hydrated.get(cid)
            if row is None:
                unknown.append(cid)
            elif row.get("namespace") != scope.namespace:
                out_of_scope.append(cid)
            else:
                accepted.append(cid)

        if accepted:
            try:
                await self._store.record_retrieval(
                    agent_id=scope.agent_id, namespace=scope.namespace,
                    query="[citation]", request_id=scope.request_id,
                    bucket_ids=scope.bucket_ids or None,
                    returned_chunk_ids=accepted, cited_chunk_ids=accepted,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("knowledge_cite_record_failed", error=str(e))

        lines: list[str] = []
        if accepted:
            lines.append(f"Recorded {len(accepted)} citation(s). Footnotes:")
            for cid in accepted:
                src = hydrated[cid].get("title") or hydrated[cid].get("uri") or cid
                lines.append(f"  [KB#{cid}] — {src}")
        rejected = rejected_memory + out_of_scope + unknown
        if rejected:
            lines.append(
                "REJECTED (not citeable as fact): " + ", ".join(rejected) + ". "
                "Memory/recall is never citeable; cite only Knowledge chunks in "
                "this task's scope, or flag the claim as ungrounded."
            )
        return "\n".join(lines) if lines else "No valid citations."


class RecallMemoryTool:
    """KB-25 — time-aware episodic recall. Lets an agent ask "what did we try /
    decide / see for this app before, and how did it turn out?" over the
    project's ``mem_project_<id>`` namespace.

    **Never citeable as fact (§5.1).** Every result is tagged
    ``[MEMORY · unvetted]`` and the tool says so — episodic memory informs the
    agent's reasoning but cannot back a substantive claim (only Knowledge can,
    via ``knowledge_cite``). The namespace is injected from ``kb_scope`` (the
    agent can't recall another app's memory). Supports ``days`` ("last N days")
    and ``as_of`` (point-in-time: "as of 2026-03-01")."""

    name = "recall_memory"

    def __init__(self, store: Any, embedder: Any) -> None:
        self._store = store
        self._embedder = embedder

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Recall this application's PAST EXPERIENCE — prior attempts, "
                "outcomes, and discussions (episodic memory). Use it to avoid "
                "repeating mistakes or to reconstruct what happened earlier. "
                "Results are UNVETTED context tagged [MEMORY] and are NEVER "
                "citeable as fact — do not cite them; cite only knowledge_search "
                "results. Optional time filters: 'days' (last N days) and "
                "'as_of' (point-in-time, e.g. '2026-03-01')."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What experience to recall."},
                    "days": {
                        "type": "integer",
                        "description": "Only recall episodes from the last N days (optional).",
                    },
                    "as_of": {
                        "type": "string",
                        "description": "Only recall episodes at-or-before this date (optional).",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Max results (optional; default 8).",
                    },
                },
                "required": ["query"],
            },
        }

    async def execute(
        self, params: dict[str, Any], *, kb_scope: KbScope | None = None, **_: Any
    ) -> str:
        if self._store is None:
            return "Episodic memory is unavailable; proceed without recall."
        scope = kb_scope or KbScope()
        if not scope.memory_namespace:
            return (
                "No episodic memory for this task (it isn't scoped to an "
                "application). Proceed without recall."
            )
        query = str(params.get("query", "")).strip()
        if not query:
            return "recall_memory requires a non-empty 'query'."
        raw_days = params.get("days")
        days = (
            int(raw_days)
            if isinstance(raw_days, int)
            or (isinstance(raw_days, str) and raw_days.isdigit())
            else None
        )
        as_of = str(params.get("as_of")).strip() if params.get("as_of") else None
        top_k = int(params.get("top_k") or 0) or 8

        # Embed the query (best-effort). If embedding is unavailable, recall
        # degrades to recency-ordered within the time window — still useful.
        query_embedding: list[float] | None = None
        if self._embedder is not None:
            try:
                query_embedding = await self._embedder.embed_query(query)
            except Exception as e:  # noqa: BLE001
                logger.warning("recall_memory_embed_failed", error=str(e))

        try:
            hits = await self._store.search_memory(
                scope.memory_namespace, query_embedding=query_embedding,
                days=days, as_of=as_of, limit=top_k,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("recall_memory_failed", error=str(e))
            return f"recall_memory error: {e}"

        if not hits:
            window = []
            if days:
                window.append(f"last {days} day(s)")
            if as_of:
                window.append(f"as of {as_of}")
            wtxt = f" ({', '.join(window)})" if window else ""
            return f"No prior experience recalled for '{query}'{wtxt}."

        lines = [
            f"{len(hits)} recalled episode(s) for '{query}' "
            "— [MEMORY · unvetted, NOT citeable as fact]:\n"
        ]
        for h in hits:
            when = (h.get("created_at") or "")[:10]
            outcome = h.get("outcome") or "?"
            lines.append(
                f"[MEMORY · {h.get('kind', 'episode')} · {outcome} · {when}]\n"
                f"{_snippet(h.get('text', ''))}\n"
            )
        lines.append(
            "Use this to inform your reasoning only. Do NOT cite [MEMORY] items; "
            "ground substantive claims in knowledge_search results instead."
        )
        return "\n".join(lines)


def register_knowledge_tools(tool_registry: Any, subsystem: Any) -> bool:
    """Register the knowledge tools into a ToolRegistry from a built
    subsystem. Returns True if registered (subsystem available), False if it
    soft-failed (KB unavailable → tools not registered, agents degrade).
    Called from main.py lifespan (KB-10)."""
    if subsystem is None or not getattr(subsystem, "available", False):
        logger.info("knowledge_tools_skipped", reason="subsystem unavailable")
        return False
    tool_registry.register_implementation(
        "knowledge_search", KnowledgeSearchTool(subsystem.retriever)
    )
    tool_registry.register_implementation(
        "knowledge_get", KnowledgeGetTool(subsystem.knowledge_store)
    )
    tool_registry.register_implementation(
        "record_decision", RecordDecisionTool(subsystem.knowledge_store)
    )
    tool_registry.register_implementation(
        "knowledge_cite", KnowledgeCiteTool(subsystem.knowledge_store)
    )
    # KB-25 — time-aware episodic recall (mem_project_<id>). Needs the embedder
    # for semantic recall; degrades to recency-ordered if it's absent.
    tool_registry.register_implementation(
        "recall_memory",
        RecallMemoryTool(subsystem.knowledge_store, getattr(subsystem, "embedder", None)),
    )
    logger.info("knowledge_tools_registered")
    return True


__all__ = [
    "KbScope",
    "KnowledgeSearchTool",
    "KnowledgeGetTool",
    "RecordDecisionTool",
    "KnowledgeCiteTool",
    "RecallMemoryTool",
    "register_knowledge_tools",
]
