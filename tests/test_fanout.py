import pytest

from aggregator.upstream import UpstreamManager

pytestmark = pytest.mark.anyio


async def test_fanout_merges_overlapping_capabilities(make_config):
    """Two servers expose `echo`; one request returns both answers."""
    cfg = make_config({
        "alpha": {"command": "python", "args": ["demo_server/echo_server.py"]},
        "beta": {"command": "python", "args": ["demo_server/echo_server.py"]},
    })
    m = UpstreamManager(cfg)
    await m.start()
    try:
        out = await m.call_all("echo", {"text": "hi"})
        assert out["sources"] == 2
        assert {k: v["text"] for k, v in out["results"].items()} == {"alpha__echo": "hi", "beta__echo": "hi"}
    finally:
        await m.close()


async def test_fanout_unknown_tool(manager):
    with pytest.raises(KeyError):
        await manager.call_all("nonexistent", {})


async def test_fanout_reports_partial_failure(manager):
    out = await manager.call_all("add", {"a": "not-a-number", "b": 1})
    assert out["results"]["demo__add"]["ok"] is False
