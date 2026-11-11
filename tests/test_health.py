import pytest

pytestmark = pytest.mark.anyio


async def test_health_check_all_healthy(manager):
    result = await manager.health_check()
    assert result == {"demo": True, "notes": True}
    assert all(s["last_latency_ms"] is not None for s in manager.status())


async def test_dead_server_detected_and_restarted(manager):
    """Health-check automation: kill a server, the check notices and recovers it."""
    up = manager.upstreams["demo"]
    up.stop.set()                      # simulate the server dying
    await up.task
    assert up.healthy is False

    result = await manager.health_check()
    assert result["demo"] is True      # restarted
    res = await manager.call("demo__add", {"a": 1, "b": 1})
    assert res.content[0].text == "2"


async def test_unrecoverable_server_is_removed_from_registry(manager):
    """Handbook: CI validates the re-discovery job REMOVES dead/unresponsive servers."""
    up = manager.upstreams["demo"]
    up.config["command"] = "definitely-not-a-real-command"   # restart will fail too
    up.stop.set()
    await up.task

    result = await manager.health_check()
    assert result["demo"] is False and result["notes"] is True
    names = {k for k, _ in manager.exposed_tools()}
    assert not any(n.startswith("demo__") for n in names)      # dead server's tools gone
    assert "notes__save_note" in names                          # healthy server unaffected
    with pytest.raises(KeyError):
        await manager.call("demo__add", {"a": 1, "b": 1})
