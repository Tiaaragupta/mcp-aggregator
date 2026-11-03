"""Contract tests: talk to the gateway like a real MCP client over stdio and
verify the protocol surface (initialize, tools, resources, prompts)."""
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
async def gateway_session(make_config):
    env = dict(os.environ, MCP_CONFIG=make_config(), NOTES_TOKEN="contract-token", PYTHONPATH=str(ROOT))
    params = StdioServerParameters(command=sys.executable, args=["-m", "aggregator.gateway"], env=env, cwd=str(ROOT))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            s.init_result = init
            yield s


async def test_initialize_handshake(gateway_session):
    init = gateway_session.init_result
    assert init.serverInfo.name == "mcp-aggregator"
    caps = init.capabilities
    assert caps.tools is not None and caps.resources is not None and caps.prompts is not None


async def test_tools_list_contract(gateway_session):
    tools = (await gateway_session.list_tools()).tools
    names = {t.name for t in tools}
    assert {"demo__add", "notes__save_note"} <= names and "demo__delete_note" not in names
    for t in tools:                       # every tool must carry a JSON-schema input
        assert t.inputSchema["type"] == "object"


async def test_tools_call_contract(gateway_session):
    res = await gateway_session.call_tool("demo__add", {"a": 20, "b": 22})
    assert not res.isError and res.content[0].text == "42"


async def test_blocked_tool_call_is_error(gateway_session):
    res = await gateway_session.call_tool("demo__delete_note", {"note_id": "1"})
    assert res.isError


async def test_resources_contract(gateway_session):
    await gateway_session.call_tool("notes__save_note", {"text": "via gateway"})
    uris = {str(r.uri) for r in (await gateway_session.list_resources()).resources}
    assert "agg://notes/notes://latest" in uris
    read = await gateway_session.read_resource("agg://notes/notes://latest")
    assert read.contents[0].text == "via gateway"


async def test_prompts_contract(gateway_session):
    names = {p.name for p in (await gateway_session.list_prompts()).prompts}
    assert "notes__summarize_notes" in names
    got = await gateway_session.get_prompt("notes__summarize_notes")
    assert got.messages and "Summarize" in got.messages[0].content.text


async def test_secret_reaches_downstream_through_gateway(gateway_session):
    res = await gateway_session.call_tool("notes__auth_status", {})
    assert res.content[0].text == "token present (length 14)"   # len("contract-token")
