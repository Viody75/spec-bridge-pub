#!/usr/bin/env bash
# Deploys SpecBridge to Cloud Run: backend (specbridge-agent) and web (specbridge-web).
#
#   scripts/deploy.sh [all|setup|backend|web|smoke]     (default: all)
#
# all = setup → backend (lint, tests, live eval, migration, deploy) → web → smoke test.
# Every step is safe to repeat. DRY_RUN=1 runs the read-only checks and prints the
# commands that would change something, without running them.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="${GCP_PROJECT:-hackathon-primestep-01}"
REGION="${REGION:-asia-southeast1}"
BACKEND_SERVICE="specbridge-agent"
WEB_SERVICE="specbridge-web"
REPOSITORY="specbridge"
RUNTIME_SA="${SERVICE_ACCOUNT:-hackathon-runtime@${PROJECT}.iam.gserviceaccount.com}"
WEB_SA="specbridge-web-run@${PROJECT}.iam.gserviceaccount.com"
DB_SECRET="specbridge-database-url"
KEY_SECRET="specbridge-api-key"
AGENTS_CLI="${AGENTS_CLI:-$HOME/.local/bin/agents-cli}"
DRY_RUN="${DRY_RUN:-0}"

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
ok() { printf '    ✓ %s\n' "$1"; }
die() { printf '\n\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }
# Runs a command that changes something; in a dry run only prints it
run() {
  printf '    + %s\n' "$*"
  if [[ "$DRY_RUN" != "1" ]]; then "$@"; fi
}
quiet() { "$@" >/dev/null 2>&1; }

preflight() {
  step "Preflight ($PROJECT, $REGION)"
  for tool in gcloud uv node; do
    command -v "$tool" >/dev/null || die "$tool is not installed."
  done
  [[ -x "$AGENTS_CLI" ]] || die "agents-cli not found at $AGENTS_CLI (uv tool install google-agents-cli)."
  [[ -d "$ROOT/specbridge/node_modules" ]] || die "Run 'npm ci' in specbridge/ first."
  local account
  account="$(gcloud config get-value account 2>/dev/null)"
  [[ -n "$account" ]] || die "Not signed in: run 'gcloud auth login' and 'gcloud auth application-default login'."
  quiet gcloud projects describe "$PROJECT" || die "No access to project $PROJECT as $account."
  ok "gcloud as $account"
}

setup() {
  step "Setup (only creates what is missing)"
  if quiet gcloud artifacts repositories describe "$REPOSITORY" --project="$PROJECT" --location="$REGION"; then
    ok "Artifact Registry repository $REPOSITORY"
  else
    run gcloud artifacts repositories create "$REPOSITORY" --project="$PROJECT" --location="$REGION" \
      --repository-format=docker --description="SpecBridge images"
  fi

  if quiet gcloud secrets describe "$DB_SECRET" --project="$PROJECT"; then
    ok "secret $DB_SECRET"
  else
    die "Secret $DB_SECRET is missing. Create it from your Neon connection string (it is not echoed):
    printf '%s' 'postgresql://USER:PASSWORD@HOST/DB?sslmode=require' | \\
      gcloud secrets create $DB_SECRET --project=$PROJECT --replication-policy=automatic --data-file=-"
  fi

  if quiet gcloud secrets describe "$KEY_SECRET" --project="$PROJECT"; then
    ok "secret $KEY_SECRET"
  else
    printf '    + create %s with a random value\n' "$KEY_SECRET"
    if [[ "$DRY_RUN" != "1" ]]; then
      python3 -c 'import secrets,sys; sys.stdout.write(secrets.token_urlsafe(32))' |
        gcloud secrets create "$KEY_SECRET" --project="$PROJECT" --replication-policy=automatic --data-file=- >/dev/null
    fi
  fi

  quiet gcloud iam service-accounts describe "$RUNTIME_SA" --project="$PROJECT" ||
    die "Backend service account $RUNTIME_SA does not exist. It needs Vertex AI user (roles/aiplatform.user); set SERVICE_ACCOUNT=... to use another."
  ok "backend runs as $RUNTIME_SA"
  if quiet gcloud iam service-accounts describe "$WEB_SA" --project="$PROJECT"; then
    ok "web runs as $WEB_SA"
  else
    run gcloud iam service-accounts create "${WEB_SA%%@*}" --project="$PROJECT" --display-name="SpecBridge web (Cloud Run)"
  fi

  # Secret-level access only: the web service reads the API key, never the database URL
  for binding in "$DB_SECRET:$RUNTIME_SA" "$KEY_SECRET:$RUNTIME_SA" "$KEY_SECRET:$WEB_SA"; do
    run gcloud secrets add-iam-policy-binding "${binding%%:*}" --project="$PROJECT" \
      --member="serviceAccount:${binding#*:}" --role=roles/secretmanager.secretAccessor --quiet --format=none
  done
}

backend() {
  step "Backend: lint, tests, live eval, migration, deploy"
  if [[ "$DRY_RUN" == "1" ]]; then
    make -C "$ROOT/specbridge-agent" dry-run GCP_PROJECT="$PROJECT" REGION="$REGION" SERVICE_ACCOUNT="$RUNTIME_SA"
  else
    make -C "$ROOT/specbridge-agent" deploy GCP_PROJECT="$PROJECT" REGION="$REGION" SERVICE_ACCOUNT="$RUNTIME_SA"
  fi
}

backend_url() {
  gcloud run services describe "$BACKEND_SERVICE" --project="$PROJECT" --region="$REGION" --format='value(status.url)' 2>/dev/null
}

web() {
  step "Web: build image and deploy"
  local url tag image
  url="$(backend_url)"
  [[ -n "$url" ]] || die "Backend $BACKEND_SERVICE is not deployed yet; run 'scripts/deploy.sh backend' first."
  # Tag with the commit, marked -dirty when there are uncommitted changes, so a revision can be traced and rolled back
  tag="$(git -C "$ROOT/specbridge" rev-parse --short HEAD)"
  [[ -z "$(git -C "$ROOT/specbridge" status --porcelain)" ]] || tag="${tag}-dirty"
  tag="${tag}-$(date +%Y%m%d%H%M%S)"
  image="${REGION}-docker.pkg.dev/${PROJECT}/${REPOSITORY}/${WEB_SERVICE}:${tag}"

  step "Lint and type-check the frontend before the cloud build"
  (cd "$ROOT/specbridge" && ./node_modules/.bin/tsc --noEmit --incremental false && ./node_modules/.bin/next lint --dir src --quiet)
  ok "tsc and next lint"

  run gcloud builds submit "$ROOT/specbridge" --project="$PROJECT" --region="$REGION" \
    --config="$ROOT/specbridge/cloudbuild.web.yaml" --substitutions="_IMAGE=${image}"
  run gcloud run deploy "$WEB_SERVICE" --project="$PROJECT" --region="$REGION" --image="$image" \
    --service-account="$WEB_SA" --set-secrets="SPECBRIDGE_API_KEY=${KEY_SECRET}:latest" \
    --set-env-vars="SPECBRIDGE_BACKEND_URL=${url}" --port=8080 --memory=1Gi --cpu=1 \
    --min-instances=0 --max-instances=3 --timeout=300 --allow-unauthenticated --quiet
}

smoke() {
  step "Smoke test"
  local api web health
  api="$(backend_url)"
  web="$(gcloud run services describe "$WEB_SERVICE" --project="$PROJECT" --region="$REGION" --format='value(status.url)' 2>/dev/null)"
  [[ -n "$api" && -n "$web" ]] || die "Both services must be deployed before the smoke test."
  if [[ "$DRY_RUN" == "1" ]]; then
    ok "skipped in a dry run ($api, $web)"
    return
  fi
  health="$(curl -fsS "$api/health")" || die "Backend /health did not answer."
  [[ "$health" == *'"status":"ok"'* && "$health" == *'"database_configured":true'* ]] || die "Backend unhealthy: $health"
  ok "backend health: $health"
  [[ "$(curl -s -o /dev/null -w '%{http_code}' "$api/v1/projects")" == "401" ]] || die "Backend answers /v1 without an API key."
  ok "backend rejects requests without an API key"
  [[ "$(curl -s -o /dev/null -w '%{http_code}' "$web/api/specbridge/v1/projects")" == "200" ]] || die "Web proxy cannot reach the backend."
  ok "web proxy reaches the backend"
  printf '\n\033[32mDeployed.\033[0m\n    Web: %s\n    API: %s\n' "$web" "$api"
}

command="${1:-all}"
case "$command" in
  all) preflight; setup; backend; web; smoke ;;
  setup) preflight; setup ;;
  backend) preflight; backend ;;
  web) preflight; web; smoke ;;
  smoke) smoke ;;
  *) die "Unknown command '$command'. Use: all, setup, backend, web or smoke." ;;
esac
