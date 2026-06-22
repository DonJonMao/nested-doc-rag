.PHONY: test test-race fmt fmt-check vet build build-api build-worker run-api run-worker docker-up docker-down docker-logs docker-prod-config docker-prod-up docker-prod-down docker-prod-logs docker-prod-worker-smoke preflight-prod docker-build docker-push backup-prod restore-prod upgrade-prod rollback-prod migrate-up migrate-down smoke-api smoke-auth smoke-files smoke-jobs smoke-product-flow loadtest-smoke ci tidy-check

CONFIG ?= configs/config.example.yaml
COMPOSE ?= docker compose -f deployments/docker-compose.yaml
COMPOSE_PROD ?= docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod
BACKUP_DIR ?=

test:
	go test ./...

test-race:
	go test -race ./...

fmt:
	gofmt -w $$(find . -name "*.go" -not -path "./vendor/*")

fmt-check:
	@test -z "$$(gofmt -l $$(find . -name "*.go" -not -path "./vendor/*"))" || (gofmt -l $$(find . -name "*.go" -not -path "./vendor/*") && exit 1)

vet:
	go vet ./...

build: build-api build-worker

build-api:
	go build ./cmd/api

build-worker:
	go build ./cmd/worker

run-api:
	go run ./cmd/api --config $(CONFIG)

run-worker:
	go run ./cmd/worker --config $(CONFIG)

docker-up:
	$(COMPOSE) up -d

docker-down:
	$(COMPOSE) down

docker-logs:
	$(COMPOSE) logs -f

docker-prod-config:
	$(COMPOSE_PROD) config

docker-prod-up:
	$(COMPOSE_PROD) up -d

docker-prod-down:
	$(COMPOSE_PROD) down

docker-prod-logs:
	$(COMPOSE_PROD) logs -f api worker

docker-prod-worker-smoke:
	$(COMPOSE_PROD) exec worker python --version
	$(COMPOSE_PROD) exec worker python -c "import nested_doc_rag; print('python core ok')"
	$(COMPOSE_PROD) exec worker sh -lc "cd /app/python-core && python -m nested_doc_rag.cli --help >/tmp/nested_doc_rag_help.txt && head -n 5 /tmp/nested_doc_rag_help.txt"
	$(COMPOSE_PROD) exec worker sh -lc "cd /app/python-core && python -m nested_doc_rag.cli show-config --config config/docker.yaml --json >/tmp/nested_doc_rag_config.json && head -n 20 /tmp/nested_doc_rag_config.json"

preflight-prod:
	./scripts/preflight_prod.sh

docker-build:
	./scripts/build_release.sh

docker-push:
	./scripts/push_release.sh

backup-prod:
	BACKUP_DIR="$(BACKUP_DIR)" ./scripts/backup_prod.sh

restore-prod:
	BACKUP_DIR="$(BACKUP_DIR)" ./scripts/restore_prod.sh

upgrade-prod:
	./scripts/upgrade_prod.sh

rollback-prod:
	BACKUP_DIR="$(BACKUP_DIR)" ./scripts/rollback_prod.sh

migrate-up:
	@echo "Migrations are applied by API startup in the current deployment mode."

migrate-down:
	@echo "Down migrations are intentionally manual; inspect migrations before rollback."

smoke-api:
	./scripts/smoke_api.sh

smoke-auth:
	./scripts/smoke_auth.sh

smoke-files:
	./scripts/smoke_files.sh

smoke-jobs:
	./scripts/smoke_jobs.sh

smoke-product-flow:
	./scripts/smoke_product_flow.sh

loadtest-smoke:
	k6 run loadtest/k6/smoke.js

tidy-check:
	go mod tidy
	git diff --exit-code go.mod go.sum

ci: fmt-check tidy-check vet test build
