"""KB-10 — /api/v1/knowledge route contract tests.

TestClient against a minimal app mounting the real knowledge router + a fake
in-memory subsystem on app.state. Auth is bypassed via dependency_overrides
(no JWT round-trip); the admin/developer gates are overridden per-test by
walking the route's `role_checker` dependencies (same trick as the models
route tests).

Pinned contracts:
  - reads soft-fail to empty + meta.kb_available=False when KB is down
  - writes 503 when KB is down (never silently no-op)
  - create/delete bucket honour developer/admin RBAC
  - upload ingests via the pipeline and tags buckets
  - the grounding report hydrates cited chunks
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import knowledge as kb_route
from src.auth.service import get_current_user

# ── Fakes ──────────────────────────────────────────────────────────────────


class _FakeEvents:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []

    async def emit(self, name: str, payload: dict) -> None:
        self.emitted.append((name, payload))


class _FakeStore:
    """In-memory stand-in for KnowledgeStore — only the methods the routes call."""

    def __init__(self) -> None:
        self.buckets: dict[str, Any] = {}
        self.docs: dict[str, Any] = {}
        self.membership: dict[str, list[str]] = {}
        self.audit: list[dict] = []
        self.chunks: dict[str, dict] = {}

    async def list_documents(self, ns, status=None, limit=200):  # noqa: ANN001
        out = list(self.docs.values())
        if status:
            out = [d for d in out if d.status == status]
        return out

    async def get_document_buckets_bulk(self, doc_ids):  # noqa: ANN001
        return {d: self.membership.get(d, []) for d in doc_ids if d in self.membership}

    async def get_chunk_counts_bulk(self, doc_ids):  # noqa: ANN001
        return {d: getattr(self, "chunk_counts", {}).get(d, 0) for d in doc_ids}

    async def get_document(self, doc_id):  # noqa: ANN001
        return self.docs.get(doc_id)

    async def set_document_status(self, doc_id, status, curated_by=None):  # noqa: ANN001
        d = self.docs.get(doc_id)
        if not d:
            return False
        d.status = status
        return True

    async def purge_document(self, doc_id):  # noqa: ANN001
        return self.docs.pop(doc_id, None) is not None

    async def set_document_buckets(self, doc_id, bucket_ids):  # noqa: ANN001
        self.membership[doc_id] = list(bucket_ids)

    async def list_buckets(self, project_id=None):  # noqa: ANN001
        if project_id is None:
            return list(self.buckets.values())
        return [b for b in self.buckets.values() if getattr(b, "project_id", None) == project_id]

    async def get_bucket(self, bucket_id):  # noqa: ANN001
        return self.buckets.get(bucket_id)

    async def create_bucket(self, name, description="", created_by="system"):  # noqa: ANN001
        bid = f"b-{len(self.buckets) + 1}"
        b = SimpleNamespace(
            bucket_id=bid, name=name, slug=name.lower(), description=description,
            project_id=None, is_system=False, created_by=created_by,
            created_at=None, doc_count=0, chunk_count=0,
        )
        self.buckets[bid] = b
        return b

    async def rename_bucket(self, bucket_id, name, description=None):  # noqa: ANN001
        b = self.buckets.get(bucket_id)
        if not b:
            return False
        b.name = name
        if description is not None:
            b.description = description
        return True

    async def delete_bucket(self, bucket_id):  # noqa: ANN001
        return self.buckets.pop(bucket_id, None) is not None

    async def provision_project(self, project_id, namespace, name="App Knowledge"):  # noqa: ANN001
        bid = f"pb-{project_id}"
        b = SimpleNamespace(bucket_id=bid, name=name, slug=bid, description="",
                            project_id=project_id, is_system=True, created_by="system",
                            created_at=None, doc_count=0, chunk_count=0)
        self.buckets[bid] = b
        return b

    async def list_retrieval_audit(self, request_id, limit=200):  # noqa: ANN001
        return [a for a in self.audit if a["request_id"] == request_id]

    async def list_decisions(self, request_id, limit=200):  # noqa: ANN001
        return [d for d in getattr(self, "decisions_log", []) if d["request_id"] == request_id]

    async def get_chunks_by_ids(self, chunk_ids):  # noqa: ANN001
        return {c: self.chunks[c] for c in chunk_ids if c in self.chunks}

    # KB-28 — promotion gate fakes (provision_project already defined above).
    async def list_promotion_candidates(self, namespace=None, status="pending", limit=100):  # noqa: ANN001
        out = [c for c in getattr(self, "promos", {}).values() if c["status"] == status]
        if namespace:
            out = [c for c in out if c["namespace"] == namespace]
        return out

    async def get_promotion_candidate(self, candidate_id):  # noqa: ANN001
        return getattr(self, "promos", {}).get(candidate_id)

    async def set_promotion_status(self, candidate_id, status, reviewed_by=None):  # noqa: ANN001
        c = getattr(self, "promos", {}).get(candidate_id)
        if not c or c["status"] != "pending":
            return False
        c["status"] = status
        return True

    # KB-30 — retention/forgetting fakes.
    async def forget_subject(self, subject, namespace=None, actor="system"):  # noqa: ANN001
        self.forgotten = getattr(self, "forgotten", [])
        self.forgotten.append({"subject": subject, "namespace": namespace, "actor": actor})
        return {"memory": 2, "documents": 1}

    async def list_retention_audit(self, limit=100):  # noqa: ANN001
        return getattr(self, "retention_audit", [])

    # KB-31 — feedback fakes.
    async def record_feedback(self, *, chunk_id, namespace, vote, request_id=None, created_by="unknown"):  # noqa: ANN001
        self.feedback = getattr(self, "feedback", [])
        self.feedback.append({"chunk_id": chunk_id, "vote": vote, "by": created_by})
        return f"fb-{len(self.feedback)}"


class _FakePipeline:
    def __init__(self) -> None:
        self.last: dict[str, Any] = {}

    async def ingest_file(self, *, filename, data, namespace, bucket_ids=None, created_by="x"):  # noqa: ANN001
        self.last = {"namespace": namespace, "bucket_ids": list(bucket_ids or [])}
        return SimpleNamespace(
            doc_id="doc-new", status="pending", skipped=False, chunks=3,
            sensitivity="normal", pii_findings=[],
        )

    async def ingest_text(  # noqa: ANN001
        self, *, text, title, source_type, namespace, bucket_ids=None, project_id=None,
    ):
        self.last = {"namespace": namespace, "bucket_ids": list(bucket_ids or []), "title": title}
        return SimpleNamespace(
            doc_id="doc-promoted", status="pending", skipped=False, chunks=2,
            sensitivity="normal", pii_findings=[],
        )


class _FakeStateStore:
    def __init__(self, req: Any = None) -> None:
        self._req = req

    async def get_request(self, request_id):  # noqa: ANN001
        return self._req


def _make_subsystem(available: bool = True) -> Any:
    store = _FakeStore()
    settings = SimpleNamespace(
        platform_namespace="kb_platform", embed_model="bge-small-en-v1.5", rerank_enabled=False,
        project_namespace=lambda p: f"kb_project_{p}",
        memory_namespace=lambda p: f"mem_project_{p}",
    )
    return SimpleNamespace(
        available=available, reason="ok" if available else "no key",
        settings=settings, knowledge_store=store, pipeline=_FakePipeline(),
        retriever=MagicMock(),
    )


def _make_app(
    subsystem: Any, role: str = "developer", req: Any = None,
    curator_scopes: list[str] | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(kb_route.router)
    app.state.kb_subsystem = subsystem
    app.state.events = _FakeEvents()
    app.state.state_store = _FakeStateStore(req)

    def _fake_user() -> dict[str, Any]:
        return {
            "sub": "u1", "username": "tester", "role": role,
            "kb_curator_scopes": curator_scopes or [],
        }

    app.dependency_overrides[get_current_user] = _fake_user
    # Override every require_role gate to the same fake user (RBAC is asserted
    # separately by toggling `role`).
    for route in app.routes:
        for dep in getattr(route, "dependant", MagicMock()).dependencies or []:
            if getattr(dep.call, "__name__", "") == "role_checker":
                app.dependency_overrides[dep.call] = _fake_user
    return app


# ── Status ───────────────────────────────────────────────────────────────


def test_status_available():
    app = _make_app(_make_subsystem(True))
    r = TestClient(app).get("/api/v1/knowledge")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["available"] is True
    assert body["data"]["namespace"] == "kb_platform"
    assert body["meta"]["kb_available"] is True


def test_status_unavailable_soft_fails():
    app = _make_app(_make_subsystem(False))
    body = TestClient(app).get("/api/v1/knowledge").json()
    assert body["data"]["available"] is False
    assert body["meta"]["kb_available"] is False


# ── Reads soft-fail ───────────────────────────────────────────────────────


def test_list_buckets_empty_when_down():
    app = _make_app(_make_subsystem(False))
    body = TestClient(app).get("/api/v1/knowledge/buckets").json()
    assert body["data"] == []
    assert body["meta"]["kb_available"] is False


def test_list_buckets_when_up():
    sub = _make_subsystem(True)
    app = _make_app(sub)
    client = TestClient(app)
    client.post("/api/v1/knowledge/buckets", json={"name": "Acme"})
    body = client.get("/api/v1/knowledge/buckets").json()
    assert body["meta"]["kb_available"] is True
    assert any(b["name"] == "Acme" for b in body["data"])


# ── Writes require KB up ──────────────────────────────────────────────────


def test_upload_503_when_down():
    app = _make_app(_make_subsystem(False))
    files = {"file": ("a.md", b"# hi", "text/markdown")}
    r = TestClient(app).post("/api/v1/knowledge/documents", files=files)
    assert r.status_code == 503


def test_upload_ingests_when_up():
    app = _make_app(_make_subsystem(True))
    files = {"file": ("a.md", b"# hi", "text/markdown")}
    r = TestClient(app).post(
        "/api/v1/knowledge/documents", files=files, data={"bucket_ids": '["b-1"]'},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["doc_id"] == "doc-new"
    assert r.json()["data"]["chunks"] == 3


def test_upload_with_project_id_uses_project_namespace_and_bucket():
    """KB-16 — a project upload provisions the project KB and ingests into its
    isolated namespace + default bucket (not the platform namespace)."""
    sub = _make_subsystem(True)
    app = _make_app(sub)
    files = {"file": ("brand.md", b"# brand guide", "text/markdown")}
    r = TestClient(app).post(
        "/api/v1/knowledge/documents", files=files, data={"project_id": "appX"},
    )
    assert r.status_code == 200, r.text
    # Ingest targeted the project namespace, with the auto-provisioned default
    # bucket included.
    assert sub.pipeline.last["namespace"] == "kb_project_appX"
    assert "pb-appX" in sub.pipeline.last["bucket_ids"]


def test_list_buckets_scoped_to_project():
    sub = _make_subsystem(True)
    # Seed a global bucket + a project bucket.
    sub.knowledge_store.buckets["g1"] = SimpleNamespace(
        bucket_id="g1", name="Global", slug="global", description="", project_id=None,
        is_system=False, created_by="u", created_at=None, doc_count=0, chunk_count=0)
    await_provision = _make_app(sub)  # app built; provision via the route below
    client = TestClient(await_provision)
    # Upload into appX provisions its bucket.
    client.post("/api/v1/knowledge/documents",
                files={"file": ("x.md", b"# x", "text/markdown")}, data={"project_id": "appX"})
    body = client.get("/api/v1/knowledge/buckets?project_id=appX").json()
    names = {b["name"] for b in body["data"]}
    assert "Global" not in names  # global bucket excluded from the project scope
    assert any(b["bucket_id"] == "pb-appX" for b in body["data"])


# ── RBAC ──────────────────────────────────────────────────────────────────


def test_create_bucket_forbidden_for_viewer():
    # Viewer is NOT in the require_role("developer","admin") set. Drive the
    # REAL role_checker: supply a credential (so it isn't a 401) + an
    # auth_service that decodes the token to a viewer → expect 403.
    from fastapi.security import HTTPAuthorizationCredentials

    from src.auth.service import security_scheme

    sub = _make_subsystem(True)
    app = FastAPI()
    app.include_router(kb_route.router)
    app.state.kb_subsystem = sub
    app.state.events = _FakeEvents()
    app.state.auth_service = SimpleNamespace(
        decode_token=lambda tok: {"sub": "u1", "username": "v", "role": "viewer"}
    )

    def _creds() -> HTTPAuthorizationCredentials:
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")

    app.dependency_overrides[security_scheme] = _creds
    r = TestClient(app).post("/api/v1/knowledge/buckets", json={"name": "X"})
    assert r.status_code == 403


def test_delete_system_bucket_forbidden():
    sub = _make_subsystem(True)
    # Seed a system bucket.
    sys_b = SimpleNamespace(
        bucket_id="b-sys", name="Platform", slug="platform", description="",
        project_id=None, is_system=True, created_by="system", created_at=None,
        doc_count=0, chunk_count=0,
    )
    sub.knowledge_store.buckets["b-sys"] = sys_b
    app = _make_app(sub, role="admin")
    r = TestClient(app).delete("/api/v1/knowledge/buckets/b-sys")
    assert r.status_code == 403


def test_create_then_delete_bucket():
    sub = _make_subsystem(True)
    app = _make_app(sub, role="admin")
    client = TestClient(app)
    created = client.post("/api/v1/knowledge/buckets", json={"name": "Temp"}).json()
    bid = created["data"]["bucket_id"]
    r = client.delete(f"/api/v1/knowledge/buckets/{bid}")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "deleted"


# ── Grounding report ──────────────────────────────────────────────────────


def test_grounding_report_hydrates_citations():
    sub = _make_subsystem(True)
    sub.knowledge_store.audit = [{
        "audit_id": "a1", "request_id": "REQ-1", "agent_id": "research_specialist",
        "namespace": "kb_platform", "query": "pricing",
        "bucket_ids": ["b-1"], "returned_chunk_ids": ["c1", "c2"],
        "cited_chunk_ids": ["c1"], "created_at": None,
    }]
    sub.knowledge_store.chunks = {
        "c1": {"chunk_id": "c1", "doc_id": "doc-1", "title": "Doc One",
               "uri": "doc1.md", "text": "the pricing is $9", "namespace": "kb_platform",
               "status": "approved"},
    }
    # KB-23 — cited document carries provenance the route hydrates onto the citation.
    sub.knowledge_store.docs = {
        "doc-1": SimpleNamespace(
            doc_id="doc-1", source_type="upload", version=3, status="approved",
            curated_by="admin",
            approved_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            created_at=datetime(2026, 5, 30, tzinfo=timezone.utc),
        ),
    }
    # KB-23 — decision ledger surfaces alongside citations.
    sub.knowledge_store.decisions_log = [{
        "decision_id": "d1", "request_id": "REQ-1", "agent_id": "research_specialist",
        "project_id": None, "summary": "Recommended $9 tier",
        "retrieved_chunk_ids": ["c1"], "recalled_memory_ids": [],
        "inputs_digest": "abc", "created_at": None,
    }]
    req = SimpleNamespace(bucket_ids=["b-1"])
    app = _make_app(sub, role="viewer", req=req)
    body = TestClient(app).get("/api/v1/knowledge/grounding/REQ-1").json()
    assert body["data"]["buckets"] == ["b-1"]
    assert len(body["data"]["retrievals"]) == 1
    assert len(body["data"]["citations"]) == 1
    cite = body["data"]["citations"][0]
    assert cite["chunk_id"] == "c1"
    assert "pricing" in cite["snippet"]
    # provenance hydrated from the source document
    assert cite["source_type"] == "upload"
    assert cite["version"] == 3
    assert cite["status"] == "approved"
    assert cite["approved_by"] == "admin"
    # decision ledger present in the envelope
    assert len(body["data"]["decisions"]) == 1
    assert body["data"]["decisions"][0]["summary"] == "Recommended $9 tier"


def test_grounding_report_soft_fails_when_down():
    app = _make_app(_make_subsystem(False))
    body = TestClient(app).get("/api/v1/knowledge/grounding/REQ-1").json()
    assert body["meta"]["kb_available"] is False
    assert body["data"]["retrievals"] == []


# ── Promotion gate (KB-28) ───────────────────────────────────────────────────


def _promo(cid="promo-1", ns="mem_project_P1", pid="P1", status="pending"):
    return {
        "candidate_id": cid, "namespace": ns, "project_id": pid, "kind": "pattern",
        "summary": "Recurring failure: migrate db", "evidence_ids": ["m1", "m2", "m3"],
        "occurrences": 3, "status": status, "created_at": None,
    }


def test_list_promotions_filters_by_project():
    sub = _make_subsystem(True)
    sub.knowledge_store.promos = {"promo-1": _promo()}
    app = _make_app(sub, role="viewer")
    body = TestClient(app).get("/api/v1/knowledge/promotions?project_id=P1").json()
    assert body["meta"]["kb_available"] is True
    assert len(body["data"]) == 1
    assert body["data"][0]["candidate_id"] == "promo-1"


def test_list_promotions_soft_fails_when_down():
    app = _make_app(_make_subsystem(False))
    body = TestClient(app).get("/api/v1/knowledge/promotions").json()
    assert body["meta"]["kb_available"] is False
    assert body["data"] == []


def test_approve_promotion_ingests_and_marks_promoted():
    sub = _make_subsystem(True)
    sub.knowledge_store.promos = {"promo-1": _promo()}
    app = _make_app(sub, role="developer")
    body = TestClient(app).post("/api/v1/knowledge/promotions/promo-1/approve").json()
    assert body["data"]["status"] == "promoted"
    assert body["data"]["doc_id"] == "doc-promoted"
    assert body["data"]["namespace"] == "kb_project_P1"
    # candidate moved out of pending; ingest landed in the project namespace
    assert sub.knowledge_store.promos["promo-1"]["status"] == "promoted"
    assert sub.pipeline.last["namespace"] == "kb_project_P1"


def test_approve_promotion_404_when_missing():
    sub = _make_subsystem(True)
    sub.knowledge_store.promos = {}
    app = _make_app(sub, role="developer")
    r = TestClient(app).post("/api/v1/knowledge/promotions/nope/approve")
    assert r.status_code == 404


def test_approve_promotion_409_when_already_reviewed():
    sub = _make_subsystem(True)
    sub.knowledge_store.promos = {"promo-1": _promo(status="promoted")}
    app = _make_app(sub, role="developer")
    r = TestClient(app).post("/api/v1/knowledge/promotions/promo-1/approve")
    assert r.status_code == 409


def test_reject_promotion_marks_rejected():
    sub = _make_subsystem(True)
    sub.knowledge_store.promos = {"promo-1": _promo()}
    app = _make_app(sub, role="developer")
    body = TestClient(app).post("/api/v1/knowledge/promotions/promo-1/reject").json()
    assert body["data"]["status"] == "rejected"
    assert sub.knowledge_store.promos["promo-1"]["status"] == "rejected"


def test_promotion_write_503_when_down():
    app = _make_app(_make_subsystem(False), role="developer")
    r = TestClient(app).post("/api/v1/knowledge/promotions/promo-1/approve")
    assert r.status_code == 503


# ── KB-29 curator capability ─────────────────────────────────────────────────


def test_approve_promotion_forbidden_for_viewer_without_scope():
    sub = _make_subsystem(True)
    sub.knowledge_store.promos = {"promo-1": _promo(pid="P1")}
    app = _make_app(sub, role="viewer", curator_scopes=[])
    r = TestClient(app).post("/api/v1/knowledge/promotions/promo-1/approve")
    assert r.status_code == 403
    assert sub.knowledge_store.promos["promo-1"]["status"] == "pending"  # untouched


def test_approve_promotion_allowed_for_scoped_viewer():
    sub = _make_subsystem(True)
    sub.knowledge_store.promos = {"promo-1": _promo(pid="P1")}
    # A plain viewer granted curator rights on exactly this project.
    app = _make_app(sub, role="viewer", curator_scopes=["P1"])
    body = TestClient(app).post("/api/v1/knowledge/promotions/promo-1/approve").json()
    assert body["data"]["status"] == "promoted"


def test_approve_promotion_scoped_viewer_wrong_project_403():
    sub = _make_subsystem(True)
    sub.knowledge_store.promos = {"promo-1": _promo(pid="P1")}
    # Curator of a DIFFERENT project can't curate P1.
    app = _make_app(sub, role="viewer", curator_scopes=["P2"])
    r = TestClient(app).post("/api/v1/knowledge/promotions/promo-1/approve")
    assert r.status_code == 403


def test_is_kb_curator_capability_matrix():
    from src.auth.service import is_kb_curator

    # admins / developers are implicit curators everywhere
    assert is_kb_curator({"role": "admin"}, "anything") is True
    assert is_kb_curator({"role": "developer"}, "P1") is True
    # wildcard scope
    assert is_kb_curator({"role": "viewer", "kb_curator_scopes": ["*"]}, "P9") is True
    # scoped viewer — only their granted scope
    assert is_kb_curator({"role": "viewer", "kb_curator_scopes": ["P1"]}, "P1") is True
    assert is_kb_curator({"role": "viewer", "kb_curator_scopes": ["P1"]}, "P2") is False
    # plain viewer is not a curator
    assert is_kb_curator({"role": "viewer"}, "platform") is False


# ── KB-30 retention + forgetting routes ──────────────────────────────────────


def test_forget_subject_global_admin_only():
    sub = _make_subsystem(True)
    app = _make_app(sub, role="admin")
    body = TestClient(app).post(
        "/api/v1/knowledge/forget", json={"subject": "alice@example.com"}
    ).json()
    assert body["data"]["memory"] == 2
    assert body["data"]["documents"] == 1
    assert sub.knowledge_store.forgotten[0]["namespace"] is None  # global


def test_forget_subject_project_scoped_purges_both_namespaces():
    sub = _make_subsystem(True)
    app = _make_app(sub, role="admin")
    TestClient(app).post(
        "/api/v1/knowledge/forget", json={"subject": "bob@x.com", "project_id": "P1"}
    )
    namespaces = {f["namespace"] for f in sub.knowledge_store.forgotten}
    assert namespaces == {"mem_project_P1", "kb_project_P1"}


def test_forget_subject_rejects_short_subject():
    app = _make_app(_make_subsystem(True), role="admin")
    r = TestClient(app).post("/api/v1/knowledge/forget", json={"subject": "ab"})
    assert r.status_code == 400


def test_forget_subject_503_when_down():
    app = _make_app(_make_subsystem(False), role="admin")
    r = TestClient(app).post("/api/v1/knowledge/forget", json={"subject": "alice"})
    assert r.status_code == 503


def test_retention_audit_lists_rows():
    sub = _make_subsystem(True)
    sub.knowledge_store.retention_audit = [
        {"audit_id": "ret-1", "action": "forget_subject", "scope": "alice",
         "actor": "admin", "counts": {"memory": 1}, "created_at": None},
    ]
    app = _make_app(sub, role="developer")
    body = TestClient(app).get("/api/v1/knowledge/retention/audit").json()
    assert len(body["data"]) == 1
    assert body["data"][0]["action"] == "forget_subject"


def test_retention_audit_soft_fails_when_down():
    app = _make_app(_make_subsystem(False), role="developer")
    body = TestClient(app).get("/api/v1/knowledge/retention/audit").json()
    assert body["meta"]["kb_available"] is False
    assert body["data"] == []


# ── KB-31 feedback route ─────────────────────────────────────────────────────


def test_submit_feedback_records_vote():
    sub = _make_subsystem(True)
    sub.knowledge_store.chunks = {"c1": {"chunk_id": "c1", "namespace": "kb_project_P1"}}
    app = _make_app(sub, role="viewer")  # any authenticated user can vote
    body = TestClient(app).post(
        "/api/v1/knowledge/feedback", json={"chunk_id": "c1", "vote": "up"}
    ).json()
    assert body["data"]["vote"] == "up"
    assert sub.knowledge_store.feedback[0]["chunk_id"] == "c1"
    assert sub.knowledge_store.feedback[0]["vote"] == 1


def test_submit_feedback_down_maps_to_negative():
    sub = _make_subsystem(True)
    sub.knowledge_store.chunks = {"c1": {"chunk_id": "c1", "namespace": "kb_platform"}}
    app = _make_app(sub, role="developer")
    TestClient(app).post("/api/v1/knowledge/feedback", json={"chunk_id": "c1", "vote": "down"})
    assert sub.knowledge_store.feedback[0]["vote"] == -1


def test_submit_feedback_404_unknown_chunk():
    sub = _make_subsystem(True)
    sub.knowledge_store.chunks = {}
    app = _make_app(sub, role="viewer")
    r = TestClient(app).post("/api/v1/knowledge/feedback", json={"chunk_id": "nope", "vote": "up"})
    assert r.status_code == 404


def test_submit_feedback_503_when_down():
    app = _make_app(_make_subsystem(False), role="viewer")
    r = TestClient(app).post("/api/v1/knowledge/feedback", json={"chunk_id": "c1"})
    assert r.status_code == 503
