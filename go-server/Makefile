.PHONY: test run-api run-worker docker-up docker-down lint migrate-up

CONFIG ?= configs/config.example.yaml
COMPOSE ?= docker compose -f deployments/docker-compose.yaml

test:
	go test ./...

run-api:
	go run ./cmd/api --config $(CONFIG)

run-worker:
	go run ./cmd/worker --config $(CONFIG)

docker-up:
	$(COMPOSE) up -d

docker-down:
	$(COMPOSE) down

lint:
	@echo "TODO(Block 8): wire golangci-lint or equivalent."

migrate-up:
	@echo "TODO(Block 0): run goose -dir migrations postgres $$DATABASE_DSN up"
