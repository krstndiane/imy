"""config.py - loads settings from the `.env` file in the project folder.

Imported first by anything that reads settings (main, llm, admin, preflight), so keys and settings
can live in .env instead of the shell. Anything already set in the shell wins.
"""
import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _read_env_file(path: Path) -> None:
    """Settings from .env, for when python-dotenv isn't installed (say, imy was started without the
    .venv). Plain KEY=value lines; quotes are dropped; anything already set in the shell wins."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value


try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
except ImportError:
    _read_env_file(ENV_FILE)
