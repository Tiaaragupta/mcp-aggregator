import json
import pytest

from aggregator.upstream import UpstreamManager

pytestmark = pytest.mark.anyio


async def test_token_injected_into_downstream(manager):
    res = await manager.call("notes__auth_status", {})
    assert res.content[0].text == "token present (length 14)"   # len("test-token-123")


async def test_status_never_leaks_secret_value(manager):
    dump = json.dumps(manager.status())
    assert "test-token-123" not in dump
    notes = next(s for s in manager.status() if s["name"] == "notes")
    assert notes["has_secret"] is True


async def test_server_without_secret_gets_no_token(make_config, monkeypatch):
    monkeypatch.delenv("NOTES_TOKEN", raising=False)
    monkeypatch.setattr("aggregator.secrets.ENV_FILE", __import__("pathlib").Path("/nonexistent/.env"))
    m = UpstreamManager(make_config())
    await m.start()
    try:
        res = await m.call("notes__auth_status", {})
        assert res.content[0].text == "no token received"
    finally:
        await m.close()
