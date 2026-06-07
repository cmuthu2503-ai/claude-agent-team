"""KB-13a — local fastembed embedder.

Most of the contract (embed dims, query≠doc, batching) needs the real ONNX
model, so the embedding tests are gated: they skip cleanly if fastembed isn't
installed or the model can't be fetched (offline CI). The identity-rerank
behaviour and the Embedder-subclass contract are checked without a model.
"""

from __future__ import annotations

import pytest

from src.knowledge.interfaces import Embedder, EmbedderUnavailableError


def test_is_embedder_subclass():
    from src.knowledge.embedder_fastembed import FastEmbedEmbedder

    assert issubclass(FastEmbedEmbedder, Embedder)


def test_missing_library_raises_unavailable(monkeypatch):
    """If fastembed can't be imported, the constructor raises
    EmbedderUnavailableError (→ subsystem soft-fails), not a bare ImportError."""
    import builtins

    real_import = builtins.__import__

    def _no_fastembed(name, *a, **k):  # noqa: ANN001, ANN002, ANN003
        if name == "fastembed" or name.startswith("fastembed."):
            raise ImportError("simulated: fastembed not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_fastembed)
    from src.knowledge.embedder_fastembed import FastEmbedEmbedder

    with pytest.raises(EmbedderUnavailableError):
        FastEmbedEmbedder()


def _load_or_skip():
    from src.knowledge.embedder_fastembed import FastEmbedEmbedder

    try:
        return FastEmbedEmbedder()  # downloads bge-small on first use (cached)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"fastembed model unavailable: {e}")


@pytest.mark.asyncio
async def test_rerank_is_identity_order():
    emb = _load_or_skip()
    hits = await emb.rerank("q", ["a", "b", "c"])
    assert [h.index for h in hits] == [0, 1, 2]
    assert hits[0].score >= hits[-1].score


@pytest.mark.asyncio
async def test_embeds_documents_and_query():
    emb = _load_or_skip()
    assert emb.dimensions == 384
    res = await emb.embed_documents(["the supervisor deploys to staging", "auth uses jwt"])
    assert len(res.vectors) == 2
    assert all(len(v) == 384 for v in res.vectors)
    q = await emb.embed_query("how does deployment work")
    assert len(q) == 384
    # Empty batch is a clean no-op.
    empty = await emb.embed_documents([])
    assert empty.vectors == []
