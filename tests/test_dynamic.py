import asyncio
import json
from pathlib import Path

import pytest

from aggregator.upstream import UpstreamManager

ROOT = Path(__file__).resolve().parent.parent

DEMO = {"command": "python", "args": [str(ROOT / "demo_server" / "echo_server.py")]}
NOTES = {"command": "python", "args": [str(ROOT / "notes_server.py")]}


def write_config(tmp_path, servers):
    path = tmp_path / "servers.json"
    path.write_text(json.dumps({"servers": servers, "policy": {"block": []}}))
    return str(path)


def test_remove_then_readd_server(tmp_path):
    config = write_config(tmp_path, {"demo": DEMO, "notes": NOTES})

    async def main():
        manager = UpstreamManager(config)
        await manager.start()
        try:
            assert "notes__save_note" in manager.list_tools()

            await manager.remove_server("notes")
            assert not any(t.startswith("notes__") for t in manager.list_tools())

            await manager.add_server("notes", NOTES)
            assert "notes__save_note" in manager.list_tools()

            result = await manager.call("notes__save_note", {"text": "hi"})
            assert result.content[0].text.startswith("saved note")
        finally:
            await manager.close()

    asyncio.run(main())


def test_bad_server_is_rejected(tmp_path):
    config = write_config(tmp_path, {"demo": DEMO})

    async def main():
        manager = UpstreamManager(config)
        await manager.start()
        try:
            with pytest.raises(ValueError):
                await manager.add_server(
                    "broken", {"command": "definitely-not-a-real-command", "args": []}
                )
            assert "broken" not in manager.upstreams
            assert not any(t.startswith("broken__") for t in manager.list_tools())
        finally:
            await manager.close()

    asyncio.run(main())