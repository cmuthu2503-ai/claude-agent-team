# Design: Per-Project Code Working Trees

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 0.1 |
| Created Date | 2026-05-20 |
| Last Updated | 2026-05-20 |
| Status | Draft — pending approval |
| Product Owner | Chandramouli |

---

## 1. Problem Statement

Today, when a user creates a project (e.g. "CrewAIAgentTeam"), finalizes a PRD + task list, and dispatches tasks for the agent team to build, the **generated source code does not land in `C:/ai-projects/CrewAIAgentTeam/`**. Only the PRD and task markdown files live there.

Instead, every project's generated code is written into the **platform repo's own working tree** at `C:/ai-projects/claude-agent-team/`. The GitHub commits go to the project's own repo (per WS-09), but the local disk view is wrong:

| Surface | Current | Expected |
|---|---|---|
| `C:/ai-projects/<Project>/docs/PRD.md` | ✓ written | ✓ written |
| `C:/ai-projects/<Project>/docs/tasks.md` | ✓ written | ✓ written |
| `C:/ai-projects/<Project>/<source files>` | ✗ NOT written (lands in platform tree) | ✓ should be written |
| `github.com/<owner>/<project-repo>` | ✓ commits go here | ✓ commits go here |

### Failure modes of the current arrangement

1. **Multiple projects collide.** Concurrent dispatches from two different projects both write into `/app/frontend/src/...` and `/app/src/...`, last-writer-wins.
2. **HMR bleed.** The platform's Vite dev server hot-reloads on every project's frontend edit, so Project A's "add a login page" task makes Project B's running UI flicker mid-build.
3. **User can't find the code.** The natural assumption — "my project is at `C:/ai-projects/MyProject/`" — is wrong. The code is buried in a folder owned by the platform.
4. **Off-scope edits.** Today the platform repo's own files (`config/`, `Dockerfile`, etc.) are reachable from agent emissions and have to be guarded by a hard prefix denylist (`_GUARDED_PATH_PREFIXES` in `code_writer.py`). With isolated trees, those paths simply don't exist in scope.

---

## 2. Goal

Make every project's generated source live at **`C:/ai-projects/<ProjectName>/<rel_path>`** on the host, mirrored to the project's own GitHub repo. The platform repo stays unchanged by project builds.

---

## 3. Proposed Layout

### 3.1 On the host (Windows)

```
C:/ai-projects/
├── claude-agent-team/                ← the platform (this repo)
│   ├── src/ … frontend/ … docs/ …
│
├── CrewAIAgentTeam/                  ← Project A's working tree
│   ├── .git/                          (cloned from project_repo, or git init'd)
│   ├── docs/
│   │   ├── PRD.md                     (already written by PM-finalize)
│   │   └── tasks.md                   (already written by PM-finalize)
│   ├── README.md                      (from auto_init=true on repo create)
│   ├── src/ …                         ← agent-written code
│   ├── frontend/ …                    ← agent-written code
│   └── tests/ …
│
└── ThemesUIRedesign/                 ← Project B's working tree
    └── …
```

### 3.2 Inside the backend container

Already bind-mounted from the PM-finalize feature:

```
C:/ai-projects   →   /host/ai-projects
```

So inside the container, Project A is at `/host/ai-projects/CrewAIAgentTeam/`.

---

## 4. Design

### 4.1 `CodeWriter` accepts a per-call `project_root`

Today `CodeWriter.__init__` captures `root = Path(".")` once at startup. Change `commit_code(...)` to accept an optional `project_root: Path` parameter; when provided, it overrides `self.root` for that call. The shared singleton on `Orchestrator` continues to work, but per-request the orchestrator passes the resolved project root.

```python
# src/core/code_writer.py
async def commit_code(
    self,
    request_id: str,
    description: str,
    agent_outputs: dict[str, str],
    repo: str | None = None,
    project_root: Path | None = None,   # NEW
) -> DeploymentState:
    root = project_root or self.root
    # …all file ops use `root` instead of `self.root`…
```

All the existing path-traversal guards (`..`, leading `/`) keep working because they check the *relative* path before joining with `root`.

### 4.2 Orchestrator resolves the project root

In `_handle_code_commit` (`src/core/orchestrator.py:415-497`), the orchestrator already looks up `project.repo_url` to route the GitHub commit. Extend that block to also resolve the host filesystem root:

