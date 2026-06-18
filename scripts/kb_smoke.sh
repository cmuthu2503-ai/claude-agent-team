#!/usr/bin/env bash
set -e
BASE=http://localhost:8000/api/v1
TOKEN=$(cat /tmp/kb_token.txt)
AUTH="Authorization: Bearer ${TOKEN}"
CT="Content-Type: application/json"

echo "=== 1. CREATE BUCKET 'Agentic AI' ==="
BID=$(curl -s -X POST "$BASE/knowledge/buckets" -H "$AUTH" -H "$CT" \
  -d '{"name":"Agentic AI","description":"agentic architecture research"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['data']['bucket_id'])")
echo "bucket_id=$BID"

echo ""
echo "=== 2. INGEST-TEXT (paste path) ==="
curl -s -X POST "$BASE/knowledge/ingest-text" -H "$AUTH" -H "$CT" -d @- <<JSON | python -m json.tool
{
  "text": "Multi-agent orchestration is transforming loan underwriting in retail banking. By decomposing credit decisions across specialist agents - a document-intake agent, a risk-scoring agent, and a compliance agent enforcing Basel III - banks achieve faster, auditable lending decisions. Each agent grounds its output in the bank policy corpus.",
  "title": "Multi-Agent Orchestration for Loan Underwriting",
  "source_url": "https://linkedin.com/posts/example-underwriting",
  "bucket_ids": ["$BID"]
}
JSON

echo ""
echo "=== 3. INGEST a second, unrelated article ==="
curl -s -X POST "$BASE/knowledge/ingest-text" -H "$AUTH" -H "$CT" -d @- <<JSON | python -c "import sys,json;d=json.load(sys.stdin)['data'];print('doc:',d['doc_id'],'status:',d['status'],'chunks:',d['chunks'])"
{
  "text": "Sourdough bread relies on a wild-yeast starter. Hydration ratio and fermentation time determine crumb structure. A higher hydration dough yields a more open crumb.",
  "title": "The Art of Sourdough",
  "source_url": "https://example.com/sourdough",
  "bucket_ids": ["$BID"]
}
JSON

echo ""
echo "=== 4. SEARCH: 'Agentic AI Architecture in Banking Industry' ==="
echo "(north-star: should rank the underwriting article top, with its source link,"
echo " even though it never contains the phrase 'Agentic AI Architecture')"
curl -s -X POST "$BASE/knowledge/search" -H "$AUTH" -H "$CT" \
  -d '{"query":"Agentic AI Architecture in Banking Industry","top_k":5}' \
  | python -m json.tool

echo ""
echo "=== 5. DEDUP CHECK: re-ingest the SAME underwriting text ==="
curl -s -X POST "$BASE/knowledge/ingest-text" -H "$AUTH" -H "$CT" -d @- <<JSON | python -c "import sys,json;d=json.load(sys.stdin)['data'];print('skipped:',d['skipped'],'(expect True)')"
{
  "text": "Multi-agent orchestration is transforming loan underwriting in retail banking. By decomposing credit decisions across specialist agents - a document-intake agent, a risk-scoring agent, and a compliance agent enforcing Basel III - banks achieve faster, auditable lending decisions. Each agent grounds its output in the bank policy corpus.",
  "title": "Multi-Agent Orchestration for Loan Underwriting",
  "source_url": "https://linkedin.com/posts/example-underwriting",
  "bucket_ids": ["$BID"]
}
JSON
echo ""
echo "SMOKE_DONE"
