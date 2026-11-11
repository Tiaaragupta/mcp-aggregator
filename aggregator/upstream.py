"""Manages connections to downstream MCP servers.

Each upstream runs in ONE long-lived asyncio task, because anyio requires a
context to be entered and exited from the same task. An Event signals stop.
"""
import asyncio
import fnmatch
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .secrets import get_secret
from .store import make_record

ROOT = Path(__file__).resolve().parent.parent
SEP = "__"


@dataclass
class Upstream:
    name: str
    config: dict
    task: asyncio.Task | None = None
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    session: ClientSession | None = None
    healthy: bool = False
    last_check: float = 0.0
    last_error: str | None = None
    last_latency_ms: float | None = None


class UpstreamManager:
    def __init__(self, config_path: str | None = None, store=None):
        self.config_path = Path(config_path or os.environ.get("MCP_CONFIG") or ROOT / "servers.json")
        self.store = store
        self.upstreams: dict[str, Upstream] = {}
        # namespaced name -> (server, original name, Tool)
        self.registry: dict[str, tuple[str, str, object]] = {}
        self.resources: dict[str, tuple[str, str, object]] = {}   # agg uri -> (server, orig uri, Resource)
        self.prompts: dict[str, tuple[str, str, object]] = {}     # server__name -> (server, name, Prompt)
        self.policy_block: list[str] = []
        self._health_task: asyncio.Task | None = None

    # ---------- lifecycle ----------
    async def start(self):
        cfg = json.loads(self.config_path.read_text())
        self.policy_block = cfg.get("policy", {}).get("block", [])
        await asyncio.gather(*(self.add_server(n, c) for n, c in cfg.get("servers", {}).items()))

    async def close(self):
        if self._health_task:
            self._health_task.cancel()
        for name in list(self.upstreams):
            await self.remove_server(name)

    async def add_server(self, name: str, config: dict):
        if name in self.upstreams:
            raise ValueError(f"server '{name}' already exists")
        up = Upstream(name=name, config=config)
        self.upstreams[name] = up
        up.task = asyncio.create_task(self._run(up))
        try:
            await asyncio.wait_for(up.ready.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass
        if not up.healthy:
            err = up.last_error or "timeout starting server"
            await self.remove_server(name)
            raise RuntimeError(f"could not start '{name}': {err}")
        await self._refresh(up)
        self._persist(up)

    async def remove_server(self, name: str):
        up = self.upstreams.pop(name, None)
        if not up:
            raise KeyError(name)
        up.stop.set()
        if up.task:
            try:
                await asyncio.wait_for(up.task, timeout=10)
            except Exception:
                up.task.cancel()
        for table in (self.registry, self.resources, self.prompts):
            for key in [k for k, v in table.items() if v[0] == name]:
                del table[key]
        if self.store:
            self.store.delete(name)

    async def _run(self, up: Upstream):
        cfg = up.config
        env = dict(os.environ)
        env.update(cfg.get("env", {}))
        secret_name = cfg.get("secret")
        if secret_name:
            value = get_secret(secret_name)
            if value:
                env["MCP_TOKEN"] = value  # injected, never logged or returned
        cmd = cfg["command"]
        if cmd == "python":
            cmd = sys.executable
        params = StdioServerParameters(command=cmd, args=cfg.get("args", []), env=env, cwd=str(ROOT))
        try:
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    up.session = session
                    up.healthy = True
                    up.ready.set()
                    await up.stop.wait()
        except Exception as e:  # noqa: BLE001
            up.last_error = f"{type(e).__name__}: {e}"
        finally:
            up.healthy = False
            up.session = None
            up.ready.set()

    # ---------- discovery ----------
    def _blocked(self, key: str) -> bool:
        return any(fnmatch.fnmatch(key, p) for p in self.policy_block)

    async def _refresh(self, up: Upstream):
        """Re-discover tools, resources and prompts for one upstream."""
        for table in (self.registry, self.resources, self.prompts):
            for key in [k for k, v in table.items() if v[0] == up.name]:
                del table[key]
        s = up.session
        tools = (await s.list_tools()).tools
        for t in tools:
            key = f"{up.name}{SEP}{t.name}"
            if not self._blocked(key):
                self.registry[key] = (up.name, t.name, t)
        try:
            for r in (await s.list_resources()).resources:
                self.resources[f"agg://{up.name}/{r.uri}"] = (up.name, str(r.uri), r)
        except Exception:
            pass
        try:
            for p in (await s.list_prompts()).prompts:
                self.prompts[f"{up.name}{SEP}{p.name}"] = (up.name, p.name, p)
        except Exception:
            pass

    def _persist(self, up: Upstream):
        if self.store:
            tools = [k for k, v in self.registry.items() if v[0] == up.name]
            self.store.put(make_record(up.name, up.config, tools))

    async def rediscover(self):
        """Refresh every healthy upstream (what EventBridge triggers on AWS)."""
        for up in list(self.upstreams.values()):
            if up.healthy and up.session:
                await self._refresh(up)
                self._persist(up)

    # ---------- health ----------
    async def health_check(self) -> dict:
        """Ping every upstream; mark failures; try one restart of dead ones."""
        results = {}
        for name, up in list(self.upstreams.items()):
            start = time.perf_counter()
            try:
                if not up.session:
                    raise RuntimeError(up.last_error or "not connected")
                await asyncio.wait_for(up.session.send_ping(), timeout=5)
                up.healthy, up.last_error = True, None
            except Exception as e:  # noqa: BLE001
                up.healthy = False
                up.last_error = f"{type(e).__name__}: {e}" or "unreachable"
                await self._restart(up)
                if not up.healthy:
                    self._drop_capabilities(name)   # unrecoverable: stop advertising its tools
            up.last_latency_ms = round((time.perf_counter() - start) * 1000, 2)
            up.last_check = time.time()
            results[name] = up.healthy
        return results

    def _drop_capabilities(self, name: str):
        """Remove a dead server's tools/resources/prompts from the live registry and persist that."""
        for table in (self.registry, self.resources, self.prompts):
            for key in [k for k, v in table.items() if v[0] == name]:
                del table[key]
        if self.store and name in self.upstreams:
            self.store.put(make_record(name, self.upstreams[name].config, []))

    async def _restart(self, up: Upstream):
        """Replace a dead upstream's task with a fresh one."""
        up.stop.set()
        if up.task:
            try:
                await asyncio.wait_for(up.task, timeout=5)
            except Exception:
                up.task.cancel()
        up.stop, up.ready = asyncio.Event(), asyncio.Event()
        up.task = asyncio.create_task(self._run(up))
        try:
            await asyncio.wait_for(up.ready.wait(), timeout=15)
        except asyncio.TimeoutError:
            return
        if up.healthy:
            await self._refresh(up)
            self._persist(up)

    def start_health_loop(self, interval: float = 30.0):
        async def loop():
            while True:
                await asyncio.sleep(interval)
                try:
                    await self.health_check()
                except Exception:
                    pass

        self._health_task = asyncio.create_task(loop())

    def status(self) -> list[dict]:
        out = []
        for name, up in self.upstreams.items():
            out.append({
                "name": name,
                "healthy": up.healthy,
                "tools": sorted(k for k, v in self.registry.items() if v[0] == name),
                "resources": sorted(k for k, v in self.resources.items() if v[0] == name),
                "prompts": sorted(k for k, v in self.prompts.items() if v[0] == name),
                "has_secret": bool(up.config.get("secret")),   # never the value
                "last_check": up.last_check or None,
                "last_latency_ms": up.last_latency_ms,
                "last_error": up.last_error,
            })
        return out

    # ---------- calls ----------
    def exposed_tools(self):
        return [(k, v[2]) for k, v in self.registry.items()]

    async def call(self, key: str, arguments: dict):
        if key not in self.registry:
            raise KeyError(f"unknown or blocked tool: {key}")
        server, orig, _ = self.registry[key]
        up = self.upstreams[server]
        if not up.session:
            raise RuntimeError(f"server '{server}' is not connected")
        return await up.session.call_tool(orig, arguments)

    async def call_all(self, tool_name: str, arguments: dict) -> dict:
        """Fan-out: call every server that exposes `tool_name` in parallel and merge.

        This is how overlapping capabilities are aggregated into one response.
        """
        keys = [k for k, v in self.registry.items() if v[1] == tool_name]
        if not keys:
            raise KeyError(f"no server exposes '{tool_name}'")

        async def one(k):
            try:
                res = await self.call(k, arguments)
                text = " ".join(getattr(c, "text", "") for c in res.content)
                return k, {"ok": not res.isError, "text": text}
            except Exception as e:  # noqa: BLE001
                return k, {"ok": False, "text": f"{type(e).__name__}: {e}"}

        pairs = await asyncio.gather(*(one(k) for k in keys))
        return {"tool": tool_name, "sources": len(pairs), "results": dict(pairs)}

    async def read_resource(self, agg_uri: str):
        if agg_uri not in self.resources:
            raise KeyError(agg_uri)
        server, orig, _ = self.resources[agg_uri]
        return await self.upstreams[server].session.read_resource(orig)

    async def get_prompt(self, key: str, arguments: dict | None = None):
        if key not in self.prompts:
            raise KeyError(key)
        server, name, _ = self.prompts[key]
        return await self.upstreams[server].session.get_prompt(name, arguments or {})
