"""AC-052 — Council API tests (mock-mode happy path, validation, persistence, RBAC)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import council as council_route
from src.auth.service import get_current_user
from src.models.base import Document
from src.state.sqlite_store import SQLiteStateStore

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = SQLiteStateStore(db_path=str(Path(tmp) / "council_test.db"))
        await s.initialize()
        try:
            yield s
        finally:
            await s.close()


def _make_app(
    store,
    *,
    user_role: str = "admin",
    agent_mode: str = "mock",
    executor: Any = None,
) -> FastAPI:
    """Build a FastAPI test app with the council router and overridden deps."""
    from src.core.events import EventEmitter

    app = FastAPI()
    app.include_router(council_route.router)
    app.state.state_store = store
    app.state.agent_mode = agent_mode
    app.state.events = EventEmitter()

    # Override agent_executor when provided (real_llm path)
    if executor:
        app.state.agent_executor = executor

    # Override auth dependency
    def _user() -> dict[str, Any]:
        return {
            "sub": "u1",
            "username": "testuser",
            "role": user_role,
        }

    app.dependency_overrides[get_current_user] = _user

    # require_role has its own Depends(security_scheme) chain, which needs
    # app.state.auth_service. Provide a minimal mock so the dependency resolves.
    class _MockAuthService:
        def decode_token(self, token: str) -> dict[str, Any]:
            return {"sub": "u1", "username": "testuser", "role": user_role}

    app.state.auth_service = _MockAuthService()
    return app


# ── Validation tests (AC-011, AC-012) ────────────────────────────


async def test_invalid_agent_type_returns_400(store):
    client = TestClient(_make_app(store))
    r = client.post("/api/v1/council", json={
        "agent_type": "nonexistent",
        "content": "some code",
    })
    assert r.status_code == 400
    assert "Invalid agent_type" in r.text


async def test_empty_content_returns_400(store):
    client = TestClient(_make_app(store))
    r = client.post("/api/v1/council", json={
        "agent_type": "code_reviewer",
        "content": "   ",
    })
    assert r.status_code == 400
    assert "must not be empty" in r.text


async def test_oversize_content_returns_400(store):
    import os

    client = TestClient(_make_app(store))
    max_chars = int(os.getenv("COUNCIL_MAX_CONTENT_CHARS", "100000"))
    r = client.post("/api/v1/council", json={
        "agent_type": "code_reviewer",
        "content": "x" * (max_chars + 1),
    })
    assert r.status_code == 400
    assert "exceeds" in r.text


# ── Mock mode test (AC-013, AC-018) ──────────────────────────────


async def test_mock_mode_returns_labelled_result(store):
    """Mock mode → 200 with mock:true, nothing persisted."""
    client = TestClient(_make_app(store, agent_mode="mock"))
    r = client.post("/api/v1/council", json={
        "agent_type": "code_reviewer",
        "content": "function hello() { return 'world'; }",
        "language": "TypeScript",
        "focus_areas": ["Security", "Readability"],
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["mock"] is True
    assert "MOCK" in data["review_report"]
    assert data["agent_type"] == "code_reviewer"
    assert data["focus_areas"] == ["Security", "Readability"]
    assert data["council_id"].startswith("mock-")

    # Verify nothing was persisted (mock mode skips save_document)
    docs = await store.search_documents("council", limit=10)
    assert len(docs) == 0


# ── Happy path (AC-014) ──────────────────────────────────────────


async def test_happy_path_code_reviewer(store):
    """Real LLM path — executor returns a canned report; it gets persisted."""
    mock_executor = AsyncMock()
    mock_executor.single_agent_call = AsyncMock(return_value={
        "text": "## Code Review Report\n\n### Summary\nLooks good! **Verdict: APPROVED**",
        "input_tokens": 500,
        "output_tokens": 200,
        "model": "claude-opus-4-8",
    })

    client = TestClient(_make_app(store, agent_mode="real_llm", executor=mock_executor))
    r = client.post("/api/v1/council", json={
        "agent_type": "code_reviewer",
        "content": "function hello() { return 'world'; }",
        "language": "TypeScript",
        "focus_areas": ["Security"],
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["mock"] is False
    assert "APPROVED" in data["review_report"]
    assert data["agent_type"] == "code_reviewer"
    assert data["focus_areas"] == ["Security"]
    assert data["council_id"].startswith("doc-")
    assert "created_at" in data

    # Verify persistence
    doc = await store.get_document(data["council_id"])
    assert doc is not None
    assert doc.doc_type == "council_code_review"
    assert doc.agent_id == "code_reviewer"
    assert "council" in doc.tags
    assert "TypeScript" in doc.tags
    assert "Security" in doc.tags
    # Content should be the report, NOT the submitted source (AC-018)
    assert "APPROVED" in doc.content
    assert "hello" not in doc.content  # submitted code not persisted


async def test_happy_path_document_reviewer(store):
    """Document reviewer path — executor returns a canned report; persisted."""
    mock_executor = AsyncMock()
    mock_executor.single_agent_call = AsyncMock(return_value={
        "text": (
            "## Document Review Report\n\n### Summary\n"
            "Missing sections. **Verdict: CHANGES REQUESTED**"
        ),
        "input_tokens": 600,
        "output_tokens": 300,
        "model": "claude-opus-4-8",
    })

    client = TestClient(_make_app(store, agent_mode="real_llm", executor=mock_executor))
    r = client.post("/api/v1/council", json={
        "agent_type": "document_reviewer",
        "content": "# PRD: Some Feature\n\nJust build it.",
        "document_type": "PRD",
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["mock"] is False
    assert "CHANGES REQUESTED" in data["review_report"]
    assert data["agent_type"] == "document_reviewer"

    doc = await store.get_document(data["council_id"])
    assert doc.doc_type == "council_doc_review"
    assert "council" in doc.tags


async def test_agent_not_found_returns_502(store):
    """If executor returns an error, surface as 502."""
    mock_executor = AsyncMock()
    mock_executor.single_agent_call = AsyncMock(return_value={
        "text": "",
        "input_tokens": 0,
        "output_tokens": 0,
        "model": None,
        "error": "agent_not_found",
    })

    client = TestClient(_make_app(store, agent_mode="real_llm", executor=mock_executor))
    r = client.post("/api/v1/council", json={
        "agent_type": "code_reviewer",
        "content": "code",
    })
    assert r.status_code == 502


# ── List tests (AC-015) ──────────────────────────────────────────


async def test_list_returns_only_council_docs(store):
    """List returns council reviews only, newest first."""
    # Seed a council doc directly
    from datetime import datetime

    doc = Document(
        document_id="doc-aaa",
        request_id="",
        doc_type="council_code_review",
        title="Code Review — TS",
        content="## Report\n\nGreat code.",
        agent_id="code_reviewer",
        tags=["council", "code_reviewer", "TypeScript"],
        created_at=datetime(2026, 6, 26, 10, 0, 0),
    )
    await store.save_document(doc)

    doc2 = Document(
        document_id="doc-bbb",
        request_id="",
        doc_type="council_doc_review",
        title="Document Review — PRD",
        content="## Report\n\nMissing sections.",
        agent_id="document_reviewer",
        tags=["council", "document_reviewer", "PRD"],
        created_at=datetime(2026, 6, 26, 11, 0, 0),
    )
    await store.save_document(doc2)

    # Also seed a non-council doc to verify filtering
    non_council = Document(
        document_id="doc-zzz",
        request_id="req-1",
        doc_type="code_review",
        title="Regular Code Review",
        content="Some content",
        agent_id="code_reviewer",
        tags=["workflow"],
        created_at=datetime(2026, 6, 26, 12, 0, 0),
    )
    await store.save_document(non_council)

    client = TestClient(_make_app(store))
    r = client.get("/api/v1/council")
    assert r.status_code == 200
    items = r.json()["data"]
    # Should return only the two council docs
    assert len(items) == 2
    # Newest first
    assert items[0]["council_id"] == "doc-bbb"
    assert items[1]["council_id"] == "doc-aaa"
    # All items have preview
    assert "preview" in items[0]


async def test_list_filters_by_agent_type(store):
    """List with ?agent_type=code_reviewer returns only code reviews."""
    from datetime import datetime

    doc_code = Document(
        document_id="doc-code",
        request_id="",
        doc_type="council_code_review",
        title="CR",
        content="x",
        agent_id="code_reviewer",
        tags=["council", "code_reviewer"],
        created_at=datetime(2026, 6, 26, 10, 0, 0),
    )
    await store.save_document(doc_code)

    doc_doc = Document(
        document_id="doc-doc",
        request_id="",
        doc_type="council_doc_review",
        title="DR",
        content="y",
        agent_id="document_reviewer",
        tags=["council", "document_reviewer"],
        created_at=datetime(2026, 6, 26, 11, 0, 0),
    )
    await store.save_document(doc_doc)

    client = TestClient(_make_app(store))
    r = client.get("/api/v1/council?agent_type=code_reviewer")
    items = r.json()["data"]
    assert len(items) == 1
    assert items[0]["agent_type"] == "code_reviewer"


# ── Detail test (AC-016) ─────────────────────────────────────────


async def test_detail_404_on_unknown_id(store):
    client = TestClient(_make_app(store))
    r = client.get("/api/v1/council/nonexistent-id")
    assert r.status_code == 404


async def test_detail_404_on_non_council_doc(store):
    doc = Document(
        document_id="doc-non",
        request_id="req-1",
        doc_type="code_review",
        title="Regular Review",
        content="stuff",
        agent_id="code_reviewer",
    )
    await store.save_document(doc)
    client = TestClient(_make_app(store))
    r = client.get("/api/v1/council/doc-non")
    assert r.status_code == 404  # wrong doc_type


async def test_detail_returns_full_report(store):
    from datetime import datetime

    doc = Document(
        document_id="doc-full",
        request_id="",
        doc_type="council_code_review",
        title="My Review",
        content="## Full Report\n\nAll good.",
        agent_id="code_reviewer",
        tags=["council", "code_reviewer"],
        created_at=datetime(2026, 6, 26, 10, 0, 0),
    )
    await store.save_document(doc)

    client = TestClient(_make_app(store))
    r = client.get("/api/v1/council/doc-full")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["council_id"] == "doc-full"
    assert data["review_report"] == "## Full Report\n\nAll good."
    assert data["agent_type"] == "code_reviewer"


# ── Delete RBAC (AC-017) ─────────────────────────────────────────


async def test_delete_viewer_returns_403(store):
    """Viewer role cannot delete; gets 403 from require_role."""
    from datetime import datetime

    doc = Document(
        document_id="doc-del",
        request_id="",
        doc_type="council_code_review",
        title="To Delete",
        content="...",
        agent_id="code_reviewer",
        tags=["council"],
        created_at=datetime(2026, 6, 26, 10, 0, 0),
    )
    await store.save_document(doc)

    client = TestClient(_make_app(store, user_role="viewer"))
    r = client.delete(
        "/api/v1/council/doc-del",
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 403


async def test_delete_developer_succeeds(store):
    """Developer can delete → 200 with deleted:true."""
    from datetime import datetime

    doc = Document(
        document_id="doc-del-2",
        request_id="",
        doc_type="council_code_review",
        title="To Delete",
        content="...",
        agent_id="code_reviewer",
        tags=["council"],
        created_at=datetime(2026, 6, 26, 10, 0, 0),
    )
    await store.save_document(doc)

    client = TestClient(_make_app(store, user_role="developer"))
    r = client.delete(
        "/api/v1/council/doc-del-2",
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["deleted"] is True
    # Verify it's gone
    assert await store.get_document("doc-del-2") is None


async def test_delete_nonexistent_returns_404(store):
    client = TestClient(_make_app(store, user_role="developer"))
    r = client.delete(
        "/api/v1/council/nonexistent",
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 404


# ── Upload tests (AC-34) ──────────────────────────────────────────


async def test_upload_happy_path_txt(store):
    """Upload a .txt file → 200 + source_filename + persisted."""
    mock_executor = AsyncMock()
    mock_executor.single_agent_call = AsyncMock(return_value={
        "text": "## Code Review Report\n\n### Summary\nLooks good! **Verdict: APPROVED**",
        "input_tokens": 100, "output_tokens": 50, "model": "claude-opus-4-8",
    })

    client = TestClient(_make_app(store, agent_mode="real_llm", executor=mock_executor))
    r = client.post(
        "/api/v1/council/upload",
        files={"file": ("hello.py", b"print('hello world')", "text/plain")},
        data={
            "agent_type": "code_reviewer",
            "language": "Python",
            "focus_areas": '["Security"]',
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["mock"] is False
    assert data["source_filename"] == "hello.py"
    assert "APPROVED" in data["review_report"]
    assert data["council_id"].startswith("doc-")

    # Verify persistence + source_filename in tags
    doc = await store.get_document(data["council_id"])
    assert doc is not None
    assert doc.doc_type == "council_code_review"
    assert "source:hello.py" in doc.tags


async def test_upload_happy_path_md_document(store):
    """Upload a .md file with document_reviewer → 200 + source_filename."""
    mock_executor = AsyncMock()
    mock_executor.single_agent_call = AsyncMock(return_value={
        "text": (
            "## Document Review Report\n\n### Summary\n"
            "Needs work. **Verdict: CHANGES REQUESTED**"
        ),
        "input_tokens": 200, "output_tokens": 100, "model": "claude-opus-4-8",
    })

    client = TestClient(_make_app(store, agent_mode="real_llm", executor=mock_executor))
    r = client.post(
        "/api/v1/council/upload",
        files={"file": ("spec.md", b"# Spec\n\nJust build it.", "text/markdown")},
        data={
            "agent_type": "document_reviewer",
            "document_type": "Spec",
            "focus_areas": '["Readability"]',
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["source_filename"] == "spec.md"
    assert data["agent_type"] == "document_reviewer"

    doc = await store.get_document(data["council_id"])
    assert doc.doc_type == "council_doc_review"
    assert "source:spec.md" in doc.tags


async def test_upload_unsupported_type_returns_415(store):
    """Upload .exe → 415."""
    mock_executor = AsyncMock()
    mock_executor.single_agent_call = AsyncMock(return_value={
        "text": "ok", "input_tokens": 1, "output_tokens": 1, "model": "x",
    })
    client = TestClient(_make_app(store, agent_mode="real_llm", executor=mock_executor))
    r = client.post(
        "/api/v1/council/upload",
        files={"file": ("virus.exe", b"evil", "application/octet-stream")},
        data={"agent_type": "code_reviewer"},
    )
    assert r.status_code == 415


async def test_upload_oversize_returns_413(store):
    """Upload a file over the cap → 413."""
    mock_executor = AsyncMock()
    mock_executor.single_agent_call = AsyncMock(return_value={
        "text": "ok", "input_tokens": 1, "output_tokens": 1, "model": "x",
    })
    client = TestClient(_make_app(store, agent_mode="real_llm", executor=mock_executor))

    big = b"x" * (25 * 1024 * 1024 + 1)  # 25 MB + 1 byte
    r = client.post(
        "/api/v1/council/upload",
        files={"file": ("big.txt", big, "text/plain")},
        data={"agent_type": "code_reviewer"},
    )
    assert r.status_code == 413


async def test_upload_empty_extraction_returns_400(store):
    """Upload whitespace-only file → 400 (no extractable text)."""
    mock_executor = AsyncMock()
    mock_executor.single_agent_call = AsyncMock(return_value={
        "text": "ok", "input_tokens": 1, "output_tokens": 1, "model": "x",
    })
    client = TestClient(_make_app(store, agent_mode="real_llm", executor=mock_executor))
    r = client.post(
        "/api/v1/council/upload",
        files={"file": ("empty.md", b"   \n  \n  ", "text/markdown")},
        data={"agent_type": "code_reviewer"},
    )
    assert r.status_code == 400


async def test_upload_mock_mode_returns_labelled_placeholder(store):
    """Upload in mock mode → 200 with mock:true, source_filename, NOT persisted."""
    client = TestClient(_make_app(store, agent_mode="mock"))
    r = client.post(
        "/api/v1/council/upload",
        files={"file": ("doc.txt", b"Some document content to review", "text/plain")},
        data={
            "agent_type": "document_reviewer",
            "document_type": "PRD",
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["mock"] is True
    assert "MOCK" in data["review_report"]
    assert data["source_filename"] == "doc.txt"

    # Nothing persisted in mock mode
    docs = await store.search_documents("council", limit=10)
    assert len(docs) == 0


async def test_upload_missing_agent_type_validation(store):
    """Invalid agent_type in multipart → 400 (our validation)."""
    client = TestClient(_make_app(store))
    r = client.post(
        "/api/v1/council/upload",
        files={"file": ("test.txt", b"hello", "text/plain")},
        data={"agent_type": "invalid"},
    )
    assert r.status_code == 400
