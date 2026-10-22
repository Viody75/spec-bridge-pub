# Deploying SpecBridge to Cloud Run

From this folder:

```bash
make deploy
```

That one command runs, in order, and stops at the first failure:

1. `make check`: backend lint and tests, frontend type check and lint.
2. Setup: creates whatever is missing (Artifact Registry repo, API-key secret, web service account, secret access). Nothing is changed when it already exists.
3. Backend (`specbridge-agent`): lint, tests, the live agent eval (Gemini calls, a few minutes), Alembic migration on Neon, deploy, CPU always on for background jobs, public access (business endpoints still need the API key).
4. Web (`specbridge-web`): builds the Next.js image on Cloud Build in backend mode, deploys it pointing at the backend.
5. Smoke test: backend health and database, API-key check, web proxy to backend.

A full run takes about 15–20 minutes, mostly the eval and the two image builds.

## Other commands

| Command | What it does |
|---|---|
| `make deploy-dry-run` | All read-only checks; prints the commands a deploy would run. Changes nothing. |
| `make deploy-web` | Frontend only, when the backend did not change (about 7 minutes). |
| `make deploy-backend` | Backend only, with its lint, tests, eval and migration. |
| `make deploy-smoke` | Checks the live services. |
| `make deploy GCP_PROJECT=other-project` | Another GCP project. |

## Before the first deploy on a new machine

- `gcloud auth login` and `gcloud auth application-default login`, with access to the project.
- `uv tool install google-agents-cli` (backend deploy) and `npm ci` in `specbridge/`.
- The Neon connection string in Secret Manager (the setup step stops and shows this command when it is missing):

  ```bash
  printf '%s' 'postgresql://USER:PASSWORD@HOST/DB?sslmode=require' | \
    gcloud secrets create specbridge-database-url --project=hackathon-primestep-01 \
    --replication-policy=automatic --data-file=-
  ```

## Rotating the database password

Reset it in the Neon console, then add a new version and redeploy the backend:

```bash
printf '%s' 'NEW_CONNECTION_STRING' | \
  gcloud secrets versions add specbridge-database-url --project=hackathon-primestep-01 --data-file=-
make deploy-backend
```

## Notes

- The backend runs as `hackathon-runtime`, the project's service account with Vertex AI access. Use another with `SERVICE_ACCOUNT=...`; it needs `roles/aiplatform.user`.
- Web images are tagged `<commit>-<time>`, with `-dirty` when `specbridge/` has uncommitted changes, so every revision can be traced. Roll back in the Cloud Run console (Revisions) or with `gcloud run services update-traffic`.
- The web app is public and uses the demo sign-in. Anyone with its URL can read and change project data through it.
