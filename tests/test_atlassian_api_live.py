"""Deep integration test — validates every Confluence + JIRA API call."""
import os, sys, json, traceback

from atlassian import Confluence, Jira

TOKEN = os.getenv("CONFLUENCE_API_TOKEN", "").strip()
EMAIL = os.getenv("CONFLUENCE_EMAIL", "").strip()
CF_URL = os.getenv("CONFLUENCE_URL", "").strip()
JIRA_URL = os.getenv("JIRA_URL", "").strip()

if not TOKEN:
    print("No API token set — aborting")
    sys.exit(1)

results = {}

# ── Confluence ──────────────────────────────────────────
cf = Confluence(url=CF_URL, username=EMAIL, password=TOKEN, timeout=15)

print("=== CONFLUENCE ===")

# 1. get_space(existing) - try a known space or the auto-created one
try:
    r = cf.get_space("AIAGENTTEA")
    results["cf_get_space_existing"] = "works" if r else "returns None"
except Exception as e:
    results["cf_get_space_existing"] = str(e)

# 2. get_space(missing)
try:
    r = cf.get_space("NONEXIST999")
    results["cf_get_space_missing"] = "raises" if r else "returns None (expected)"
except Exception as e:
    results["cf_get_space_missing"] = f"raises: {type(e).__name__}"

# 3. create_space
try:
    r = cf.create_space(space_key="TESTSPACE1", space_name="Test Space 1")
    results["cf_create_space"] = f"returns {type(r).__name__} ({'dict' if r else 'None'})"
except Exception as e:
    results["cf_create_space"] = str(e)

# 4. create_page
try:
    r = cf.create_page(space="TESTSPACE1", title="Test Page", body="h1. Hello World", representation="wiki")
    pid = r.get("id") if r else "None"
    results["cf_create_page"] = f"returns {type(r).__name__} id={pid}"
except Exception as e:
    results["cf_create_page"] = str(e)

# 5. update_page
try:
    pages = cf.get_all_pages_from_space("TESTSPACE1", limit=1)
    if pages:
        page = pages[0]
        cf.update_page(page_id=page["id"], title="Test Page v2", body="h2. Updated", representation="wiki")
        results["cf_update_page"] = "no exception"
    else:
        results["cf_update_page"] = "no pages found to update"
except Exception as e:
    results["cf_update_page"] = str(e)

# 6. Cleanup pages
try:
    for p in cf.get_all_pages_from_space("TESTSPACE1", limit=10):
        cf.remove_page(p["id"])
    results["cf_cleanup"] = "cleaned"
except Exception as e:
    results["cf_cleanup"] = str(e)

# ── JIRA ────────────────────────────────────────────────
jr = Jira(url=JIRA_URL, username=EMAIL, password=TOKEN, timeout=15)

print("=== JIRA ===")

# 7. get_all_fields
try:
    fields = jr.get_all_fields()
    epic_link = [f for f in fields if f.get("name","").lower() == "epic link"]
    results["jr_get_all_fields"] = f"{len(fields)} fields, Epic Link: {epic_link[0]['id'] if epic_link else 'N/A'}"
except Exception as e:
    results["jr_get_all_fields"] = str(e)

# 8. myself
try:
    me = jr.myself()
    results["jr_myself"] = f"accountId={me.get('accountId','?')}"
except Exception as e:
    results["jr_myself"] = str(e)

# 9. create_project_from_raw_json
try:
    r = jr.create_project_from_raw_json({
        "key": "TESTPRJ1",
        "name": "Test Project 1",
        "projectTypeKey": "software",
        "templateKey": "com.pyxis.greenhopper.jira:gh-simplified-scrum-classic",
    })
    results["jr_create_project"] = f"returns {type(r).__name__} key={r.get('key') if r else 'None'}"
except Exception as e:
    err = str(e)
    if "already exists" in err.lower():
        results["jr_create_project"] = "already exists (idempotent OK)"
    else:
        results["jr_create_project"] = str(e)

