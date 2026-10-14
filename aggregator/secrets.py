import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_env_file():
    """Read KEY=VALUE lines from .env into the environment. Existing variables win."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


def get_secret(name: str) -> str | None:
    """Return a secret by name, or None if it isn't set. Swap this for AWS Secrets Manager later."""
    _load_env_file()
    return os.environ.get(name)