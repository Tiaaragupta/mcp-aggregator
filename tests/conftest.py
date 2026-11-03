import json
import pytest

from aggregator.upstream import UpstreamManager


@pytest.fixture(autouse=True)
def _fake_aws_env(monkeypatch):
    """Never touch real AWS from tests."""
    for k, v in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                 "AWS_DEFAULT_REGION": "us-east-1"}.items():
        monkeypatch.setenv(k, v)
    for k in ("SECRETS_BACKEND", "DYNAMODB_TABLE", "CLOUDWATCH_LOG_GROUP", "BEDROCK_MODEL_ID"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def make_config(tmp_path):
    """Write a temp servers.json and return its path."""
    def _make(servers=None, block=("*delete*",)):
        servers = servers or {
            "demo": {"command": "python", "args": ["demo_server/echo_server.py"]},
            "notes": {"command": "python", "args": ["notes_server.py"], "secret": "NOTES_TOKEN"},
        }
        p = tmp_path / "servers.json"
        p.write_text(json.dumps({"servers": servers, "policy": {"block": list(block)}}))
        return str(p)
    return _make


@pytest.fixture
async def manager(make_config, monkeypatch):
    monkeypatch.setenv("NOTES_TOKEN", "test-token-123")
    m = UpstreamManager(make_config())
    await m.start()
    yield m
    await m.close()
