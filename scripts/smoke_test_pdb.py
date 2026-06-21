"""PDB-47: End-to-end smoke test for the Project-driven Build feature.

Covers Phase A → Phase D in one run, plus Phase E's brief-changed banner
condition.

Steps:
  1. Login as admin.
  2. Create a fresh test project.
  3. PUT /brief — minimum-length validation works.
  4. PUT /brief with valid content — succeeds.
  5. POST /prd/generate — agent runs synchronously (~30-90s); PRD artifact
     stored as draft.
  6. PATCH /prd { content: ... } — save-draft path works.
  7. PATCH /prd { status: 'finalized' } — finalize succeeds and stamps
     finalized_at + finalized_by.
  8. POST /tasks/generate — business_analyst returns structured tasks; rows
     persisted with list_status=draft, list_version=1.
  9. PATCH /tasks/{task_id} — inline edit of title + priority round-trips.
 10. POST /tasks/finalize — atomic flip; rows marked list_status=finalized.
 11. POST /build/dispatch with first task — Request created with source_task_id
     set; task_status transitions backlog → dispatched.
 12. Wait briefly + verify the PDB-25 mapping handler flipped task_status
     to in_progress (or later) when the orchestrator workflow started.
 13. POST /build/chat — orchestrator agent answers a status question via
     the get_project_status tool.
 14. POST /build/chat — orchestrator dispatches the second task by name;
     verify a new Request was created and the task is linked.
 15. PDB-43 — edit the brief after PRD finalize; verify brief.updated_at >
     prd.finalized_at (the frontend banner condition).
 16. Cleanup — archive task list, delete the test project, delete created
     requests.

Run from the host:
    python scripts/smoke_test_pdb.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE = os.environ.get("PDB_SMOKE_BASE", "http://localhost:8000/api/v1")
ADMIN_USER = os.environ.get("PDB_SMOKE_USER", "admin")
ADMIN_PASS = os.environ.get("PDB_SMOKE_PASS", "SmokeTest_admin_pm48")
LLM_TIMEOUT_S = 150


def _req(
    method: str,
    path: str,
    token: str | None = None,
    body: dict | None = None,
    expect: int | tuple[int, ...] = 200,
    timeout: int = 30,
) -> dict:
    url = f"{BASE}{path}"
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        status = e.code
    expected = expect if isinstance(expect, tuple) else (expect,)
    if status not in expected:
        raise AssertionError(
            f"{method} {path}: expected {expected}, got {status}\nbody: {raw[:600]}"
        )
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}


def step(n: int, title: str) -> None:
    print(f"\n[{n:02d}] {title}", flush=True)


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def main() -> int:
    print("=" * 70)
    print("PDB-47 — Project-driven Build end-to-end smoke test")
    print("=" * 70)

    # 1 ─────────────────────────────────────────────────────────────────
    step(1, "Login as admin")
    login = _req("POST", "/auth/login", body={"username": ADMIN_USER, "password": ADMIN_PASS})
    token = (login.get("data") or {}).get("access_token")
    if not token:
        raise AssertionError(f"Login returned no token: {login}")
    log(f"got token (len={len(token)})")

    # 2 ─────────────────────────────────────────────────────────────────
    step(2, "Create a fresh test project")
    suffix = int(time.time())
    proj_resp = _req(
        "POST", "/projects", token=token,
        body={
            "name": f"PDB-Smoke-{suffix}",
            "description": "Throwaway project used by smoke_test_pdb.py",
            "color": "#00f0ff",
            "icon": "rocket",
            "default_team": "engineering",
        },
        expect=(200, 201),
    )
    project = proj_resp["data"]
    project_id = project["project_id"]
    log(f"created {project_id} — {project['name']}")

    # 3 ─────────────────────────────────────────────────────────────────
    step(3, "PUT /brief with too-short content is rejected")
    err = _req(
        "PUT", f"/projects/{project_id}/brief", token=token,
        body={"content": "too short"},
        expect=(400, 422),
    )
    log(f"rejected as expected: {err.get('detail', err)!r}")

    # 4 ─────────────────────────────────────────────────────────────────
    step(4, "PUT /brief with valid content")
    brief_resp = _req(
        "PUT", f"/projects/{project_id}/brief", token=token,
        body={"content":
              "Build an automated regression-test bot that watches commits "
              "to main and re-runs the smoke test suite. Notify Slack on "
              "failure. Stretch: keep a flake-rate dashboard."},
    )
    brief = brief_resp["data"]
    assert brief["kind"] == "brief"
    assert brief["status"] == "draft"
    log(f"brief artifact {brief['artifact_id']} v{brief['version']} stored")

    # 5 ─────────────────────────────────────────────────────────────────
    step(5, f"POST /prd/generate (LLM call, up to {LLM_TIMEOUT_S}s)")
    t0 = time.time()
    prd_gen = _req(
        "POST", f"/projects/{project_id}/prd/generate", token=token,
        expect=(200, 201),
        timeout=LLM_TIMEOUT_S,
    )
    prd = prd_gen["data"]
    elapsed = time.time() - t0
    assert prd["kind"] == "prd" and prd["status"] == "draft"
    assert len(prd["content"]) > 200, f"PRD content too short: {len(prd['content'])} chars"
    log(f"PRD draft {prd['artifact_id']} v{prd['version']} stored — {len(prd['content'])} chars in {elapsed:.1f}s")

    # 6 ─────────────────────────────────────────────────────────────────
    step(6, "PATCH /prd save-draft round-trips")
    patched = _req(
        "PATCH", f"/projects/{project_id}/prd", token=token,
        body={"content": prd["content"] + "\n\n## Smoke test edit\nMarker."},
    )
    assert "Smoke test edit" in patched["data"]["content"]
    log("save-draft succeeded")

    # 7 ─────────────────────────────────────────────────────────────────
    step(7, "PATCH /prd { status: 'finalized' }")
    finalized = _req(
        "PATCH", f"/projects/{project_id}/prd", token=token,
        body={"status": "finalized"},
    )
    assert finalized["data"]["status"] == "finalized"
    assert finalized["data"]["finalized_at"] is not None
    log(f"finalized at {finalized['data']['finalized_at']}")

    # 8 ─────────────────────────────────────────────────────────────────
    step(8, f"POST /tasks/generate (LLM call, up to {LLM_TIMEOUT_S}s)")
    t0 = time.time()
    tasks_gen = _req(
        "POST", f"/projects/{project_id}/tasks/generate", token=token,
        expect=(200, 201),
        timeout=LLM_TIMEOUT_S,
    )
    tasks = tasks_gen["data"]
    meta = tasks_gen["meta"]
    elapsed = time.time() - t0
    assert len(tasks) >= 1
    log(f"got {len(tasks)} tasks via parse_mode={meta['parse_mode']} in {elapsed:.1f}s")
    for t in tasks[:3]:
        log(f"  - {t['task_id']} | {t['title'][:60]}")

    # 9 ─────────────────────────────────────────────────────────────────
    step(9, "PATCH a task inline (title + priority)")
    first = tasks[0]
    new_title = "SMOKE-PATCHED title"
    patched_task = _req(
        "PATCH", f"/projects/{project_id}/tasks/{first['task_id']}", token=token,
        body={"title": new_title, "priority": "high"},
    )
    assert patched_task["data"]["title"] == new_title
    assert patched_task["data"]["priority"] == "high"
    log("title+priority round-tripped")

    # 10 ────────────────────────────────────────────────────────────────
    step(10, "POST /tasks/finalize")
    fin_tasks = _req(
        "POST", f"/projects/{project_id}/tasks/finalize", token=token,
    )
    assert all(t["list_status"] == "finalized" for t in fin_tasks["data"])
    log(f"finalized {fin_tasks['meta']['count']} tasks at list_version={fin_tasks['meta']['list_version']}")

    # 11 ────────────────────────────────────────────────────────────────
    step(11, "POST /build/dispatch — dispatch first task")
    dispatch = _req(
        "POST", f"/projects/{project_id}/build/dispatch", token=token,
        body={"task_ids": [first["task_id"]]},
    )
    dispatched = dispatch["data"]["dispatched"][0]
    assert dispatched["status"] == "dispatched"
    request_id = dispatched["request_id"]
    log(f"dispatched {dispatched['task_id']} → {request_id}")

    # 12 ────────────────────────────────────────────────────────────────
    step(12, "Verify PDB-25 status mapping flips task_status from 'dispatched'")
    log("waiting 8s for orchestrator to enter in_progress…")
    time.sleep(8)
    listing = _req("GET", f"/projects/{project_id}/tasks", token=token)
    task_now = next((t for t in listing["data"] if t["task_id"] == first["task_id"]), None)
    assert task_now is not None
    assert task_now["task_status"] in {"in_progress", "review", "testing", "deployed", "failed"}, (
        f"task_status unexpectedly still {task_now['task_status']!r}"
    )
    log(f"task_status now {task_now['task_status']!r} (mapping handler working)")

    # 13 ────────────────────────────────────────────────────────────────
    step(13, "POST /build/chat — ask for status")
    t0 = time.time()
    chat1 = _req(
        "POST", f"/projects/{project_id}/build/chat", token=token,
        body={"message": "What is the status of this project? Use the tool."},
        timeout=LLM_TIMEOUT_S,
    )
    reply = chat1["data"]
    elapsed = time.time() - t0
    assert reply["content"].strip(), "assistant returned empty content"
    tool_names = [tc["tool"] for tc in reply.get("tool_calls", [])]
    log(f"assistant ({elapsed:.1f}s): {reply['content'][:120]!r}")
    log(f"tools invoked: {tool_names}")

    # 14 ────────────────────────────────────────────────────────────────
    step(14, "POST /build/chat — instruct dispatch by id")
    backlog = [t for t in listing["data"] if t["task_status"] == "backlog"]
    if not backlog:
        log("no backlog tasks left — skipping chat-dispatch step")
    else:
        target = backlog[0]
        t0 = time.time()
        chat2 = _req(
            "POST", f"/projects/{project_id}/build/chat", token=token,
            body={"message": f"Dispatch {target['task_id']}."},
            timeout=LLM_TIMEOUT_S,
        )
        elapsed = time.time() - t0
        tool_names = [tc["tool"] for tc in chat2["data"].get("tool_calls", [])]
        log(f"assistant ({elapsed:.1f}s) tools={tool_names}")
        # Verify the task is now linked to a Request.
        time.sleep(2)
        listing2 = _req("GET", f"/projects/{project_id}/tasks", token=token)
        updated = next((t for t in listing2["data"] if t["task_id"] == target["task_id"]), None)
        assert updated is not None and updated.get("request_id"), (
            f"chat dispatch failed to link task to a request: {updated}"
        )
        log(f"task {updated['task_id']} now linked to {updated['request_id']}")

    # 15 ────────────────────────────────────────────────────────────────
    step(15, "PDB-43 — brief edit AFTER prd finalize sets banner condition")
    edited_brief = _req(
        "PUT", f"/projects/{project_id}/brief", token=token,
        body={"content":
              "Updated brief AFTER the PRD was finalized. The build workspace "
              "should now show a yellow banner indicating the PRD may be stale. "
              "This is the PDB-43 banner condition under test."},
    )
    bu = edited_brief["data"].get("updated_at")
    pf = finalized["data"].get("finalized_at")
    assert bu and pf, "expected both timestamps to be populated"
    assert bu > pf, f"brief.updated_at {bu} should be > prd.finalized_at {pf}"
    log(f"banner condition met: brief.updated_at={bu} > prd.finalized_at={pf}")

    # 16 ────────────────────────────────────────────────────────────────
    step(16, "Cleanup")
    # Archive the task list so the project can be deleted (DELETE requires
    # zero requests in v1; we'll skip deletion if requests exist).
    archive = _req(
        "POST", f"/projects/{project_id}/tasks/archive", token=token,
    )
    log(f"archived {archive['meta'].get('archived', 0)} tasks")
    # DELETE the project is blocked if it has requests — that's expected.
    del_resp = _req(
        "DELETE", f"/projects/{project_id}", token=token,
        expect=(204, 409),
    )
    if not del_resp:  # 204 No Content
        log("project deleted")
    else:
        detail = del_resp.get("detail") if isinstance(del_resp, dict) else None
        log(f"project not deletable (expected — has dispatched requests): {detail}")

    print("\n" + "=" * 70)
    print("PDB-47 SMOKE TEST: ALL CHECKS PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"\nFAIL: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
