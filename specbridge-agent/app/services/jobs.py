from __future__ import annotations

import asyncio
import contextlib
import logging

from google.adk.runners import Runner

from app.config import get_settings
from app.infrastructure.repository import Repository, get_repository
from app.services.analysis import AnalysisService, get_analysis_service

logger = logging.getLogger(__name__)


class AnalysisWorker:
    """Runs queued analyses in the background of the API process.

    The queue lives in the `analysis_runs` table, so any instance can pick up a
    job and the frontend reads progress from the database. On Cloud Run the
    service needs CPU outside requests (`--no-cpu-throttling`) for this to run
    at full speed.
    """

    def __init__(
        self,
        *,
        runner: Runner,
        app_name: str,
        service: AnalysisService | None = None,
        repository: Repository | None = None,
    ) -> None:
        self._runner = runner
        self._app_name = app_name
        self._service = service or get_analysis_service()
        self._repository = repository or get_repository()
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        settings = get_settings()
        self._service.set_enqueue_listener(self.wake)
        self._tasks = [
            asyncio.create_task(self._loop(), name=f"analysis-worker-{index}")
            for index in range(settings.analysis_worker_concurrency)
        ]
        self._tasks.append(asyncio.create_task(self._reap(), name="analysis-reaper"))

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        self._service.set_enqueue_listener(None)
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []

    async def run_once(self) -> bool:
        """Claim and run one queued job; False when the queue is empty."""
        run_id = await self._repository.claim_next_run()
        if run_id is None:
            return False
        try:
            await self._service.execute_claimed(
                run_id, runner=self._runner, app_name=self._app_name
            )
        except Exception:
            # The service already marked the run failed with a public message
            logger.exception("analysis run %s failed", run_id)
        return True

    async def _loop(self) -> None:
        poll = get_settings().analysis_worker_poll_seconds
        while True:
            try:
                if await self.run_once():
                    continue
            except Exception:
                logger.exception("analysis worker could not claim a job")
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=poll)

    async def _reap(self) -> None:
        settings = get_settings()
        while True:
            try:
                failed = await self._repository.fail_stale_runs(
                    settings.analysis_job_stale_seconds
                )
                if failed:
                    logger.warning("marked %s stale analysis run(s) as failed", failed)
            except Exception:
                logger.exception("stale analysis check failed")
            await asyncio.sleep(60)
