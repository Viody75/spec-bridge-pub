from __future__ import annotations

import getpass
import os
import secrets
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def prepare_environment(
    *,
    env_file: Path = PROJECT_ROOT / ".env",
    prompt: Callable[[str], str] = getpass.getpass,
) -> tuple[str, bool]:
    """Load local config and interactively fill development-only secrets."""
    load_dotenv(env_file, override=False)

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        database_url = prompt("Neon DATABASE_URL (input hidden): ").strip()
        if not database_url:
            raise RuntimeError("DATABASE_URL is required to run the E2E demo")
        os.environ["DATABASE_URL"] = database_url

    api_key = os.getenv("SPECBRIDGE_API_KEY", "").strip()
    generated_api_key = not api_key
    if generated_api_key:
        api_key = secrets.token_urlsafe(24)
        os.environ["SPECBRIDGE_API_KEY"] = api_key

    os.environ.setdefault("AUTO_CREATE_SCHEMA", "false")
    os.environ.setdefault("OTEL_TO_CLOUD", "false")
    os.environ.setdefault("TEXT_EMBEDDING_MODEL", "gemini-embedding-001")
    os.environ.setdefault("WORKFLOW_TIMEOUT_SECONDS", "600")
    return api_key, generated_api_key


def main() -> None:
    api_key, generated_api_key = prepare_environment()

    print("Applying database migrations…", flush=True)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=os.environ.copy(),
        check=True,
    )

    print("\nSpecBridge E2E Demo: http://127.0.0.1:8000/demo", flush=True)
    if generated_api_key:
        print(f"Temporary local API key: {api_key}", flush=True)
    else:
        print("Use SPECBRIDGE_API_KEY from .env as the API key.", flush=True)
    print("Press Ctrl+C to stop.\n", flush=True)

    try:
        subprocess.run(
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
            cwd=PROJECT_ROOT,
            env=os.environ.copy(),
            check=True,
        )
    except KeyboardInterrupt:
        print("\nSpecBridge E2E Demo stopped.", flush=True)


if __name__ == "__main__":
    main()
