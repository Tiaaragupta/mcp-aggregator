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
