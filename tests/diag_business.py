"""Test business project template for issue types."""
import os, time
from atlassian import Jira

EMAIL = os.environ.get('JIRA_EMAIL', '') or os.environ.get('CONFLUENCE_EMAIL', '')
URL = os.environ.get('JIRA_URL', '')
tok = os.environ.get('JIRA_API_TOKEN', '') or os.environ.get('CONFLUENCE_API_TOKEN', '')
if not tok:
    # fallback: read from any env var ending in _TOKEN that starts with ATATT
    for k, v in os.environ.items():
        if k.endswith('_TOKEN') and v.startswith('ATATT'):
            tok = v
            break

jr = Jira(url=URL, username=EMAIL, password=tok, timeout=15)
me = jr.myself()
aid = me['accountId']
ts = str(int(time.time()))[-4:]
pk = 'BPRJ' + ts
proj = jr.create_project_from_raw_json({
    'key': pk, 'name': pk,
    'projectTypeKey': 'business', 'leadAccountId': aid,
})
print('Project: ' + proj['key'])

for itype in ['Epic', 'Story', 'Task', 'Bug', 'Sub-task']:
    try:
        r = jr.create_issue(fields={
            'project': {'key': pk},
            'summary': itype, 'description': 'x',
            'issuetype': {'name': itype},
        })
        print('  ' + itype + ': OK -> ' + r['key'])
    except Exception as e:
        err = str(e)
        if 'valid issue type' in err.lower():
            print('  ' + itype + ': NOT AVAILABLE')
        else:
            print('  ' + itype + ': ' + err[:100])

jr.delete_project(pk)
print('Cleaned')
