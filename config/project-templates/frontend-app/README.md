# {{PROJECT_NAME}}

Scaffolded by the Agent Team platform. Vite + React frontend — no backend.

## Allocated port

- **Frontend:** http://localhost:{{FRONTEND_PORT}}

## Run it

```bash
docker compose up -d --build
```

Then open http://localhost:{{FRONTEND_PORT}}. To stop: `docker compose down`.

## Structure

```
.
├── docker-compose.yml
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx              React entry — extend this
│       └── index.css
├── docs/
│   ├── PRD.md
│   └── tasks.md
└── README.md
```