```python
project_root: Path | None = None
if req_for_repo and req_for_repo.project_id:
    proj = await self.state.get_project(req_for_repo.project_id)
    if proj:
        # Re-use the same validator that finalize uses, so project
        # names that pass validation map to a safe host path.
        from src.core.project_workspace import project_root_dir
        project_root = project_root_dir(proj.name)

dep_state = await self._code_writer.commit_code(
    request_id, description, agent_outputs,
    repo=target_repo,
    project_root=project_root,
)
```

Adds one helper to `project_workspace.py`:

```python
def project_root_dir(project_name: str) -> Path:
    """`<host>/<ProjectName>/` — the working tree root for a project's
    code. Parent of `docs/`, peer of `.git/`."""
    safe = validate_name(project_name)
    return _host_root() / safe
```

### 4.3 Working-tree initialization (one-shot, idempotent)

Before the first commit lands, the project tree should be a real git working copy so the user can `git pull`, branch, etc. New helper, called from `commit_code` when `project_root` is provided and the directory either doesn't exist or has no `.git/`:

```python
async def ensure_working_tree(project_root: Path, repo_url: str | None) -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    if (project_root / ".git").exists():
        return  # Already initialized
    if repo_url:
        # Shallow clone — fast, and we don't care about history for
        # the first build.
        await _run_cmd(f"git clone --depth 1 {repo_url} {project_root}")
    else:
        await _run_cmd(f"git init {project_root}")
```

Git is **not currently installed in the backend image**. Options:
- **A. Install `git` in `Dockerfile.backend`** (one-line `apt-get install -y git`).
- **B. Use the GitHub Contents API** to read the project repo's tree and download blobs into the working dir — no git CLI required. Slower for big repos, but matches the existing "no git CLI" stance of `GitHubPublisher`.
- **C. Skip cloning.** First commit just creates the dir, writes agent files, pushes to GitHub via Trees API (same as today). User does a manual `git clone` themselves if they want a working tree. Simplest, defers the question.

**Recommendation:** **C for v1**, revisit if users complain. Keeps the change small. The user already has the repo URL surfaced in the UI; cloning is one CLI command.

### 4.4 Build-pipeline adjustments

`commit_code` runs three local validation steps after writing files:

| Step | What it does today | What it should do per-project |
|---|---|---|
| `_compile_python` | `ruff check <files> --select E,F` against platform's `pyproject.toml` | Probe for `pyproject.toml` / `ruff.toml` in `project_root`. If missing → skip with `python_compiled: skipped (no ruff config in project)`. Else run `ruff check` with `--config <project>/pyproject.toml`. |
| `_compile_typescript` | `cd frontend && npm run build` against platform's `frontend/` | Probe for `<project>/package.json`. If missing → skip with `typescript_compiled: skipped`. Else `cd <project> && npm install --no-audit --no-fund && npm run build`. |
| `_run_tests` | `pytest tests/test_*.py -x -q` | Probe for `<project>/tests/`. If missing → skip. Else `pytest <project>/tests/...`. |

The agent's brief should mention what kind of project this is — eventually we'll have project templates, but for v1 a sensible default is: **if the agent emits `package.json`, the next commit's typescript build will run; if it emits `pyproject.toml`, ruff will run**. Project bootstraps itself one commit at a time.

### 4.5 Guarded path enforcement (simplification)

The current `_GUARDED_PATH_PREFIXES` denylist exists because every project shared the platform's tree. With isolated per-project trees, **none of those paths exist in scope** — there's no `config/agents/` to protect because the project tree only has what the agent put there. The denylist can be kept as defense-in-depth (paths starting with `..`, absolute paths) but the platform-specific entries (`config/agents/`, `supervisor/`, `.github/`, `Dockerfile*`, `docker-compose*`) become irrelevant for project commits. Easiest: keep the denylist, just acknowledge in comments that it's mostly inert for projects.

### 4.6 GitHub publish (unchanged)

`GitHubPublisher.commit_files(repo=target_repo)` already does the right thing — commits the file contents (read from the per-project root now) to the project's repo. No change.

### 4.7 Platform / non-project requests

