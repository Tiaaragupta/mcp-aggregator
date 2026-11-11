"""Contract tests for the aggregator served as an MCP server over streamable HTTP (/mcp).

A real MCP client connects to a real uvicorn server, so this is what API Gateway forwards to.
"""
import socket
import threading
import time

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from aggregator.api import create_app
from aggregator.upstream import UpstreamManager

pytestmark = pytest.mark.anyio


@pytest.fixture
def base_url(make_config, monkeypatch):
    monkeypatch.setenv("NOTES_TOKEN", "http-token")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    app = create_app(UpstreamManager(make_config()), health_interval=None)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "server did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=10)


async def test_http_initialize_and_tools(base_url):
    async with streamablehttp_client(base_url + "/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            assert init.serverInfo.name == "mcp-aggregator"
            assert init.capabilities.tools is not None
            names = {t.name for t in (await s.list_tools()).tools}
            assert {"demo__add", "notes__save_note"} <= names and "demo__delete_note" not in names


async def test_http_tool_call_and_auth(base_url):
    async with streamablehttp_client(base_url + "/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("demo__add", {"a": 20, "b": 22})
            assert not res.isError and res.content[0].text == "42"
            auth = await s.call_tool("notes__auth_status", {})
            assert auth.content[0].text.startswith("token present")


async def test_http_resources_and_prompts(base_url):
    async with streamablehttp_client(base_url + "/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            assert any("notes" in str(x.uri) for x in (await s.list_resources()).resources)
            assert any(p.name == "notes__summarize_notes" for p in (await s.list_prompts()).prompts)
