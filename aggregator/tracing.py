"""Structured JSON logs, trace IDs, per-hop latency, optional CloudWatch sink, live event feed."""
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "gateway.jsonl"

SINKS: list = []                       # extra sinks, e.g. CloudWatchSink
SUBSCRIBERS: set[asyncio.Queue] = set()  # live WebSocket listeners


def new_trace_id() -> str:
    """AWS X-Ray compatible trace id: 1-<8 hex time>-<24 hex random>."""
    return f"1-{int(time.time()):08x}-{uuid.uuid4().hex[:24]}"


class CloudWatchSink:
    """Sends each event to a CloudWatch Logs stream. Failures never break a tool call."""

    def __init__(self, group: str, stream: str, client=None):
        import boto3

        self.group, self.stream = group, stream
        self.client = client or boto3.client("logs")
        try:
            self.client.create_log_group(logGroupName=group)
        except Exception:
            pass
        try:
            self.client.create_log_stream(logGroupName=group, logStreamName=stream)
        except Exception:
            pass

    def emit(self, event: dict):
        self.client.put_log_events(
            logGroupName=self.group,
            logStreamName=self.stream,
            logEvents=[{"timestamp": int(event["ts"] * 1000), "message": json.dumps(event, default=str)}],
        )


def configure_from_env():
    group = os.environ.get("CLOUDWATCH_LOG_GROUP")
    if group:
        SINKS.append(CloudWatchSink(group, os.environ.get("CLOUDWATCH_LOG_STREAM", "gateway")))


def log_event(event: dict):
    line = json.dumps(event, default=str)
    try:
        LOG_FILE.parent.mkdir(exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    # stderr, never stdout, because stdout carries MCP messages in gateway mode
    print(line, file=sys.stderr)
    for sink in SINKS:
        try:
            sink.emit(event)
        except Exception:
            pass
    for q in list(SUBSCRIBERS):
        if not q.full():
            q.put_nowait(event)


async def traced_call(manager, key: str, arguments: dict, source: str = "api"):
    """Run a namespaced tool call and log two hops under one trace ID. Arguments are never logged."""
    trace_id = new_trace_id()
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
