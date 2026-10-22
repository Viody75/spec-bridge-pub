from __future__ import annotations

import getpass
import os
import secrets
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "specbridge-agent"
FRONTEND = ROOT / "specbridge"


def prepare_environment(
    env_file: Path = AGENT / ".env",
    prompt: Callable[[str], str] = getpass.getpass,
) -> bool:
    load_dotenv(env_file, override=False)
    if not os.getenv("DATABASE_URL", "").strip():
        database_url = prompt("Neon DATABASE_URL (input hidden): ").strip()
        if not database_url:
            raise RuntimeError("DATABASE_URL is required")
        os.environ["DATABASE_URL"] = database_url

    generated_api_key = not bool(os.getenv("SPECBRIDGE_API_KEY", "").strip())
    if generated_api_key:
        os.environ["SPECBRIDGE_API_KEY"] = secrets.token_urlsafe(32)

    return generated_api_key


def main() -> None:
    generated_api_key = prepare_environment()

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=AGENT,
        check=True,
    )

    backend_env = os.environ.copy()
    backend_env.setdefault("AUTO_CREATE_SCHEMA", "false")
    backend_env.setdefault("OTEL_TO_CLOUD", "false")
    frontend_env = os.environ.copy()
    frontend_env.update(
        {
            "NEXT_PUBLIC_SPECBRIDGE_DATA_MODE": "backend",
            "SPECBRIDGE_BACKEND_URL": "http://127.0.0.1:8000",
            "NEXT_TELEMETRY_DISABLED": "1",
        }
    )

    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.fast_api_app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            cwd=AGENT,
            env=backend_env,
        ),
        subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=FRONTEND,
            env=frontend_env,
        ),
    ]
    print("\nSpecBridge frontend: http://localhost:3000/hexadash-nextjs")
    print("SpecBridge backend:  http://127.0.0.1:8000/docs")
    if generated_api_key:
        print("A temporary local API key was generated for this session.")
    print("Press Ctrl+C to stop both services.\n", flush=True)

    def stop(*_args: object) -> None:
        for process in processes:
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
    finally:
        stop()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        failed = next(
            (process.returncode for process in processes if process.returncode), 0
        )
        if failed:
            raise SystemExit(failed)


if __name__ == "__main__":
    main()
