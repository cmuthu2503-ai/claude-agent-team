#!/usr/bin/env bash
# Run the deployment supervisor on the HOST (not in Docker).
#
# Why on host: see scripts/run-supervisor.bat header comment.
#
# Prerequisites: python 3.12, anthropic[aws]>=0.52.0, git, docker, .env file.
# Run from anywhere: scripts/run-supervisor.sh

set -euo pipefail

# Resolve repo root from this script's location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Load .env so the supervisor sees ANTHROPIC_AWS_API_KEY / GITHUB_TOKEN / etc.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

echo "Starting Agent Team deployment supervisor on host"
echo "Project root: $REPO_ROOT"
echo "DB path:      $REPO_ROOT/data/agent_team.db"
echo

exec python supervisor/deploy_supervisor.py
