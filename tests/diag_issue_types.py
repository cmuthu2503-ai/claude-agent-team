"""Check available issue types for Kanban template."""
import os, time
from atlassian import Jira

EMAIL = os.environ["JIRA_EMAIL"]
URL = os.environ["JIRA_URL"]
tok_var_names = ["JIRA_API_TOKEN", "CONFLUENCE_API_TOKEN"]
TOKEN=""
for vn in tok_var_names:
    val = os.environ.get(vn, "")
    if val and len(val) > 10:
        TOKEN=val
        break

jr = Jira(url=URL, username=EMAIL, password=TOKEN, timeout=15)
me = jr.myself()
aid = me["accountId"]

pk = "TSTPRJ" + str(int(time.time()))[-4:]
proj = jr.create_project_from_raw_json({
    "key": pk, "name": "Test " + pk,
    "projectTypeKey": "software", "leadAccountId": aid,
})
print("Project: " + proj["key"])

for itype in ["Epic", "Story", "Task", "Bug", "Sub-task"]:
    try:
        r = jr.create_issue(fields={
            "project": {"key": pk},
            "summary": "Test " + itype,
            "description": "x",
            "issuetype": {"name": itype},
        })
        print("  " + itype + ": OK -> " + r["key"])
    except Exception as e:
        err = str(e)
        if "valid issue type" in err.lower():
            print("  " + itype + ": NOT AVAILABLE")
        else:
            print("  " + itype + ": ERROR - " + err[:120])

jr.delete_project(pk)
print("Cleaned")
