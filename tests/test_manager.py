import asyncio
import json
from pathlib import Path

import pytest

from aggregator.upstream import UpstreamManager

ROOT = Path(__file__).resolve().parent.parent

DEMO = {"command": "python", "args": [str(ROOT / "demo_server" / "echo_server.py")]}
NOTES = {"command": "python", "args": [str(ROOT / "notes_server.py")]}


def write_config(tmp_path, servers, block=None):
    """Write a temporary servers.json so tests never touch the real config."""
    path = tmp_path / "servers.json"
    path.write_text(json.dumps({"servers": servers, "policy": {"block": block or []}}))
    return str(path)


def run_with_manager(config_path, action):
    """Start a manager, run an async action against it, then always shut it down."""
    async def main():
        manager = UpstreamManager(config_path)
        await manager.start()
        try:
            return await action(manager)
        finally:
            await manager.close()

    return asyncio.run(main())


def test_tools_are_namespaced_per_server(tmp_path):
    config = write_config(tmp_path, {"demo": DEMO, "notes": NOTES})

    tools = run_with_manager(config, _list)

    assert "demo__add" in tools
    assert "notes__save_note" in tools
    assert "notes__list_notes" in tools


def test_policy_blocks_matching_tools(tmp_path):
    config = write_config(tmp_path, {"demo": DEMO}, block=["*delete*"])

    tools = run_with_manager(config, _list)

    assert "demo__delete_note" not in tools
    assert "demo__add" in tools


def test_call_routes_to_the_right_server(tmp_path):
    config = write_config(tmp_path, {"demo": DEMO, "notes": NOTES})

    async def action(manager):
        add = await manager.call("demo__add", {"a": 2, "b": 3})
        save = await manager.call("notes__save_note", {"text": "buy milk"})
        return add.content[0].text, save.content[0].text

    add_result, save_result = run_with_manager(config, action)

    assert add_result == "5"
    assert save_result == "saved note #1"


def test_unknown_tool_is_rejected(tmp_path):
    config = write_config(tmp_path, {"demo": DEMO})

    async def action(manager):
        with pytest.raises(ValueError):
            await manager.call("nope__thing", {})

    run_with_manager(config, action)


def test_blocked_tool_cannot_be_called(tmp_path):
    config = write_config(tmp_path, {"demo": DEMO}, block=["*delete*"])

    async def action(manager):
        with pytest.raises(ValueError):
            await manager.call("demo__delete_note", {"note_id": "1"})

    run_with_manager(config, action)


async def _list(manager):
    return manager.list_tools()