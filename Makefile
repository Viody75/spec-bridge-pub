SHELL := /bin/zsh

.PHONY: dev check deploy deploy-dry-run deploy-setup deploy-backend deploy-web deploy-smoke

# Cloud Run project; override with: make deploy GCP_PROJECT=other-project
GCP_PROJECT ?= hackathon-primestep-01
DEPLOY = GCP_PROJECT=$(GCP_PROJECT) ./scripts/deploy.sh

dev:
	cd specbridge-agent && uv run python ../scripts/run_local.py

check:
	cd specbridge-agent && UV_CACHE_DIR=/tmp/specbridge-uv-cache uv run ruff check app tests migrations
	cd specbridge-agent && UV_CACHE_DIR=/tmp/specbridge-uv-cache uv run pytest tests/unit tests/integration -q
	/bin/zsh -lc 'cd specbridge && ./node_modules/.bin/tsc --noEmit --incremental false'
	/bin/zsh -lc "cd specbridge && ./node_modules/.bin/eslint src/pages/api/specbridge/'[...path].ts' src/specbridge"

# One command: setup → backend (lint, tests, live eval, migration) → web → smoke test
deploy: check
	$(DEPLOY) all

# Read-only checks; prints the commands a deploy would run
deploy-dry-run:
	DRY_RUN=1 $(DEPLOY) all

deploy-setup:
	$(DEPLOY) setup

deploy-backend:
	$(DEPLOY) backend

# Frontend only (backend unchanged)
deploy-web:
	$(DEPLOY) web

deploy-smoke:
	$(DEPLOY) smoke
