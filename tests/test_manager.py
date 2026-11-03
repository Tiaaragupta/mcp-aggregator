import pytest

pytestmark = pytest.mark.anyio


async def test_tools_are_namespaced(manager):
    names = {k for k, _ in manager.exposed_tools()}
    assert {"demo__add", "demo__echo", "notes__save_note", "notes__list_notes"} <= names


async def test_call_routes_to_right_server(manager):
    res = await manager.call("demo__add", {"a": 2, "b": 3})
    assert res.content[0].text == "5"


async def test_policy_blocks_delete(manager):
    assert "demo__delete_note" not in {k for k, _ in manager.exposed_tools()}
    with pytest.raises(KeyError):
        await manager.call("demo__delete_note", {"note_id": "1"})


async def test_unknown_tool_rejected(manager):
    with pytest.raises(KeyError):
        await manager.call("nope__nothing", {})
