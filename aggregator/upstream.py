import asyncio
import fnmatch
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

SEP = "__"  # separates server name from tool name, e.g. demo__add
CONNECT_TIMEOUT = 15


def log(msg):
    # stdout is reserved for MCP messages in gateway mode, so log to stderr
    print(msg, file=sys.stderr)


@dataclass
class Upstream:
    name: str
    config: dict
    status: str = "starting"
    error: str | None = None
    session: ClientSession | None = None
    tools: list = field(default_factory=list)
    runner: asyncio.Task | None = None
    stop: asyncio.Event | None = None


class UpstreamManager:
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.upstreams: dict[str, Upstream] = {}
        self.registry: dict[str, tuple[str, str, types.Tool]] = {}
        self.block: list[str] = []

    async def start(self):
        config = json.loads(self.config_path.read_text())
        self.block = config.get("policy", {}).get("block", [])
        for name, cfg in config["servers"].items():
            try:
                await self.add_server(name, cfg)
            except ValueError as e:
                log(f"could not connect '{name}': {e}")

    async def add_server(self, name: str, cfg: dict) -> Upstream:
        if SEP in name:
            raise ValueError(f"server name may not contain '{SEP}'")
        if name in self.upstreams:
            raise ValueError(f"'{name}' already exists")

        up = Upstream(name, cfg, stop=asyncio.Event())
        self.upstreams[name] = up
        ready = asyncio.Event()
        up.runner = asyncio.create_task(self._run(up, ready))
        try:
            await asyncio.wait_for(ready.wait(), CONNECT_TIMEOUT + 5)
        except asyncio.TimeoutError:
            up.error = "connect timed out"
            up.stop.set()

        if up.status != "connected":
            self.upstreams.pop(name, None)
            raise ValueError(up.error or "failed to connect")
        log(f"connected to '{name}': {len(up.tools)} tools")
        return up

    async def remove_server(self, name: str):
        up = self.upstreams.pop(name, None)
        if up is None:
            raise ValueError(f"unknown server: {name}")
        up.stop.set()
        try:
            await asyncio.wait_for(up.runner, 5)
        except Exception:
            up.runner.cancel()
        self._unindex(name)
        log(f"removed '{name}'")

    async def _run(self, up: Upstream, ready: asyncio.Event):
        """Owns the connection for one server. Entering and exiting happen in this one task."""
        try:
            cfg = up.config
            params = StdioServerParameters(command=cfg["command"], args=cfg.get("args", []))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), CONNECT_TIMEOUT)
                    up.tools = (await session.list_tools()).tools
                    up.session = session
                    self._index(up)
                    up.status = "connected"
                    ready.set()
                    await up.stop.wait()  # stay connected until remove_server sets stop
        except Exception as e:
            up.status = "error"
            up.error = f"{type(e).__name__}: {e}"
            ready.set()
        finally:
            up.session = None
            if up.status == "connected":
                up.status = "disconnected"
            self._unindex(up.name)

    def _index(self, up: Upstream):
        for tool in up.tools:
            key = f"{up.name}{SEP}{tool.name}"
            if any(fnmatch.fnmatch(key, p) for p in self.block):
                continue  # blocked by policy, so it never enters the registry
            self.registry[key] = (up.name, tool.name, tool)

    def _unindex(self, name: str):
        for key in [k for k in self.registry if k.startswith(name + SEP)]:
            del self.registry[key]

    async def call(self, key: str, arguments: dict):
        """Route a namespaced tool call to the server that owns it."""
        if key not in self.registry:
            raise ValueError(f"unknown tool: {key}")
        server, tool_name, _ = self.registry[key]
        up = self.upstreams.get(server)
        if up is None or up.session is None:
            raise ValueError(f"server '{server}' is not connected")
        return await up.session.call_tool(tool_name, arguments)

    def exposed_tools(self) -> list[types.Tool]:
        """Tools as the gateway advertises them to MCP clients, with namespaced names."""
        return [
            types.Tool(
                name=key,
                description=f"[{server}] {tool.description or ''}",
                inputSchema=tool.inputSchema,
            )
            for key, (server, _, tool) in self.registry.items()
        ]

    def status(self) -> list[dict]:
        return [
            {
                "name": u.name,
                "status": u.status,
                "error": u.error,
                "tools": len(u.tools),
                "config": {k: v for k, v in u.config.items() if k != "env"},
            }
            for u in self.upstreams.values()
        ]

    async def close(self):
        for name in list(self.upstreams):
            await self.remove_server(name)

    def list_tools(self):
        return list(self.registry.keys())