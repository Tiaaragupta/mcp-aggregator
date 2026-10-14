import json
import sys
import time
import uuid
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "gateway.jsonl"


def log_event(event: dict):
    line = json.dumps(event, default=str)
    LOG_FILE.parent.mkdir(exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    # stderr, never stdout, because stdout carries MCP messages in gateway mode
    print(line, file=sys.stderr)


async def traced_call(manager, key: str, arguments: dict, source: str = "api"):
    """Run a namespaced tool call and log two hops under one trace ID."""
    trace_id = uuid.uuid4().hex[:16]
    server = key.split("__", 1)[0]
    start = time.perf_counter()
    ok, error = False, None

    upstream_start = time.perf_counter()
    try:
        result = await manager.call(key, arguments)
        ok = not result.isError
        return result
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        now = time.perf_counter()
        log_event({
            "ts": time.time(), "trace_id": trace_id, "hop": "upstream",
            "server": server, "tool": key, "ok": ok, "error": error,
            "latency_ms": round((now - upstream_start) * 1000, 2),
        })
        log_event({
            "ts": time.time(), "trace_id": trace_id, "hop": "gateway",
            "server": server, "tool": key, "source": source, "ok": ok, "error": error,
            "latency_ms": round((now - start) * 1000, 2),
        })