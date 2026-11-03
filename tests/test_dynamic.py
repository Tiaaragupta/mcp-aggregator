import pytest

pytestmark = pytest.mark.anyio


async def test_add_and_remove_server_at_runtime(manager):
    await manager.add_server("demo2", {"command": "python", "args": ["demo_server/echo_server.py"]})
    assert "demo2__add" in {k for k, _ in manager.exposed_tools()}
    res = await manager.call("demo2__add", {"a": 10, "b": 5})
    assert res.content[0].text == "15"

    await manager.remove_server("demo2")
    assert "demo2__add" not in {k for k, _ in manager.exposed_tools()}


async def test_duplicate_server_rejected(manager):
    with pytest.raises(ValueError):
        await manager.add_server("demo", {"command": "python", "args": ["demo_server/echo_server.py"]})


async def test_bad_server_fails_cleanly(manager):
    with pytest.raises(RuntimeError):
        await manager.add_server("broken", {"command": "definitely-not-a-real-command"})
    assert "broken" not in manager.upstreams


async def test_rediscover_keeps_tools(manager):
    before = {k for k, _ in manager.exposed_tools()}
    await manager.rediscover()
    assert {k for k, _ in manager.exposed_tools()} == before
