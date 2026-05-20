# {{PROJECT_NAME}}

Scaffolded by the Agent Team platform. FastAPI service — no frontend.

## Allocated port

- **Backend:** http://localhost:{{BACKEND_PORT}}

## Run it

```bash
docker compose up -d --build
```

Then `curl http://localhost:{{BACKEND_PORT}}/` — you'll see
"Hello from {{PROJECT_NAME}}". Healthcheck at `/health`.

OpenAPI docs at http://localhost:{{BACKEND_PORT}}/docs (FastAPI default).

To stop: `docker compose down`.

## Structure

```
.
├── docker-compose.yml
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       └── main.py              FastAPI entry point — extend this
├── docs/
│   ├── PRD.md
│   └── tasks.md
└── README.md
```
