"""KB-05 — loaders, PII scanner, and the ingestion pipeline.

Loader + PII tests are pure. The pipeline tests run live against Postgres
(gated on reachability) using a FAKE embedder — deterministic vectors, no
model download needed — so the full flow (dedup, chunk, PII, embed, store,
bucket-assign) is exercised end-to-end.
"""

from __future__ import annotations

import io
import os
import uuid

import pytest

from src.knowledge.interfaces import Embedder, EmbeddingResult, RerankHit
from src.knowledge.loaders import (
    UnsupportedFileTypeError,
    load_text,
    source_type_for,
)
from src.knowledge.pii import PiiScanner

# ── Loaders (pure) ───────────────────────────────────────────────────────


def test_load_text_markdown_and_code():
    text, st = load_text("notes.md", b"# Title\n\nbody")
    assert "# Title" in text and st == "upload"
    code, st2 = load_text("main.py", b"def f():\n    return 1\n")
    assert "def f" in code and st2 == "code"
    assert source_type_for("x.ts") == "code"


def test_load_text_rejects_unsupported():
    with pytest.raises(UnsupportedFileTypeError):
        load_text("archive.zip", b"PK\x03\x04")


def test_load_text_latin1_fallback():
    # invalid utf-8 bytes must not raise
    text, _ = load_text("weird.txt", b"caf\xe9 latin1")
    assert "caf" in text


def test_load_pdf_roundtrip():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello from a PDF page.")
    data = doc.tobytes()
    doc.close()
    text, st = load_text("doc.pdf", data)
    assert "Hello from a PDF" in text and st == "upload"


def test_load_docx_roundtrip():
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("First docx paragraph.")
    d.add_paragraph("Second one.")
    buf = io.BytesIO()
    d.save(buf)
    text, st = load_text("doc.docx", buf.getvalue())
    assert "First docx paragraph" in text and "Second one" in text and st == "upload"


def test_load_csv_is_plain_text():
    text, st = load_text("data.csv", b"name,role\nAlice,admin\nBob,dev\n")
    assert "Alice" in text and "admin" in text and st == "upload"


def test_load_xlsx_roundtrip():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "People"
    ws.append(["name", "role"])
    ws.append(["Alice", "admin"])
    ws.append(["Bob", "dev"])
    buf = io.BytesIO()
    wb.save(buf)
    text, st = load_text("data.xlsx", buf.getvalue())
    assert "## Sheet: People" in text
    assert "Alice" in text and "admin" in text and "Bob" in text
    assert st == "upload"


# ── PII scanner (pure) ───────────────────────────────────────────────────


def test_pii_detects_email_ssn_card():
    s = PiiScanner()
    assert s.scan("reach me at a@b.com").has_pii
    assert "email" in s.scan("a@b.com").findings
    assert "ssn" in s.scan("ssn 123-45-6789 here").findings
    # a Luhn-valid test card number
    assert "credit_card" in s.scan("card 4242 4242 4242 4242").findings
    assert "secret" in s.scan("key AKIAIOSFODNN7EXAMPLE rotated").findings


def test_pii_clean_text():
    r = PiiScanner().scan("the supervisor deploys to staging, nothing sensitive")
    assert r.has_pii is False and r.findings == []


def test_pii_card_false_positive_filtered_by_luhn():
    # a long digit run that is NOT a valid card
    r = PiiScanner().scan("order number 1234 5678 9012 3456 999")
    assert "credit_card" not in r.findings


# ── Pipeline (live) ──────────────────────────────────────────────────────


class _FakeEmbedder(Embedder):
    """Deterministic 3-d vectors from text length — no network."""

    @property
    def dimensions(self) -> int:
        return 384

    @property
    def model(self) -> str:
        return "fake-3"

    async def embed_documents(self, texts):  # type: ignore[no-untyped-def]
        return EmbeddingResult(
            vectors=[[float(len(t) % 7), 0.5, 1.0] + [0.0] * 381 for t in texts],
            model="fake-3", input_tokens=0,
        )

    async def embed_query(self, text):  # type: ignore[no-untyped-def]
        return [float(len(text) % 7), 0.5, 1.0] + [0.0] * 381

    async def rerank(self, query, documents, top_k=None):  # type: ignore[no-untyped-def]
        return [RerankHit(index=i, score=1.0) for i in range(len(documents))]


