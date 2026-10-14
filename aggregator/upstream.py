import asyncio
import fnmatch
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

SEP = "__"                # separates server name from tool/prompt name
RES_PREFIX = "agg://"     # resource URIs become agg://<server>/<original uri>
CONNECT_TIMEOUT = 15
HEALTH_TIMEOUT = 5


def log(msg):
    # stdout is reserved for MCP messages in gateway mode, so log to stderr
    print(msg, file=sys.stderr)


@dataclass
class Upstream:
    name: str
    config: dict
    status: str = "starting"
    health: str = "unknown"
    error: str | None = None
    session: ClientSession | None = None
    tools: list = field(default_factory=list)
    resources: list = field(default_factory=list)
    prompts: list = field(default_factory=list)
    runner: asyncio.Task | None = None
    stop: asyncio.Event | None = None


def _drop(d: dict, prefix: str):
    for key in [k for k in d if k.startswith(prefix)]:
        del d[key]


class UpstreamManager:
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.upstreams: dict[str, Upstream] = {}
        self.registry = {}       # tool key -> (server, tool name, tool)
        self.resource_defs = {}  # gateway uri -> types.Resource
        self.resource_map = {}   # gateway uri -> (server, original uri)
        self.prompt_defs = {}    # gateway prompt name -> types.Prompt
        self.prompt_map = {}     # gateway prompt name -> (server, original name)
        self.block: list[str] = []
        self._health_task: asyncio.Task | None = None

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
        up.health = "healthy"
        log(f"connected to '{name}': {len(up.tools)} tools, "
            f"{len(up.resources)} resources, {len(up.prompts)} prompts")
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
                    up.session = session
                    await self._refresh(up)
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

    @staticmethod
    async def _optional(fn, attr: str):
        # Resources and prompts are optional in MCP, so a server may not have them
        try:
            return getattr(await fn(), attr)
        except Exception:
            return []

    async def _refresh(self, up: Upstream):
        """Re-discover tools, resources and prompts from one server and rebuild its entries."""
        up.tools = (await up.session.list_tools()).tools
        up.resources = await self._optional(up.session.list_resources, "resources")
        up.prompts = await self._optional(up.session.list_prompts, "prompts")
        self._unindex(up.name)
        self._index(up)

    async def health_check(self) -> dict:
        """Ping every connected server. Re-discover on success, drop from the registry on failure."""
        results = {}
        for name, up in list(self.upstreams.items()):
            if up.session is None:
                results[name] = "not connected"
                continue
            try:
                await asyncio.wait_for(self._refresh(up), HEALTH_TIMEOUT)
                up.health = "healthy"
            except Exception as e:
                up.health = f"unhealthy: {type(e).__name__}"
                self._unindex(name)
                log(f"health check failed for '{name}': {up.health}")
            results[name] = up.health
        return results

    def start_health_loop(self, interval: float = 30):
        self._health_task = asyncio.create_task(self._health_loop(interval))

    async def _health_loop(self, interval: float):
        while True:
            await asyncio.sleep(interval)
            await self.health_check()

    def _blocked(self, key: str) -> bool:
        return any(fnmatch.fnmatch(key, p) for p in self.block)

    def _index(self, up: Upstream):
        for tool in up.tools:
            key = f"{up.name}{SEP}{tool.name}"
            if not self._blocked(key):
                self.registry[key] = (up.name, tool.name, tool)

        for r in up.resources:
            key = f"{RES_PREFIX}{up.name}/{r.uri}"
            if not self._blocked(key):
                self.resource_defs[key] = types.Resource(
                    uri=key, name=r.name, description=r.description, mimeType=r.mimeType
                )
                self.resource_map[key] = (up.name, str(r.uri))

        for p in up.prompts:
            key = f"{up.name}{SEP}{p.name}"
            if not self._blocked(key):
                self.prompt_defs[key] = types.Prompt(
                    name=key, description=p.description, arguments=p.arguments
                )
                self.prompt_map[key] = (up.name, p.name)

    def _unindex(self, name: str):
        _drop(self.registry, name + SEP)
        _drop(self.prompt_defs, name + SEP)
        _drop(self.prompt_map, name + SEP)
        _drop(self.resource_defs, f"{RES_PREFIX}{name}/")
        _drop(self.resource_map, f"{RES_PREFIX}{name}/")

    def _session_for(self, server: str) -> ClientSession:
        up = self.upstreams.get(server)
        if up is None or up.session is None:
            raise ValueError(f"server '{server}' is not connected")
        return up.session

    async def call(self, key: str, arguments: dict):
        """Route a namespaced tool call to the server that owns it."""
        if key not in self.registry:
            raise ValueError(f"unknown tool: {key}")
        server, tool_name, _ = self.registry[key]
        return await self._session_for(server).call_tool(tool_name, arguments)

    async def read_resource(self, uri: str) -> str:
        """Read a gateway resource and return its text."""
        if uri not in self.resource_map:
            raise ValueError(f"unknown resource: {uri}")
        server, original = self.resource_map[uri]
        result = await self._session_for(server).read_resource(original)
        return "\n".join(c.text for c in result.contents if hasattr(c, "text"))

    async def get_prompt(self, name: str, arguments: dict | None):
        """Route a namespaced prompt request to the server that owns it."""
        if name not in self.prompt_map:
            raise ValueError(f"unknown prompt: {name}")
        server, original = self.prompt_map[name]
        return await self._session_for(server).get_prompt(original, arguments or None)

    def list_resources(self) -> list[types.Resource]:
        return list(self.resource_defs.values())

    def list_prompts(self) -> list[types.Prompt]:
        return list(self.prompt_defs.values())

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
                "health": u.health,
                "error": u.error,
                "tools": len(u.tools),
                "resources": len(u.resources),
                "prompts": len(u.prompts),
                "config": {k: v for k, v in u.config.items() if k != "env"},
            }
            for u in self.upstreams.values()
        ]

    async def close(self):
        if self._health_task:
            self._health_task.cancel()
        for name in list(self.upstreams):
            await self.remove_server(name)

    def list_tools(self):
        return list(self.registry.keys())