import asyncio
import json
from pathlib import Path

import pytest

from aggregator import secrets
from aggregator.upstream import UpstreamManager

ROOT = Path(__file__).resolve().parent.parent
NOTES_SECRET = {
    "command": "python",
    "args": [str(ROOT / "notes_server.py")],
    "secret": "NOTES_TOKEN",
}


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    # Ignore any real .env on this machine and start with no NOTES_TOKEN
    monkeypatch.setattr(secrets, "ENV_FILE", tmp_path / "none.env")
    monkeypatch.delenv("NOTES_TOKEN", raising=False)


def _config(tmp_path):
    path = tmp_path / "servers.json"
    path.write_text(json.dumps({"servers": {"notes": NOTES_SECRET}, "policy": {"block": []}}))
    return str(path)


def test_missing_secret_blocks_server(tmp_path):
    async def main():
        manager = UpstreamManager(_config(tmp_path))
        with pytest.raises(ValueError, match="missing secret"):
            await manager.add_server("notes", NOTES_SECRET)
        assert "notes" not in manager.upstreams
        await manager.close()

    asyncio.run(main())


def test_secret_reaches_server_without_leaking(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTES_TOKEN", "abc123")

    async def main():
        manager = UpstreamManager(_config(tmp_path))
        await manager.start()
        try:
            result = await manager.call("notes__auth_status", {})
            text = result.content[0].text
            assert text == "token present (length 6)"
            assert "abc123" not in text
        finally:
            await manager.close()

    asyncio.run(main())