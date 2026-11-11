"""HTTP + WebSocket API (FastAPI). On AWS this sits behind API Gateway / an ALB."""
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.routing import Route
from pydantic import BaseModel

from . import tracing
from .gateway import build_server
from .router import Router
from .store import SCHEMA_VERSION, store_from_env
from .upstream import UpstreamManager

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


class CallBody(BaseModel):
    tool: str
    arguments: dict = {}


class ServerBody(BaseModel):
    name: str
    command: str
    args: list[str] = []
    secret: str | None = None


class RouteBody(BaseModel):
    query: str
    top_k: int = 3
    execute: bool = False
    arguments: dict = {}


class FanoutBody(BaseModel):
    tool: str
    arguments: dict = {}


def create_app(manager: UpstreamManager | None = None, bedrock_client=None, health_interval: float | None = 30.0) -> FastAPI:
    import os

    mgr = manager or UpstreamManager(store=store_from_env())
    model_id = os.environ.get("BEDROCK_MODEL_ID")
    if bedrock_client is None and model_id:
        import boto3

        bedrock_client = boto3.client("bedrock-runtime")
    router = Router(mgr, bedrock_client=bedrock_client, **({"model_id": model_id} if model_id else {}))

    # The same MCP server as gateway.py (stdio), served over streamable HTTP at /mcp.
    # stateless + JSON responses so it works behind API Gateway / ALB with several tasks.
    mcp_manager = StreamableHTTPSessionManager(app=build_server(mgr), json_response=True, stateless=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tracing.configure_from_env()
        if not mgr.upstreams:
            await mgr.start()
        if health_interval:
            mgr.start_health_loop(health_interval)
        async with mcp_manager.run():
            yield
        await mgr.close()

    app = FastAPI(title="MCP Aggregator", lifespan=lifespan)
    app.state.manager = mgr
    app.state.router = router

    class MCPEndpoint:
        async def __call__(self, scope, receive, send):
            await mcp_manager.handle_request(scope, receive, send)

    app.router.routes.append(Route("/mcp", endpoint=MCPEndpoint()))
    app.router.routes.append(Route("/mcp/", endpoint=MCPEndpoint()))

    @app.get("/trace/recent")
    async def trace_recent(since: int = 0):
        """Polling fallback for the live feed (HTTP API Gateway cannot carry WebSockets)."""
        events = [dict(e, seq=n) for n, e in list(tracing.RECENT) if n > since]
        return {"events": events, "last": events[-1]["seq"] if events else since}

    @app.get("/")
    async def index():
        if FRONTEND.exists():
            return FileResponse(FRONTEND)
        return {"service": "mcp-aggregator"}

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "schema_version": SCHEMA_VERSION, "servers": len(mgr.upstreams)}

    @app.get("/tools")
    async def tools():
        return [{"name": k, "description": t.description, "inputSchema": t.inputSchema} for k, t in mgr.exposed_tools()]

    @app.post("/call")
    async def call(body: CallBody):
        try:
            res = await tracing.traced_call(mgr, body.tool, body.arguments)
        except KeyError as e:
            raise HTTPException(404, str(e))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"{type(e).__name__}: {e}")
        return {
            "isError": res.isError,
            "content": [c.model_dump() for c in res.content],
        }

    @app.post("/fanout")
    async def fanout(body: FanoutBody):
        try:
            return await mgr.call_all(body.tool, body.arguments)
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.post("/route")
    async def route(body: RouteBody):
        decision = router.route(body.query, body.top_k)
        out = dict(decision)
        if body.execute and decision["matches"]:
            best = decision["matches"][0]["tool"]
            res = await tracing.traced_call(mgr, best, body.arguments, source="router")
            out["executed"] = best
            out["result"] = [c.model_dump() for c in res.content]
        return out

    @app.get("/servers")
    async def servers():
        return mgr.status()

    @app.post("/servers")
    async def add_server(body: ServerBody):
        cfg = {"command": body.command, "args": body.args}
        if body.secret:
            cfg["secret"] = body.secret
        try:
            await mgr.add_server(body.name, cfg)
        except ValueError as e:
            raise HTTPException(409, str(e))
        except RuntimeError as e:
            raise HTTPException(502, str(e))
        return {"added": body.name}

    @app.delete("/servers/{name}")
    async def delete_server(name: str):
        try:
            await mgr.remove_server(name)
        except KeyError:
            raise HTTPException(404, f"no such server: {name}")
        return {"removed": name}

    @app.post("/health/check")
    async def health_check():
        return await mgr.health_check()

    @app.post("/rediscover")
    async def rediscover():
        await mgr.rediscover()
        return {"tools": len(mgr.registry)}

    @app.get("/resources")
    async def resources():
        return [{"uri": k, "name": v[2].name} for k, v in mgr.resources.items()]

    @app.get("/prompts")
    async def prompts():
        return [{"name": k, "description": v[2].description} for k, v in mgr.prompts.items()]

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        """Live feed of trace events. Clients may also send {"tool":..., "arguments":...} to call a tool."""
        await socket.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        tracing.SUBSCRIBERS.add(q)

        async def pump():
            while True:
                await socket.send_text(json.dumps(await q.get(), default=str))

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                msg = json.loads(await socket.receive_text())
                try:
                    res = await tracing.traced_call(mgr, msg["tool"], msg.get("arguments", {}), source="ws")
                    reply = {"type": "result", "tool": msg["tool"], "isError": res.isError,
                             "content": [c.model_dump() for c in res.content]}
                except Exception as e:  # noqa: BLE001
                    reply = {"type": "error", "error": f"{type(e).__name__}: {e}"}
                await socket.send_text(json.dumps(reply, default=str))
        except WebSocketDisconnect:
            pass
        finally:
            pump_task.cancel()
            tracing.SUBSCRIBERS.discard(q)

    return app


app = create_app()
