# {{PROJECT_NAME}}

Scaffolded by the Agent Team platform. Full-stack web app with a FastAPI
backend and a Vite + React frontend.

## Allocated ports

- **Backend:** http://localhost:{{BACKEND_PORT}}
- **Frontend:** http://localhost:{{FRONTEND_PORT}}

These were claimed at project creation and are pinned for the life of
this project. They're free of conflict with the Agent Team platform
(:8000 / :3000) and reserved environment ports.

## Run it

From this directory:

```bash
docker compose up -d --build
```

Then open http://localhost:{{FRONTEND_PORT}} in a browser. You should see
"Hello from {{PROJECT_NAME}}" — that's the scaffold confirming
end-to-end works before any agent code lands.

To stop: `docker compose down`.

## Structure

```
.
├── docker-compose.yml          Per-project stack (2 services)
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       └── main.py              FastAPI entry point — extend this
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.ts           Vite config + /api proxy → backend
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx              React entry — extend this
│       └── index.css
├── docs/
│   ├── PRD.md                   Finalized PRD (written by platform)
│   └── tasks.md                 Finalized task list (written by platform)
└── README.md                    This file
```

## Working with the Agent Team platform

Code emissions from project tasks land **in this directory**, not in
the Agent Team platform's tree. Whole-file replacement: when an agent
writes `frontend/src/App.tsx`, the version here is replaced. Same for
backend files.

Commits are pushed to this project's own GitHub repo on every successful
task. The platform's deploy supervisor does **not** touch this stack —
deploys happen via the "Deploy" button on the project's detail page
(which runs the `docker compose` commands above for you).
