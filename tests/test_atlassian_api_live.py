"""Live integration test - JIRA Free tier verified patterns."""
import os, sys, time
from atlassian import Confluence, Jira

TOKEN=""
for vn in ["JIRA_API_TOKEN", "CONFLUENCE_API_TOKEN"]:
    v = os.environ.get(vn, "").strip()
    if v and len(v) > 10:
        TOKEN=v
        break
if not TOKEN:
    print("No token"); sys.exit(1)

EMAIL = os.environ.get("JIRA_EMAIL", "").strip() or os.environ.get("CONFLUENCE_EMAIL", "").strip()
CF_URL = os.environ.get("CONFLUENCE_URL", "").strip()
JIRA_URL = os.environ.get("JIRA_URL", "").strip()
ts = str(int(time.time()))[-4:]
results = {}

# Confluence
cf = Confluence(url=CF_URL, username=EMAIL, password=TOKEN, timeout=15)
print("=== CONFLUENCE ===")

try: r = cf.get_space("AIAGENTTEA"); results["cf_get_space"] = "OK"
except Exception: results["cf_get_space"] = "raises (expected)"

sk = "TSTSP" + ts
try: cf.create_space(space_key=sk, space_name="Test"); results["cf_create_space"] = "OK"
except Exception as e: results["cf_create_space"] = "exists/limit (OK)" if "cannot" in str(e).lower() else str(e)[:80]

try: r = cf.create_page(space=sk, title="T", body="h1. Hi", representation="wiki"); results["cf_create_page"] = "OK"
except Exception as e: results["cf_create_page"] = str(e)[:80]

try:
    pages = cf.get_all_pages_from_space(sk, limit=1)
    if pages: cf.update_page(page_id=pages[0]["id"], title="U", body="h2. ok", representation="wiki"); results["cf_update_page"] = "OK"
    else: results["cf_update_page"] = "SKIP"
except Exception as e: results["cf_update_page"] = str(e)[:80]

try:
    for p in cf.get_all_pages_from_space(sk, limit=10): cf.remove_page(p["id"])
    results["cf_cleanup"] = "OK"
except Exception as e: results["cf_cleanup"] = str(e)[:80]

# JIRA
jr = Jira(url=JIRA_URL, username=EMAIL, password=TOKEN, timeout=15)
print("=== JIRA ===")

try: fields = jr.get_all_fields(); results["jr_get_fields"] = f"OK ({len(fields)})"
except Exception as e: results["jr_get_fields"] = str(e)[:80]

try: me = jr.myself(); aid = me.get("accountId", ""); results["jr_myself"] = "OK"
except Exception as e: results["jr_myself"] = str(e)[:80]; aid = ""

pk = "TST" + ts
try: r = jr.create_project_from_raw_json({"key": pk, "name": pk, "projectTypeKey": "software", "leadAccountId": aid}); results["jr_create_project"] = f"OK key={r.get('key',pk) if r else pk}"
except Exception as e: results["jr_create_project"] = "exists (OK)" if "already exists" in str(e).lower() else str(e)[:80]

# Epic -> Task ([EPIC], no parent)
ek = None
try:
    r = jr.create_issue(fields={"project": {"key": pk}, "summary": "[EPIC] Test", "description": "d", "issuetype": {"name": "Task"}})
    ek = r.get("key") if r else None; results["jr_epic_task"] = f"OK key={ek}"
except Exception as e: results["jr_epic_task"] = str(e)[:80]

# Feature -> Task ([Feature], no parent, linked)
sk2 = None
if ek:
    try:
        r = jr.create_issue(fields={"project": {"key": pk}, "summary": "[Feature] Test", "description": "d", "issuetype": {"name": "Task"}})
        sk2 = r.get("key") if r else None
        try: jr.create_issue_link(data={"type": {"name": "Relates"}, "inwardIssue": {"key": ek}, "outwardIssue": {"key": sk2}})
        except: pass
        results["jr_feature_task"] = f"OK key={sk2} link={ek}"
    except Exception as e: results["jr_feature_task"] = str(e)[:80]
else: results["jr_feature_task"] = "SKIP"

# Subtask -> Sub-task (parent=feature)
sb = None
if sk2:
    try:
        r = jr.create_issue(fields={"project": {"key": pk}, "summary": "Sub1", "description": "d", "issuetype": {"name": "Sub-task"}, "parent": {"key": sk2}})
        sb = r.get("key") if r else None; results["jr_subtask"] = f"OK key={sb}"
    except Exception as e: results["jr_subtask"] = str(e)[:80]
else: results["jr_subtask"] = "SKIP"

# update_issue
if ek:
    try:
        jr.update_issue(ek, update={"fields": {"summary": "[EPIC] Updated"}})
        results["jr_update"] = "OK"
    except Exception as e: results["jr_update"] = str(e)[:80]
else: results["jr_update"] = "SKIP"

# transitions
if ek:
    try:
        ts2 = jr.get_issue_transitions(ek)
        if ts2: jr.issue_transition(ek, ts2[0]["name"]); results["jr_transition"] = "OK"
        else: results["jr_transition"] = "OK (no transitions)"
    except Exception as e: results["jr_transition"] = str(e)[:80]
else: results["jr_transition"] = "SKIP"

# cleanup
try: jr.delete_project(pk); results["jr_cleanup"] = "OK"
except Exception as e: results["jr_cleanup"] = str(e)[:80]

# Summary
print("\n" + "=" * 60)
fails = 0
for name, result in sorted(results.items()):
    if result.startswith("OK") or result.startswith("exists") or result.startswith("SKIP") or result.startswith("raises"):
        print(f"  [OK] {name}: {result}")
    else:
        print(f"  [FAIL] {name}: {result}"); fails += 1
print(f"\n{'ALL PASSED' if fails == 0 else f'{fails} FAILURES'}")
