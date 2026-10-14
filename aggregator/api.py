from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from aggregator.tracing import traced_call
from aggregator.upstream import UpstreamManager

manager = UpstreamManager("servers.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await manager.start()
    manager.start_health_loop(interval=30)
    yield
    await manager.close()


app = FastAPI(title="MCP Aggregator", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class CallIn(BaseModel):
    tool: str
    arguments: dict = {}


class ServerIn(BaseModel):
    name: str
    command: str
    args: list[str] = []


@app.get("/")
def dashboard():
    return FileResponse("frontend/index.html")


@app.get("/tools")
def list_tools():
    return {"tools": manager.list_tools()}


@app.post("/call")
async def call_tool(body: CallIn):
    try:
        result = await traced_call(manager, body.tool, body.arguments, source="dashboard")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "ok": not result.isError,
        "content": [c.model_dump() for c in result.content],
    }


@app.get("/servers")
def list_servers():
    return {"servers": manager.status()}


@app.post("/servers")
async def add_server(body: ServerIn):
    try:
        await manager.add_server(body.name, {"command": body.command, "args": body.args})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "tools": [t for t in manager.list_tools() if t.startswith(body.name + "__")]}


@app.delete("/servers/{name}")
async def remove_server(name: str):
    try:
        await manager.remove_server(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


@app.post("/health/check")
async def run_health_check():
    return {"results": await manager.health_check()}
