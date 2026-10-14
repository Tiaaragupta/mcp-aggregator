import asyncio

from aggregator.upstream import UpstreamManager


async def main():
    manager = UpstreamManager("servers.json")
    await manager.start()

    print("\nAggregated tools:")
    for name in manager.list_tools():
        print("  -", name)

    await manager.close()


asyncio.run(main())