"""The aggregator itself, exposed as ONE MCP server over stdio.

Any MCP client (Claude Desktop, an IDE) connects here and sees every downstream
server's tools, resources and prompts merged. Logs go to stderr only.
"""
import asyncio

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import tracing
from .upstream import UpstreamManager


def build_server(manager: UpstreamManager) -> Server:
    server = Server("mcp-aggregator")

    @server.list_tools()
    async def list_tools():
        return [types.Tool(name=k, description=t.description, inputSchema=t.inputSchema)
                for k, t in manager.exposed_tools()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        res = await tracing.traced_call(manager, name, arguments or {}, source="gateway")
        if res.isError:
            raise RuntimeError(" ".join(getattr(c, "text", "") for c in res.content))
        return res.content

    @server.list_resources()
    async def list_resources():
        return [types.Resource(uri=k, name=v[2].name, description=v[2].description, mimeType=v[2].mimeType)
                for k, v in manager.resources.items()]

    @server.read_resource()
    async def read_resource(uri):
        res = await manager.read_resource(str(uri))
        from mcp.server.lowlevel.helper_types import ReadResourceContents
        return [ReadResourceContents(content=c.text, mime_type=c.mimeType)
                for c in res.contents if hasattr(c, "text")]

    @server.list_prompts()
    async def list_prompts():
        return [types.Prompt(name=k, description=v[2].description, arguments=v[2].arguments)
                for k, v in manager.prompts.items()]

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict | None):
        return await manager.get_prompt(name, arguments)

    return server


async def main():
    manager = UpstreamManager()
    await manager.start()
    try:
        async with stdio_server() as (r, w):
            server = build_server(manager)
            await server.run(r, w, server.create_initialization_options())
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
