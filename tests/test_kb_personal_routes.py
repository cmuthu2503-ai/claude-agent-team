"""KB-PL — personal knowledge library route tests (ingest-url / ingest-text /
search).

Mirrors test_knowledge_route.py: a TestClient over the real router with an
in-memory fake subsystem on app.state, auth + RBAC bypassed via
dependency_overrides. Pins the personal-library contracts:

  - ingest-url fetches via the adapter, ingests into the personal namespace
  - ingest-text (paste path) ingests with caller title + source link
  - auto_approve flows through to the pipeline
  - search returns doc-level de-duplicated results carrying source links
  - writes 503 / reads soft-fail when the KB is down
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.knowledge.web_ingest as web_ingest
from src.api.routes import knowledge as kb_route
from src.auth.service import get_current_user
from src.knowledge.web_ingest import FetchedArticle


class _FakeEvents:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []

    async def emit(self, name: str, payload: dict) -> None:
        self.emitted.append((name, payload))


class _FakePipeline:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def ingest_text(  # noqa: ANN001
        self, *, text, title, source_type, namespace, bucket_ids=None,
        uri=None, created_by="x", sensitivity="normal", project_id=None,
        auto_approve=False,
    ):
        self.calls.append({
            "text": text, "title": title, "source_type": source_type,
            "namespace": namespace, "bucket_ids": list(bucket_ids or []),
            "uri": uri, "auto_approve": auto_approve,
        })
        return SimpleNamespace(
            doc_id="doc-pl", status="approved" if auto_approve else "pending",
            skipped=False, chunks=2, sensitivity="normal", pii_findings=[],
        )


class _FakeRetriever:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks
        self.calls: list[dict] = []

    async def retrieve(self, query, namespace, *, bucket_ids=None,  # noqa: ANN001
                       agent_id="", request_id=None, top_k=None, rerank=None):
        self.calls.append({
            "query": query, "namespace": namespace,
            "bucket_ids": bucket_ids, "agent_id": agent_id, "top_k": top_k,
        })
        return self._chunks


def _chunk(doc_id, title, text, score, uri):  # noqa: ANN001
    return SimpleNamespace(
        chunk_id=f"chk-{doc_id}-{score}", doc_id=doc_id, title=title, text=text,
        score=score, uri=uri, namespace="kb_personal", metadata={},
    )


def _make_subsystem(available: bool = True, *, auto_approve: bool = False,
                    chunks: list[Any] | None = None) -> Any:
    settings = SimpleNamespace(
        platform_namespace="kb_platform",
        personal_namespace="kb_personal",
        personal_auto_approve=auto_approve,
        embed_model="bge-small-en-v1.5", rerank_enabled=False,
        project_namespace=lambda p: f"kb_project_{p}",
        memory_namespace=lambda p: f"mem_project_{p}",
    )
    return SimpleNamespace(
        available=available, reason="ok" if available else "down",
        settings=settings, knowledge_store=MagicMock(),
        pipeline=_FakePipeline(),
        retriever=_FakeRetriever(chunks or []),
    )


def _make_app(subsystem: Any, role: str = "developer") -> FastAPI:
    app = FastAPI()
    app.include_router(kb_route.router)
    app.state.kb_subsystem = subsystem
    app.state.events = _FakeEvents()
    # Robust auth bypass (version-independent): supply credentials via the
    # security scheme + a fake auth_service that decodes them to our user. This
    # satisfies both get_current_user and the require_role role_checker without
    # walking route dependants.
    from fastapi.security import HTTPAuthorizationCredentials

    from src.auth.service import security_scheme

    payload = {"sub": "u1", "username": "tester", "role": role, "kb_curator_scopes": []}
    app.state.auth_service = SimpleNamespace(decode_token=lambda tok: payload)

    def _creds() -> HTTPAuthorizationCredentials:
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")

    app.dependency_overrides[security_scheme] = _creds
    app.dependency_overrides[get_current_user] = lambda: payload
    return app


# ── ingest-url ───────────────────────────────────────────────────────────────


def test_ingest_url_fetches_and_ingests_into_personal_ns(monkeypatch):
    sub = _make_subsystem(True, auto_approve=True)
    app = _make_app(sub)

    async def _fake_fetch(url: str, **_):
        return FetchedArticle(
            text="# Agentic AI in Banking\n\nMulti-agent underwriting.",
            title="Agentic AI in Banking", url=url,
            metadata={"fetched_via": "firecrawl"},
        )

    monkeypatch.setattr(web_ingest, "fetch_article", _fake_fetch)

    r = TestClient(app).post(
        "/api/v1/knowledge/ingest-url",
        json={"url": "https://example.com/x", "bucket_ids": ["b-agentic"]},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["doc_id"] == "doc-pl"
    assert data["status"] == "approved"        # auto_approve default flowed through
    assert data["uri"] == "https://example.com/x"
    # ingest landed in the personal namespace, source_type web, tagged bucket
    call = sub.pipeline.calls[0]
    assert call["namespace"] == "kb_personal"
    assert call["source_type"] == "web"
    assert call["bucket_ids"] == ["b-agentic"]
    assert call["auto_approve"] is True


def test_ingest_url_502_when_fetch_fails(monkeypatch):
    sub = _make_subsystem(True)
    app = _make_app(sub)

    async def _boom(url: str, **_):
        raise web_ingest.ArticleFetchError("403 forbidden")

    monkeypatch.setattr(web_ingest, "fetch_article", _boom)
    r = TestClient(app).post(
        "/api/v1/knowledge/ingest-url", json={"url": "https://example.com/x"}
    )
    assert r.status_code == 502


def test_ingest_url_503_when_down():
    app = _make_app(_make_subsystem(False))
    r = TestClient(app).post(
        "/api/v1/knowledge/ingest-url", json={"url": "https://example.com/x"}
    )
    assert r.status_code == 503


# ── ingest-text (paste / LinkedIn path) ──────────────────────────────────────


def test_ingest_text_paste_path():
    sub = _make_subsystem(True)
    app = _make_app(sub)
    r = TestClient(app).post("/api/v1/knowledge/ingest-text", json={
        "text": "Pasted LinkedIn post about agentic architectures.",
        "title": "LinkedIn: Agentic Patterns",
        "source_url": "https://linkedin.com/posts/abc",
        "bucket_ids": ["b-agentic"],
        "auto_approve": True,
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "approved"
    call = sub.pipeline.calls[0]
    assert call["source_type"] == "paste"
    assert call["namespace"] == "kb_personal"
    assert call["uri"] == "https://linkedin.com/posts/abc"
    assert call["auto_approve"] is True


def test_ingest_text_requires_text_and_title():
    sub = _make_subsystem(True)
    app = _make_app(sub)
    client = TestClient(app)
    assert client.post("/api/v1/knowledge/ingest-text",
                       json={"text": "", "title": "x"}).status_code == 400
    assert client.post("/api/v1/knowledge/ingest-text",
                       json={"text": "body", "title": "  "}).status_code == 400


def test_ingest_text_respects_default_auto_approve_false():
    sub = _make_subsystem(True, auto_approve=False)
    app = _make_app(sub)
    r = TestClient(app).post("/api/v1/knowledge/ingest-text", json={
        "text": "body", "title": "T",
    })
    assert r.json()["data"]["status"] == "pending"  # team-safe default preserved


# ── search ────────────────────────────────────────────────────────────────────


def test_search_returns_doc_level_deduped_results_with_links():
    # Two chunks of doc-A (one higher score) + one chunk of doc-B.
    chunks = [
        _chunk("doc-A", "Loan Underwriting Agents", "multi-agent underwriting", 0.91,
               "https://example.com/a"),
        _chunk("doc-A", "Loan Underwriting Agents", "second chunk of A", 0.40,
               "https://example.com/a"),
        _chunk("doc-B", "Banking Compliance", "kyc and aml controls", 0.55,
               "https://example.com/b"),
    ]
    sub = _make_subsystem(True, chunks=chunks)
    app = _make_app(sub, role="viewer")
    r = TestClient(app).post("/api/v1/knowledge/search", json={
        "query": "Agentic AI Architecture in Banking", "bucket_ids": ["b-agentic"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    results = body["data"]
    # de-duplicated to 2 distinct docs
    assert len(results) == 2
    assert {x["doc_id"] for x in results} == {"doc-A", "doc-B"}
    # ranked by best score: doc-A first, carrying its source link
    assert results[0]["doc_id"] == "doc-A"
    assert results[0]["uri"] == "https://example.com/a"
    assert results[0]["more_matches"] == 1   # the second A chunk counted
    # search hit the personal namespace + passed the bucket scope
    assert sub.retriever.calls[0]["namespace"] == "kb_personal"
    assert sub.retriever.calls[0]["bucket_ids"] == ["b-agentic"]


def test_search_soft_fails_when_down():
    app = _make_app(_make_subsystem(False))
    body = TestClient(app).post("/api/v1/knowledge/search", json={"query": "x"}).json()
    assert body["meta"]["kb_available"] is False
    assert body["data"] == []


def test_search_requires_query():
    sub = _make_subsystem(True)
    app = _make_app(sub)
    r = TestClient(app).post("/api/v1/knowledge/search", json={"query": "  "})
    assert r.status_code == 400
