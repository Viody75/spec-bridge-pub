AGENTS_CLI ?= $(HOME)/.local/bin/agents-cli
REGION ?= asia-southeast1
SERVICE_NAME ?= specbridge-agent
DATABASE_SECRET ?= specbridge-database-url
API_KEY_SECRET ?= specbridge-api-key
TEXT_EMBEDDING_MODEL ?= gemini-embedding-001
# Runtime identity: needs Vertex AI user and access to the two secrets
SERVICE_ACCOUNT ?= hackathon-runtime@$(GCP_PROJECT).iam.gserviceaccount.com
# Analyses run as background jobs, so they are no longer bound by the request timeout
WORKFLOW_TIMEOUT_SECONDS ?= 900

.PHONY: install demo lint test eval migrate migrate-cloud validate-deploy deploy dry-run

install:
	$(AGENTS_CLI) install

demo:
	uv run python scripts/run_demo.py

lint:
	$(AGENTS_CLI) lint

test:
	uv run pytest tests/unit tests/integration -q

eval:
	mkdir -p artifacts
	# Fresh schema every run: AUTO_CREATE_SCHEMA adds missing tables, never new columns
	rm -f artifacts/eval.db
	DISABLE_API_KEY_AUTH=true AUTO_CREATE_SCHEMA=true DATABASE_URL=sqlite+aiosqlite:///./artifacts/eval.db $(AGENTS_CLI) eval run --dataset tests/eval/datasets/basic-dataset.json --config tests/eval/eval_config.yaml

migrate:
	uv run alembic upgrade head

validate-deploy:
	@test -n "$(GCP_PROJECT)" || (echo "GCP_PROJECT is required"; exit 1)
	@command -v gcloud >/dev/null || (echo "gcloud is required"; exit 1)
	@test -x "$(AGENTS_CLI)" || (echo "agents-cli is required at $(AGENTS_CLI)"; exit 1)
	@gcloud secrets describe $(DATABASE_SECRET) --project=$(GCP_PROJECT) >/dev/null
	@gcloud secrets describe $(API_KEY_SECRET) --project=$(GCP_PROJECT) >/dev/null

migrate-cloud: validate-deploy
	DATABASE_URL="$$(gcloud secrets versions access latest --secret=$(DATABASE_SECRET) --project=$(GCP_PROJECT))" uv run alembic upgrade head

dry-run: validate-deploy
	$(AGENTS_CLI) deploy --dry-run --project $(GCP_PROJECT) --region $(REGION) --service-name $(SERVICE_NAME) --service-account $(SERVICE_ACCOUNT) --cpu 1 --memory 2Gi --concurrency 2 --min-instances 0 --max-instances 3 --timeout 300 --secrets DATABASE_URL=$(DATABASE_SECRET):latest,SPECBRIDGE_API_KEY=$(API_KEY_SECRET):latest --env-vars GOOGLE_CLOUD_LOCATION=global,TEXT_EMBEDDING_MODEL=$(TEXT_EMBEDDING_MODEL),AUTO_CREATE_SCHEMA=false,WORKFLOW_TIMEOUT_SECONDS=$(WORKFLOW_TIMEOUT_SECONDS),MODEL_MAX_OUTPUT_TOKENS=8192,OTEL_TO_CLOUD=false

deploy: validate-deploy lint test eval migrate-cloud
	$(AGENTS_CLI) deploy --project $(GCP_PROJECT) --region $(REGION) --service-name $(SERVICE_NAME) --service-account $(SERVICE_ACCOUNT) --cpu 1 --memory 2Gi --concurrency 2 --min-instances 0 --max-instances 3 --timeout 300 --secrets DATABASE_URL=$(DATABASE_SECRET):latest,SPECBRIDGE_API_KEY=$(API_KEY_SECRET):latest --env-vars GOOGLE_CLOUD_LOCATION=global,TEXT_EMBEDDING_MODEL=$(TEXT_EMBEDDING_MODEL),AUTO_CREATE_SCHEMA=false,WORKFLOW_TIMEOUT_SECONDS=$(WORKFLOW_TIMEOUT_SECONDS),MODEL_MAX_OUTPUT_TOKENS=8192,OTEL_TO_CLOUD=false
	gcloud run services update $(SERVICE_NAME) --project=$(GCP_PROJECT) --region=$(REGION) --no-cpu-throttling --quiet
	gcloud run services add-iam-policy-binding $(SERVICE_NAME) --project=$(GCP_PROJECT) --region=$(REGION) --member=allUsers --role=roles/run.invoker --quiet