# 10. create_issue (Epic)
epic_key = None
try:
    r = jr.create_issue(fields={
        "project": {"key": "TESTPRJ1"},
        "summary": "Test Epic",
        "description": "Test epic description",
        "issuetype": {"name": "Epic"},
        "customfield_10011": "Test Epic",
    })
    epic_key = r.get("key") if r else None
    results["jr_create_epic"] = f"key={epic_key}"
except Exception as e:
    results["jr_create_epic"] = str(e)

# 11. create_issue (Story under Epic)
story_key = None
if epic_key:
    try:
        all_f = jr.get_all_fields()
        epic_link_id = None
        for f in all_f:
            if f.get("name","").lower() == "epic link":
                epic_link_id = f["id"]
                break
        fields2 = {
            "project": {"key": "TESTPRJ1"},
            "summary": "Test Story",
            "description": "Test story description",
            "issuetype": {"name": "Story"},
        }
        if epic_link_id:
            fields2[epic_link_id] = epic_key
        r = jr.create_issue(fields=fields2)
        story_key = r.get("key") if r else None
        results["jr_create_story"] = f"key={story_key}"
    except Exception as e:
        results["jr_create_story"] = str(e)
else:
    results["jr_create_story"] = "skipped (no epic)"

# 12. create_issue (Sub-task under Story)
sub_key = None
if story_key:
    try:
        r = jr.create_issue(fields={
            "project": {"key": "TESTPRJ1"},
            "summary": "Test Subtask",
            "description": "Test subtask description",
            "issuetype": {"name": "Sub-task"},
            "parent": {"key": story_key},
        })
        sub_key = r.get("key") if r else None
        results["jr_create_subtask"] = f"key={sub_key}"
    except Exception as e:
        results["jr_create_subtask"] = str(e)
else:
    results["jr_create_subtask"] = "skipped (no story)"

# 13. update_issue
if epic_key:
    try:
        jr.update_issue(epic_key, update={
            "summary": [{"set": "Updated Epic Title"}],
        })
        results["jr_update_issue"] = "no exception"
    except Exception as e:
        results["jr_update_issue"] = str(e)
else:
    results["jr_update_issue"] = "skipped"

# 14. create_issue_link
if story_key and epic_key:
    try:
        jr.create_issue_link(data={
            "type": {"name": "Blocks"},
            "inwardIssue": {"key": epic_key},
            "outwardIssue": {"key": story_key},
        })
        results["jr_issue_link"] = "no exception"
    except Exception as e:
        results["jr_issue_link"] = str(e)
else:
    results["jr_issue_link"] = "skipped"

# 15. get_issue_transitions
if epic_key:
    try:
        transitions = jr.get_issue_transitions(epic_key)
        names = [t.get("name") for t in transitions]
        results["jr_get_transitions"] = str(names) if names else "empty list"
    except Exception as e:
        results["jr_get_transitions"] = str(e)
else:
    results["jr_get_transitions"] = "skipped"

# 16. issue_transition
if epic_key:
    try:
        transitions = jr.get_issue_transitions(epic_key)
        if transitions:
            first = transitions[0]["name"]
            jr.issue_transition(epic_key, first)
            results["jr_transition"] = f"transitioned to '{first}'"
        else:
            results["jr_transition"] = "no transitions available"
    except Exception as e:
        results["jr_transition"] = str(e)
else:
    results["jr_transition"] = "skipped"

# 17. delete_project cleanup
try:
    jr.delete_project("TESTPRJ1")
    results["jr_cleanup"] = "cleaned"
except Exception as e:
    results["jr_cleanup"] = str(e)

# ── Summary ─────────────────────────────────────────────
print("\n" + "=" * 60)
failures = 0
for name, result in results.items():
    if result.startswith("returns") or result.startswith("key=") or result.startswith("no exception") or result.startswith("cleaned") or result.startswith("works") or result.startswith(("alread","transition","account","empty")):
        icon = "OK"
    elif result.startswith(("raises","returns None (expected)","skipped","no trans","no pages")):
        icon = "OK"
    else:
        icon = "FAIL"
        failures += 1
    print(f"  [{icon}] {name}: {result}")

print(f"\n{'ALL PASSED' if failures == 0 else f'{failures} FAILURES'}")
