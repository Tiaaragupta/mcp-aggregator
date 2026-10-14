import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(command="python", args=["demo_server/echo_server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("Tools found:")
            for t in tools.tools:
                print("  -", t.name)

            result = await session.call_tool("add", {"a": 2, "b": 3})
            print("add(2, 3) =", result.content[0].text)


asyncio.run(main())