"""Secret lookup. Local .env by default, AWS Secrets Manager when SECRETS_BACKEND=aws."""
import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_aws_client = None


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


def set_aws_client(client):
    """Inject a Secrets Manager client (used by tests)."""
    global _aws_client
    _aws_client = client


def _aws_secret(name: str):
    global _aws_client
    if _aws_client is None:
        import boto3

        _aws_client = boto3.client("secretsmanager")
    try:
        prefix = os.environ.get("SECRETS_PREFIX", "mcp-aggregator/")
        return _aws_client.get_secret_value(SecretId=prefix + name)["SecretString"]
    except Exception:
        return None


def get_secret(name: str) -> str | None:
    """Return a secret by name, or None if it isn't set."""
    if os.environ.get("SECRETS_BACKEND") == "aws":
        return _aws_secret(name)
    _load_env_file()
    return os.environ.get(name)
