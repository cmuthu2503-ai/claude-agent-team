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
