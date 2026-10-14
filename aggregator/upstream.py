import fnmatch
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

SEP = "__"  # separates server name from tool name, e.g. demo__add


def log(msg):
    # stdout is reserved for MCP messages when running as a gateway, so log to stderr
    print(msg, file=sys.stderr)


class UpstreamManager:
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.stack = AsyncExitStack()
        self.sessions = {}   # server name -> ClientSession
        self.registry = {}   # namespaced name -> (server, tool name, tool object)
        self.block = []      # policy patterns, e.g. "*delete*"

    async def start(self):
        config = json.loads(self.config_path.read_text())
        self.block = config.get("policy", {}).get("block", [])
        for name, cfg in config["servers"].items():
            await self.connect(name, cfg)

    async def connect(self, name: str, cfg: dict):
        params = StdioServerParameters(command=cfg["command"], args=cfg.get("args", []))
        read, write = await self.stack.enter_async_context(stdio_client(params))
        session = await self.stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self.sessions[name] = session

        tools = (await session.list_tools()).tools
        allowed = 0
        for tool in tools:
            key = f"{name}{SEP}{tool.name}"
            if any(fnmatch.fnmatch(key, p) for p in self.block):
                continue  # blocked by policy, so it never enters the registry
            self.registry[key] = (name, tool.name, tool)
            allowed += 1
        log(f"connected to '{name}': {allowed} allowed of {len(tools)} tools")

    async def call(self, key: str, arguments: dict):
        """Route a namespaced tool call to the server that owns it."""
        if key not in self.registry:
            raise ValueError(f"unknown tool: {key}")
        server, tool_name, _ = self.registry[key]
        session = self.sessions[server]
        return await session.call_tool(tool_name, arguments)

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

    async def close(self):
        await self.stack.aclose()

    def list_tools(self):
        return list(self.registry.keys())