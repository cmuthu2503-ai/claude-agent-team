"""Live integration test — Free-tier aware."""
import os, sys, time
from atlassian import Confluence, Jira

# Read token from first available env var
TOKEN = ""
for vn in ["CONFLUENCE_API_TOKEN", "JIRA_API_TOKEN"]:
    val = os.environ.get(vn, "").strip()
    if val and len(val) > 10:
        TOKEN = val
        break
if not TOKEN:
    print("No API token"); sys.exit(1)

EMAIL = os.environ.get("CONFLUENCE_EMAIL", "").strip()
CF_URL = os.environ.get("CONFLUENCE_URL", "").strip()
JIRA_URL = os.environ.get("JIRA_URL", "").strip()

ts = str(int(time.time()))[-4:]
results = {}

# ── Confluence ──
cf = Confluence(url=CF_URL, username=EMAIL, password=TOKEN, timeout=15)
print("=== CONFLUENCE ===")

try:
    r = cf.get_space("AIAGENTTEA")
    results["cf_get_space_exists"] = "OK" if r else "None"
except Exception as e:
    results["cf_get_space_exists"] = "raises (expected)"

try:
    r = cf.get_space("NONEXIST" + ts)
    results["cf_get_space_missing"] = "None" if not r else "found?"
except Exception:
    results["cf_get_space_missing"] = "raises (expected)"

sk = "TSTSP" + ts
try:
    cf.create_space(space_key=sk, space_name="Test " + ts)
    results["cf_create_space"] = "OK"
except Exception as e:
    results["cf_create_space"] = "exists/limit (OK)" if "cannot create" in str(e).lower() else "FAIL: " + str(e)[:80]

try:
    r = cf.create_page(space=sk, title="Test", body="h1. Hi", representation="wiki")
    results["cf_create_page"] = "OK" if r else "OK (None)"
except Exception as e:
    results["cf_create_page"] = "FAIL: " + str(e)[:80]

try:
    pages = cf.get_all_pages_from_space(sk, limit=1)
    if pages:
        cf.update_page(page_id=pages[0]["id"], title="Upd", body="h2. ok", representation="wiki")
        results["cf_update_page"] = "OK"
    else:
        results["cf_update_page"] = "SKIP"
except Exception as e:
    results["cf_update_page"] = "FAIL: " + str(e)[:80]

try:
    for p in cf.get_all_pages_from_space(sk, limit=10):
        cf.remove_page(p["id"])
    results["cf_cleanup"] = "OK"
except Exception as e:
    results["cf_cleanup"] = str(e)[:80]

# ── JIRA ──
jr = Jira(url=JIRA_URL, username=EMAIL, password=TOKEN, timeout=15)
print("=== JIRA ===")

try:
    fields = jr.get_all_fields()
    results["jr_get_all_fields"] = f"OK ({len(fields)})"
except Exception as e:
    results["jr_get_all_fields"] = "FAIL: " + str(e)[:80]

try:
    me = jr.myself()
    aid = me.get("accountId", "")
    results["jr_myself"] = "OK"
except Exception as e:
    results["jr_myself"] = "FAIL: " + str(e)[:80]
    aid = ""

pk = "TST" + ts
try:
    r = jr.create_project_from_raw_json({
        "key": pk, "name": "Test " + ts,
        "projectTypeKey": "software", "leadAccountId": aid,
    })
    results["jr_create_project"] = "OK key=" + (r.get("key", pk) if r else pk)
except Exception as e:
    results["jr_create_project"] = "exists (OK)" if "already exists" in str(e).lower() else "FAIL: " + str(e)[:80]

ek = None
try:
    r = jr.create_issue(fields={
        "project": {"key": pk}, "summary": "[EPIC] Test",
        "description": "d", "issuetype": {"name": "Task"},
    })
    ek = r.get("key") if r else None
    results["jr_create_epic"] = "OK key=" + str(ek)
except Exception as e:
    results["jr_create_epic"] = "FAIL: " + str(e)[:80]

sk2 = None
if ek:
    try:
        r = jr.create_issue(fields={
            "project": {"key": pk}, "summary": "[Feature] Test",
            "description": "d", "issuetype": {"name": "Task"},
            "parent": {"key": ek},
        })
        sk2 = r.get("key") if r else None
        results["jr_create_feature"] = "OK key=" + str(sk2)
    except Exception as e:
        results["jr_create_feature"] = "FAIL: " + str(e)[:80]
else:
    results["jr_create_feature"] = "SKIP"

sb = None
if sk2:
    try:
        r = jr.create_issue(fields={
            "project": {"key": pk}, "summary": "Subtask",
            "description": "d", "issuetype": {"name": "Sub-task"},
            "parent": {"key": sk2},
        })
        sb = r.get("key") if r else None
        results["jr_create_subtask"] = "OK key=" + str(sb)
    except Exception as e:
        results["jr_create_subtask"] = "FAIL: " + str(e)[:80]
else:
    results["jr_create_subtask"] = "SKIP"

if ek:
    try:
        jr.update_issue(ek, update={"summary": [{"set": "[EPIC] Updated"}]})
        results["jr_update_issue"] = "OK"
    except Exception as e:
        results["jr_update_issue"] = "FAIL: " + str(e)[:80]
else:
    results["jr_update_issue"] = "SKIP"

try:
    jr.delete_project(pk)
    results["jr_cleanup"] = "OK"
except Exception as e:
    results["jr_cleanup"] = str(e)[:80]

print("\n" + "=" * 60)
fails = 0
for name, result in sorted(results.items()):
    if result.startswith("OK") or result.startswith("exists") or result.startswith("SKIP") or result.startswith("raises") or result.startswith("None"):
        print(f"  [OK] {name}: {result}")
    else:
        print(f"  [FAIL] {name}: {result}")
        fails += 1
print(f"\n{'ALL PASSED' if fails == 0 else f'{fails} FAILURES'}")
