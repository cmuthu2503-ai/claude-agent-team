@echo off
REM Run the deployment supervisor on the HOST (not in Docker).
REM
REM Why on host: the supervisor manages `docker compose` for the dev stack,
REM which uses bind mounts. Bind mount paths only resolve correctly when
REM `docker compose` is invoked from the host filesystem — invoking it from
REM inside a Docker container makes Docker daemon try to mount paths that
REM only exist inside the supervisor container.
REM
REM Prerequisites on host:
REM   - Python 3.12 with `anthropic[aws]>=0.52.0` installed
REM   - Git CLI
REM   - Docker CLI + Compose plugin
REM   - .env file in repo root with ANTHROPIC_AWS_API_KEY, ANTHROPIC_AWS_WORKSPACE_ID,
REM     GITHUB_TOKEN, GITHUB_REPO
REM
REM Run this from the repo root: scripts\run-supervisor.bat

setlocal
cd /d "%~dp0\.."

REM Load .env so subprocess sees the credentials
if exist .env (
    for /f "tokens=1,* delims==" %%a in (.env) do (
        if not "%%a"=="" if not "%%a:~0,1%"=="#" set "%%a=%%b"
    )
)

echo Starting Agent Team deployment supervisor on host
echo Project root: %cd%
echo DB path:      %cd%\data\agent_team.db
echo.

python supervisor\deploy_supervisor.py
