"""Diagnostic: JIRA Next-Gen project + Epic/Story/Sub-task full test."""
import os
from atlassian import Jira

TOKEN=os.environ.get("CONFLUENCE_API_TOKEN") or os.environ.get("JIRA_API_TOKEN", "")
EMAIL = os.environ.get("CONFLUENCE_EMAIL", "")
URL = os.environ.get("JIRA_URL", "")

jr = Jira(url=URL, username=EMAIL, password=TOKEN, timeout=15)
me = jr.myself()
aid = me["accountId"]

# 1. Create project
proj = jr.create_project_from_raw_json({
    "key": "TSTPRJ9", "name": "Test9",
    "projectTypeKey": "software", "leadAccountId": aid,
})
pk = proj["key"]
print(f"1. Project: {pk}")

# 2. Epic
epic = jr.create_issue(fields={
    "project": {"key": pk}, "summary": "Epic1",
    "description": "d", "issuetype": {"name": "Epic"},
    "customfield_10011": "Epic1",
})
ek = epic["key"]
print(f"2. Epic: {ek}")

# 3. Story with parent=epic
story = jr.create_issue(fields={
    "project": {"key": pk}, "summary": "Story1",
    "description": "d", "issuetype": {"name": "Story"},
    "parent": {"key": ek},
})
sk = story["key"]
print(f"3. Story: {sk} (parent={ek})")

# 4. Subtask
sub = jr.create_issue(fields={
    "project": {"key": pk}, "summary": "Sub1",
    "description": "d", "issuetype": {"name": "Sub-task"},
    "parent": {"key": sk},
})
print(f"4. Subtask: {sub['key']}")

# 5. Update
jr.update_issue(ek, update={"summary": [{"set": "Epic1 Updated"}]})
print("5. Update: OK")

# 6. Link
jr.create_issue_link(data={
    "type": {"name": "Blocks"},
    "inwardIssue": {"key": ek}, "outwardIssue": {"key": sk},
})
print("6. Link: OK")

# 7. Transitions
ts = jr.get_issue_transitions(ek)
names = [t["name"] for t in ts]
print(f"7. Transitions: {names}")
if ts:
    jr.issue_transition(ek, ts[0]["name"])
    print(f"   Transition to '{ts[0]['name']}': OK")

# 8. Cleanup
jr.delete_project(pk)
print("8. Cleanup: OK")
print("\nALL 8 PASSED")
