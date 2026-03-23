from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from data_extraction.create_dso_planner_db import create_dso_planner_db


RUNTIME_DB_PATH = Path("./runtime/dso_data.db")


def db_is_initialized(db_path: Path) -> bool:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return False

    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dso'"
            ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def ensure_runtime_db() -> None:
    RUNTIME_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if db_is_initialized(RUNTIME_DB_PATH):
        print(f"Runtime DSO database already initialized at {RUNTIME_DB_PATH}")
        return

    print(f"Initializing runtime DSO database at {RUNTIME_DB_PATH}")
    create_dso_planner_db()

    if not db_is_initialized(RUNTIME_DB_PATH):
        raise RuntimeError(f"Database initialization did not produce a usable DB at {RUNTIME_DB_PATH}")


def main() -> None:
    ensure_runtime_db()

    host = os.environ.get("UVICORN_HOST", "0.0.0.0")
    port = os.environ.get("UVICORN_PORT", "5010")

    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            host,
            "--port",
            port,
        ],
    )


if __name__ == "__main__":
    main()