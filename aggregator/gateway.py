import asyncio

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import AnyUrl

from aggregator.tracing import traced_call
from aggregator.upstream import UpstreamManager


async def main():
    manager = UpstreamManager("servers.json")
    await manager.start()
    manager.start_health_loop(interval=30)

    server = Server("mcp-aggregator")

    @server.list_tools()
    async def list_tools():
        return manager.exposed_tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        result = await traced_call(manager, name, arguments or {}, source="mcp-client")
        if result.isError:
            raise RuntimeError(result.content[0].text if result.content else "tool error")
        return result.content

    @server.list_resources()
    async def list_resources():
        return manager.list_resources()

    @server.read_resource()
    async def read_resource(uri: AnyUrl):
        return await manager.read_resource(str(uri))

    @server.list_prompts()
    async def list_prompts():
        return manager.list_prompts()

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None):
        return await manager.get_prompt(name, arguments)

    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())