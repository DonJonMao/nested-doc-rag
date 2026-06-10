.PHONY: test test-race fmt fmt-check vet build build-api build-worker run-api run-worker docker-up docker-down docker-logs migrate-up migrate-down smoke-api smoke-auth smoke-files smoke-jobs loadtest-smoke ci tidy-check

CONFIG ?= configs/config.example.yaml
COMPOSE ?= docker compose -f deployments/docker-compose.yaml

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

loadtest-smoke:
	k6 run loadtest/k6/smoke.js

tidy-check:
	go mod tidy
	git diff --exit-code go.mod go.sum

ci: fmt-check tidy-check vet test build