@pytest.fixture
async def pipeline():
    from src.knowledge.ingest import IngestionPipeline
    from src.knowledge.pg import open_pool
    from src.knowledge.store import KnowledgeStore

    dsn = (
        f"host={os.getenv('KB_PG_HOST', 'postgres')} port={os.getenv('KB_PG_PORT', '5432')} "
        f"user={os.getenv('KB_PG_USER', 'agentteam')} "
        f"password={os.getenv('KB_PG_PASSWORD', 'change-me-in-dev')} "
        f"dbname={os.getenv('KB_PG_DB', 'agentteam_kb')}"
    )
    try:
        pool = await open_pool(dsn, 1, 4)
    except Exception:
        pytest.skip("Postgres not reachable for live ingest test")
    store = KnowledgeStore(pool, dimensions=384)
    await store.initialize()
    pipe = IngestionPipeline(store, _FakeEmbedder(), max_tokens=60, overlap_tokens=10)
    try:
        yield pipe, store, pool
    finally:
        await pool.close()


async def _chunk_count(pool, doc_id):  # noqa: ANN001
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM kb_chunks WHERE doc_id=%s", [doc_id])
        return (await cur.fetchone())[0]


@pytest.mark.asyncio
async def test_ingest_text_creates_doc_chunks_and_buckets(pipeline):
    pipe, store, pool = pipeline
    ns = f"kb_test_{uuid.uuid4().hex[:6]}"
    b = await store.create_bucket(f"Bucket {uuid.uuid4().hex[:6]}")
    md = "# Doc\n\n" + "\n\n".join(f"Paragraph {i} of some length here." for i in range(12))
    res = await pipe.ingest_text(
        text=md, title="Doc", source_type="repo_doc", namespace=ns,
        bucket_ids=[b.bucket_id], uri="doc.md",
    )
    try:
        assert res.skipped is False and res.status == "pending" and res.chunks > 1
        assert await _chunk_count(pool, res.doc_id) == res.chunks
        # buckets synced onto chunks
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT bucket_ids FROM kb_chunks WHERE doc_id=%s LIMIT 1", [res.doc_id])
            assert str(b.bucket_id) in {str(x) for x in (await cur.fetchone())[0]}
        # embeddings written
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM kb_chunks WHERE doc_id=%s AND embedding IS NOT NULL",
                [res.doc_id])
            assert (await cur.fetchone())[0] == res.chunks
    finally:
        await store.purge_document(res.doc_id)
        await store.delete_bucket(b.bucket_id)


@pytest.mark.asyncio
async def test_ingest_is_idempotent(pipeline):
    pipe, store, _ = pipeline
    ns = f"kb_test_{uuid.uuid4().hex[:6]}"
    text = "Same content body that should dedup on second ingest."
    r1 = await pipe.ingest_text(text=text, title="A", source_type="lesson", namespace=ns)
    r2 = await pipe.ingest_text(text=text, title="A again", source_type="lesson", namespace=ns)
    try:
        assert r1.skipped is False
        assert r2.skipped is True and r2.doc_id == r1.doc_id and r2.chunks == 0
    finally:
        await store.purge_document(r1.doc_id)


@pytest.mark.asyncio
async def test_ingest_flags_pii(pipeline):
    pipe, store, _ = pipeline
    ns = f"kb_test_{uuid.uuid4().hex[:6]}"
    res = await pipe.ingest_text(
        text="Contact alice@example.com for the SSN 123-45-6789.",
        title="HasPII", source_type="lesson", namespace=ns,
    )
    try:
        assert res.sensitivity == "pii"
        assert "email" in res.pii_findings and "ssn" in res.pii_findings
        doc = await store.get_document(res.doc_id)
        assert doc.sensitivity == "pii"
    finally:
        await store.purge_document(res.doc_id)


@pytest.mark.asyncio
async def test_ingest_file_pdf(pipeline):
    pytest.importorskip("fitz")
    import fitz
    pipe, store, _ = pipeline
    ns = f"kb_test_{uuid.uuid4().hex[:6]}"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Supervisor deploys to staging.")
    data = doc.tobytes()
    doc.close()
    res = await pipe.ingest_file(filename="r.pdf", data=data, namespace=ns)
    try:
        assert res.chunks >= 1 and res.status == "pending"
    finally:
        await store.purge_document(res.doc_id)
