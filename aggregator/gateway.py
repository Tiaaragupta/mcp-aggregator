import asyncio

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from aggregator.upstream import UpstreamManager


async def main():
    manager = UpstreamManager("servers.json")
    await manager.start()

    server = Server("mcp-aggregator")

    @server.list_tools()
    async def list_tools():
        return manager.exposed_tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        result = await manager.call(name, arguments or {})
        if result.isError:
            raise RuntimeError(result.content[0].text if result.content else "tool error")
        return result.content

    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())