#!/usr/bin/env bash
# Live URL-ingestion smoke test (uses the real Firecrawl key).
set -e
BASE=http://localhost:8000/api/v1
TOKEN=$(cat token.txt)
AUTH="Authorization: Bearer ${TOKEN}"
CT="Content-Type: application/json"

echo "=== 1. CREATE BUCKET 'Web Research' ==="
BID=$(curl -s -X POST "$BASE/knowledge/buckets" -H "$AUTH" -H "$CT" \
  -d '{"name":"Web Research","description":"articles ingested by URL"}' \
  | grep -oE '"bucket_id":"[^"]+"' | head -1 | cut -d'"' -f4)
echo "bucket_id=$BID"

echo ""
echo "=== 2. INGEST-URL (live Firecrawl fetch) ==="
echo "Fetching a real public article and ingesting it..."
curl -s -X POST "$BASE/knowledge/ingest-url" -H "$AUTH" -H "$CT" -d @- <<JSON
{
  "url": "https://en.wikipedia.org/wiki/Large_language_model",
  "bucket_ids": ["$BID"]
}
JSON
echo ""

echo ""
echo "=== 3. SEARCH the freshly-ingested article ==="
curl -s -X POST "$BASE/knowledge/search" -H "$AUTH" -H "$CT" \
  -d '{"query":"how do large language models work","top_k":3}'
echo ""
echo "URL_SMOKE_DONE"
