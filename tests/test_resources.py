import pytest

pytestmark = pytest.mark.anyio


async def test_resource_listed_with_agg_uri(manager):
    assert "agg://notes/notes://latest" in manager.resources


async def test_resource_read_reflects_state(manager):
    await manager.call("notes__save_note", {"text": "hello resource"})
    res = await manager.read_resource("agg://notes/notes://latest")
    assert res.contents[0].text == "hello resource"


async def test_prompt_listed_and_fetched(manager):
    assert "notes__summarize_notes" in manager.prompts
    await manager.call("notes__save_note", {"text": "alpha"})
    p = await manager.get_prompt("notes__summarize_notes")
    assert "alpha" in p.messages[0].content.text