When a request has no `project_id` (legacy or platform-internal), `project_root` is `None` and `CodeWriter` falls back to `self.root = Path(".")`. Existing behavior preserved.

---

## 5. Migration / Rollout

This change is backward-compatible for the platform itself. Per-project requests that previously wrote into the platform tree will now write into `C:/ai-projects/<Project>/`. Phased rollout:

| Phase | Scope | Risk |
|---|---|---|
| **P0** | Helper + plumbing only. Add `project_root` parameter to `CodeWriter.commit_code`. No orchestrator wiring yet — `project_root` always `None`. | Zero — code path unchanged. |
| **P1** | Orchestrator passes `project_root` when the request has a `project_id`. Working-tree directory is created if missing, but **no git init / clone** (option C from §4.3). Build steps probe for project-local configs; skip if absent. | Low — project commits now land in `/host/ai-projects/<Project>/` instead of `/app/`. Mismatch with what the GitHub commit contains until §4.3 clone lands. |
| **P2** | Add a `POST /projects/:id/init-workspace` endpoint that clones the repo into the project root (using GitHub Contents API, no git CLI dependency). Idempotent. UI button on Project Detail. | Medium — needs the Contents API code path and a way to surface progress. |
| **P3** | Project templates (Python backend / React frontend / hybrid) scaffolded at project creation time so the first commit already has a real toolchain. | Higher — design churn likely. Defer until P1/P2 stabilize. |

---

## 6. Open Questions

1. **Project lifecycle.** What happens when a project is deleted? Today the SQLite row goes away but the `C:/ai-projects/<Project>/docs/` files stay. Should code deletion also leave the tree (safer — user might have local edits) or wipe it (cleaner)? Default: **leave it**, surface a "Delete local workspace too?" checkbox on the project delete dialog.
2. **Concurrent dispatches.** Two tasks for the same project run in parallel. Both write into the same project tree. File-level conflicts → last-writer-wins (same as today). Acceptable for v1; add a per-project dispatch lock in P3 if it causes issues.
3. **Renaming a project.** `validate_name` lets the user change a project's name. If they rename "Foo" → "Bar", the folder `C:/ai-projects/Foo/` is orphaned. Options: (a) reject rename if `<Foo>/` has any files, (b) `os.rename` the folder atomically, (c) leave it and create a new `Bar/` folder. Default: **option (c)** — least surprising, user can clean up manually.
4. **Supervisor scope.** The deploy supervisor watches `deployment_states` and runs the staging→prod rollout on **the platform repo**. With per-project trees, what does "deploy" mean for a project? Likely the supervisor should skip project deployments entirely (the project has its own GitHub Actions / Vercel / whatever) and only handle platform deployments. Surface in the Deployment Judge LLM as a `skip_project_deployments` strategy.

---

## 7. Out of scope (for now)

- **Cross-project shared libraries.** If two projects want to share a utility module, that's a monorepo question, not solved here.
- **Project-specific Docker compose for the supervisor to deploy.** Today the supervisor knows how to build the platform's compose stack. Building an arbitrary project's stack would require the project to declare its build/deploy steps in a manifest (e.g. `agent-team-project.yaml`). Defer to P3.
- **Existing-codebase projects.** Today there's no flow to "import" an existing GitHub repo as a project. P2's clone step is the foundation for that, but the import UX (pick repo, map directory → project) is separate.

---

## 8. Acceptance criteria for P0 + P1

When a user creates `MyProject`, finalizes a PRD with a task "create a simple React counter component", finalizes the task list, and dispatches it:

- [ ] `C:/ai-projects/MyProject/` exists on the host
- [ ] The agent's emitted `frontend/src/components/Counter.tsx` lands at `C:/ai-projects/MyProject/frontend/src/components/Counter.tsx` (NOT `C:/ai-projects/claude-agent-team/frontend/src/components/Counter.tsx`)
- [ ] The GitHub commit on `github.com/<owner>/myproject` contains that same file
- [ ] The platform's own `frontend/src/` directory is unchanged by the dispatch
- [ ] If no `pyproject.toml` exists in the project tree, `python_compiled` step is recorded as `skipped (no project config)` instead of running ruff against the platform
- [ ] Legacy requests without a `project_id` still write to `/app/` as before
- [ ] Smoke test: create two projects, dispatch a task in each, verify their files don't collide
