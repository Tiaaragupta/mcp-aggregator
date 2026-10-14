from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from aggregator.upstream import UpstreamManager

manager = UpstreamManager("servers.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await manager.start()
    yield
    await manager.close()


app = FastAPI(title="MCP Aggregator", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class CallIn(BaseModel):
    tool: str
    arguments: dict = {}


@app.get("/")
def dashboard():
    return FileResponse("frontend/index.html")


@app.get("/tools")
def list_tools():
    return {"tools": manager.list_tools()}


@app.post("/call")
async def call_tool(body: CallIn):
    try:
        result = await manager.call(body.tool, body.arguments)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "ok": not result.isError,
        "content": [c.model_dump() for c in result.content],
    }