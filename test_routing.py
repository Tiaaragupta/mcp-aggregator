import asyncio

from aggregator.upstream import UpstreamManager


async def main():
    manager = UpstreamManager("servers.json")
    await manager.start()

    # Route two calls to the same server through the aggregator
    result = await manager.call("demo__add", {"a": 2, "b": 3})
    print("demo__add(2, 3) ->", result.content[0].text)

    result = await manager.call("demo__echo", {"text": "hello aggregator"})
    print("demo__echo ->", result.content[0].text)

    # A name that doesn't exist should fail cleanly
    try:
        await manager.call("nope__thing", {})
    except ValueError as e:
        print("error handled:", e)

    await manager.close()


asyncio.run(main())