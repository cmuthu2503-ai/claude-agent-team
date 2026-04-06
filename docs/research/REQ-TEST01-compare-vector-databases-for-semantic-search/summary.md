# Executive Summary

We evaluated pgvector, Pinecone, and Weaviate as vector database options for our semantic search feature.

**Recommendation:** pgvector.

**Why:** It is the simplest option, runs on existing Postgres infrastructure, and supports our scale (up to 10M vectors). Pinecone offers better horizontal scaling but the cost-per-query is 5x higher.

**Next steps:** Run a 2-week proof of concept with 100K production vectors.
