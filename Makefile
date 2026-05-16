.PHONY: dev down build logs logs-backend logs-frontend shell-backend shell-frontend status clean staging prod demo down-all rollback health seed-demo help

# ============================================
# Local Development
# ============================================

dev: ## Start local dev stack (backend + frontend)
	docker compose up --build -d
	@echo ""
	@echo "  Backend:  http://localhost:8000/api/v1/health"
	@echo "  Frontend: http://localhost:3000"
	@echo "  API Docs: http://localhost:8000/api/v1/docs"
	@echo ""

down: ## Stop local dev stack
	docker compose down

build: ## Rebuild all images (no cache)
	docker compose build --no-cache

logs: ## Tail all container logs
	docker compose logs -f

logs-backend: ## Tail backend logs only
	docker compose logs -f backend

logs-frontend: ## Tail frontend logs only
	docker compose logs -f frontend

shell-backend: ## Open shell in backend container
	docker compose exec backend bash

shell-frontend: ## Open shell in frontend container
	docker compose exec frontend sh

status: ## Show status of all containers across all environments
	@echo "=== Local Dev ==="
	@docker compose ps 2>/dev/null || echo "  Not running"
	@echo ""
	@echo "=== Staging ==="
	@docker compose -f docker-compose.staging.yml ps 2>/dev/null || echo "  Not running"
	@echo ""
	@echo "=== Production ==="
	@docker compose -f docker-compose.prod.yml ps 2>/dev/null || echo "  Not running"
	@echo ""
	@echo "=== Demo ==="
	@docker compose -f docker-compose.demo.yml ps 2>/dev/null || echo "  Not running"

clean: ## Remove all containers, volumes, and images for this project
	docker compose down -v --rmi local
	@echo "Cleaned local dev environment"

# ============================================
# Supervisor (standalone — survives app rebuilds)
# ============================================

supervisor: ## Start the deployment supervisor on the HOST (foreground)
	@echo "Starting supervisor on host (NOT in Docker)."
	@echo "See CLAUDE.md 'Supervisor scope' for why host execution is required."
	@bash scripts/run-supervisor.sh

supervisor-bg: ## Start the supervisor in the background (logs → supervisor.log)
	@nohup bash scripts/run-supervisor.sh > supervisor.log 2>&1 &
	@echo "Supervisor started in background. Logs: tail -f supervisor.log"

supervisor-stop: ## Kill the host supervisor process
	@pkill -f "python supervisor/deploy_supervisor.py" || echo "No supervisor running"

supervisor-container-deprecated: ## DEPRECATED: container-based supervisor — see CLAUDE.md
	@echo "ERROR: The containerized supervisor is deprecated."
	@echo "The supervisor must run on the host to manage dev's bind-mounted compose."
	@echo "Use 'make supervisor' (foreground) or 'make supervisor-bg' (background) instead."
	@exit 1

# ============================================
# Staging / Production / Demo (P6)
# ============================================

staging: ## Deploy staging environment
	docker compose -f docker-compose.staging.yml up --build -d

prod: ## Deploy production environment
	docker compose -f docker-compose.prod.yml up --build -d

demo: ## Deploy demo environment with seed data
	docker compose -f docker-compose.demo.yml up --build -d

down-all: ## Stop all environments
	docker compose down 2>/dev/null; \
	docker compose -f docker-compose.staging.yml down 2>/dev/null; \
	docker compose -f docker-compose.prod.yml down 2>/dev/null; \
	docker compose -f docker-compose.demo.yml down 2>/dev/null; \
	echo "All environments stopped"

# ============================================
# Utilities
# ============================================

health: ## Check backend health endpoint
	@curl -s http://localhost:8000/api/v1/health | python -m json.tool 2>/dev/null || echo "Backend not reachable"

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
