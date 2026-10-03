"""Load settings from a `.env` file (this folder or a parent one) into the environment.

Lines are KEY=VALUE; variables that are already set in the environment win. Used for the
Google key and the optional SOLARY_* settings, so nothing secret has to live in the code.
"""

from __future__ import annotations

import os
from pathlib import Path


def find_env_file(start: Path | None = None) -> Path | None:
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / ".env").is_file():
            return folder / ".env"
    return None


def load_env(start: Path | None = None) -> Path | None:
    path = find_env_file(start)
    if path is None:
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip().strip('"').strip("'")
        if key and value:
            os.environ.setdefault(key, value)
    return path
