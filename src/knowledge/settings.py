"""Knowledge-base settings resolution (KB-02).

Reads the ``knowledge_base`` block from ``config/project.yaml`` and resolves
connection + credential values from env vars / Docker secrets (via the same
``read_secret`` helper the rest of the platform uses). Produces a frozen
``KnowledgeSettings`` the subsystem factory consumes.

Pure config + string assembly — no DB or network. Safe to import and unit
test without psycopg/fastembed installed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from src.utils.secrets import read_secret

# Chunk table the stores read/write. Created by KB-03; named here so the
# stores and the schema migration agree on one source of truth.
DEFAULT_CHUNK_TABLE = "kb_chunks"


@dataclass(frozen=True)
class KnowledgeSettings:
    enabled: bool
    # Postgres
    pg_host: str
    pg_port: int
    pg_user: str
    pg_password: str
    pg_db: str
    pool_min: int
    pool_max: int
    # Embeddings (local fastembed by default — no key, no cost)
    embed_provider: str
    embed_model: str
    embed_cache_dir: str
    dimensions: int
    # Retrieval
    top_k: int
    hybrid_candidates: int
    rerank_enabled: bool
    # Ingestion dispatch (KB-33): inline | background | queue
    ingest_mode: str
    # Namespaces
    platform_namespace: str
    project_namespace_prefix: str
    memory_namespace_prefix: str
    agent_memory_namespace_prefix: str

    def project_namespace(self, project_id: str) -> str:
        """KB-13 — the isolated namespace for a Project's own knowledge
        (``kb_project_<id>``). This is the hard-isolation boundary: an agent
        working App A is scoped to its namespace and structurally cannot
        retrieve App B's chunks."""
        return f"{self.project_namespace_prefix}{project_id}"

    def memory_namespace(self, project_id: str) -> str:
        """KB-24 — a Project's episodic-memory namespace (``mem_project_<id>``).
        Distinct from its KB namespace: same isolation boundary, but holds
        unvetted, decaying experience that is never citeable as fact."""
        return f"{self.memory_namespace_prefix}{project_id}"

    def agent_memory_namespace(self, agent_id: str) -> str:
        """KB-24 — cross-project, per-agent episodic memory
        (``mem_agent_<id>``) for experience not tied to a single app."""
        return f"{self.agent_memory_namespace_prefix}{agent_id}"

    @property
    def dsn(self) -> str:
        """libpq connection string for psycopg. Password may be empty in
        degraded local dev — psycopg handles that; the subsystem's health
        check is what gates real use."""
        return (
            f"host={self.pg_host} port={self.pg_port} "
            f"user={self.pg_user} password={self.pg_password} "
            f"dbname={self.pg_db}"
        )

    @classmethod
    def from_config(cls, config: Any) -> KnowledgeSettings:
        """Build settings from a loaded ConfigLoader (``config.project`` is
        the parsed project.yaml dict). Tolerant of a missing ``knowledge_base``
        block — returns ``enabled=False`` so the subsystem cleanly no-ops."""
        project: dict[str, Any] = getattr(config, "project", {}) or {}
        kb: dict[str, Any] = project.get("knowledge_base", {}) or {}
        pg: dict[str, Any] = kb.get("postgres", {}) or {}
        emb: dict[str, Any] = kb.get("embeddings", {}) or {}
        ret: dict[str, Any] = kb.get("retrieval", {}) or {}
        ns: dict[str, Any] = kb.get("namespaces", {}) or {}

        pg_password = read_secret(
            pg.get("password_ref", "kb_pg_password"),
            "KB_PG_PASSWORD",
            default="",
        )

        return cls(
            enabled=bool(kb.get("enabled", False)),
            pg_host=os.getenv(pg.get("host_env", "KB_PG_HOST"), "postgres"),
            pg_port=int(os.getenv(pg.get("port_env", "KB_PG_PORT"), "5432")),
            pg_user=os.getenv(pg.get("user_env", "KB_PG_USER"), "agentteam"),
            pg_password=pg_password,
            pg_db=os.getenv(pg.get("db_env", "KB_PG_DB"), "agentteam_kb"),
            pool_min=int(pg.get("pool_min", 1)),
            pool_max=int(pg.get("pool_max", 10)),
            embed_provider=str(emb.get("provider", "fastembed")),
            embed_model=str(emb.get("model", "BAAI/bge-small-en-v1.5")),
            embed_cache_dir=str(
                emb.get("cache_dir")
                or os.getenv("FASTEMBED_CACHE_PATH", "")
            ),
            dimensions=int(emb.get("dimensions", 384)),
            top_k=int(ret.get("top_k", 8)),
            hybrid_candidates=int(ret.get("hybrid_candidates", 40)),
            rerank_enabled=bool(ret.get("rerank", False)),
            ingest_mode=str(kb.get("ingest_mode", "inline")),
            platform_namespace=str(ns.get("platform", "kb_platform")),
            project_namespace_prefix=str(ns.get("project_prefix", "kb_project_")),
            memory_namespace_prefix=str(ns.get("memory_prefix", "mem_project_")),
            agent_memory_namespace_prefix=str(
                ns.get("agent_memory_prefix", "mem_agent_")
            ),
        )
