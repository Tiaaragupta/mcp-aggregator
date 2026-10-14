import asyncio
import json
from pathlib import Path

from aggregator.upstream import UpstreamManager

ROOT = Path(__file__).resolve().parent.parent
DEMO = {"command": "python", "args": [str(ROOT / "demo_server" / "echo_server.py")]}
NOTES = {"command": "python", "args": [str(ROOT / "notes_server.py")]}


class DeadSession:
    """Stands in for a server that has crashed: every request fails."""

    async def list_tools(self):
        raise ConnectionError("server went away")


def _config(tmp_path):
    path = tmp_path / "servers.json"
    path.write_text(json.dumps({"servers": {"demo": DEMO, "notes": NOTES}, "policy": {"block": []}}))
    return str(path)


def test_dead_server_is_removed_from_registry(tmp_path):
    async def main():
        manager = UpstreamManager(_config(tmp_path))
        await manager.start()
        try:
            assert "notes__save_note" in manager.list_tools()

            manager.upstreams["notes"].session = DeadSession()
            results = await manager.health_check()

            assert results["notes"].startswith("unhealthy")
            assert results["demo"] == "healthy"
            assert not any(t.startswith("notes__") for t in manager.list_tools())
            assert "demo__add" in manager.list_tools()
        finally:
            await manager.close()

    asyncio.run(main())


def test_healthy_servers_keep_their_tools(tmp_path):
    async def main():
        manager = UpstreamManager(_config(tmp_path))
        await manager.start()
        try:
            results = await manager.health_check()
            assert results == {"demo": "healthy", "notes": "healthy"}

            status = {s["name"]: s["health"] for s in manager.status()}
            assert status == {"demo": "healthy", "notes": "healthy"}
            assert "notes__list_notes" in manager.list_tools()
        finally:
            await manager.close()

    asyncio.run(main())