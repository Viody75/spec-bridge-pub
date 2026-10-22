# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import contextlib
import hmac
import os
from collections.abc import AsyncIterator

from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner

from app.api import router as specbridge_router
from app.app_utils import services
from app.app_utils.a2a import attach_a2a_routes
from app.config import get_settings
from app.infrastructure.repository import ConflictError, NotFoundError, get_repository
from app.services.jobs import AnalysisWorker

load_dotenv()
allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)
otel_to_cloud = os.getenv("OTEL_TO_CLOUD", "false").lower() == "true"

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from app.agent import app as adk_app
    from app.agent import root_agent

    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )
    app.state.runner = runner
    app.state.agent_app_name = adk_app.name
    settings = get_settings()
    if settings.database_url and settings.auto_create_schema:
        await get_repository().create_schema()
    await attach_a2a_routes(
        app,
        agent=root_agent,
        runner=runner,
        task_store=InMemoryTaskStore(),
        rpc_path=f"/a2a/{adk_app.name}",
    )
    worker = None
    if settings.database_url and settings.analysis_worker_enabled:
        worker = AnalysisWorker(runner=runner, app_name=adk_app.name)
        worker.start()
    app.state.analysis_worker = worker
    try:
        yield
    finally:
        if worker:
            await worker.stop()


app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=False,
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    allow_origins=allow_origins,
    session_service_uri=services.SESSION_SERVICE_URI,
    otel_to_cloud=otel_to_cloud,
    lifespan=lifespan,
)
app.title = "specbridge-agent"
app.description = "API for interacting with the Agent specbridge-agent"

# Replace the generic ADK health response with the persistence-aware MVP contract.
app.router.routes = [
    route
    for route in app.router.routes
    if getattr(route, "path", None) not in {"/health", "/run_live"}
]
app.include_router(specbridge_router)


@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    public_paths = {"/health", "/demo", "/docs", "/openapi.json", "/redoc"}
    settings = get_settings()
    if (
        not settings.disable_api_key_auth
        and request.method != "OPTIONS"
        and request.url.path not in public_paths
    ):
        configured = settings.specbridge_api_key
        if configured is None:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "API authentication is not configured"},
            )
        supplied = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(supplied, configured.get_secret_value()):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "invalid API key"},
            )
    return await call_next(request)


@app.exception_handler(NotFoundError)
async def not_found_handler(_request: Request, exc: NotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ConflictError)
async def conflict_handler(_request: Request, exc: ConflictError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


# Main execution
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
