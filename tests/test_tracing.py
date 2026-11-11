import json
import re
import pytest

from aggregator import tracing

pytestmark = pytest.mark.anyio


async def test_two_hops_share_trace_id_and_latency(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(tracing, "LOG_FILE", tmp_path / "gateway.jsonl")
    await tracing.traced_call(manager, "demo__add", {"a": 1, "b": 2})
    events = [json.loads(l) for l in (tmp_path / "gateway.jsonl").read_text().splitlines()]
    assert {e["hop"] for e in events} == {"upstream", "gateway"}
    assert len({e["trace_id"] for e in events}) == 1
    assert all(e["latency_ms"] >= 0 for e in events)


async def test_arguments_never_logged(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(tracing, "LOG_FILE", tmp_path / "gateway.jsonl")
    await tracing.traced_call(manager, "notes__save_note", {"text": "TOP-SECRET-PAYLOAD"})
    assert "TOP-SECRET-PAYLOAD" not in (tmp_path / "gateway.jsonl").read_text()


def test_trace_id_is_xray_format():
    assert re.fullmatch(r"1-[0-9a-f]{8}-[0-9a-f]{24}", tracing.new_trace_id())


async def test_xray_segments_sent_to_daemon(manager, tmp_path, monkeypatch):
    """Segments arrive over UDP in X-Ray daemon format: gateway segment + upstream subsegment."""
    import json as _json
    import socket

    monkeypatch.setattr(tracing, "LOG_FILE", tmp_path / "gateway.jsonl")
    daemon = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    daemon.bind(("127.0.0.1", 0))
    daemon.settimeout(3)
    sink = tracing.XRaySink(f"127.0.0.1:{daemon.getsockname()[1]}")
    tracing.SINKS.append(sink)
    try:
        await tracing.traced_call(manager, "demo__add", {"a": 1, "b": 1})
    finally:
        tracing.SINKS.remove(sink)
    docs = []
    for _ in range(2):
        header, body = daemon.recv(65535).split(b"\n", 1)
        assert _json.loads(header) == {"format": "json", "version": 1}
        docs.append(_json.loads(body))
    sub = next(d for d in docs if d.get("type") == "subsegment")
    seg = next(d for d in docs if d.get("type") != "subsegment")
    assert sub["trace_id"] == seg["trace_id"] and sub["parent_id"] == seg["id"]
    assert re.fullmatch(r"1-[0-9a-f]{8}-[0-9a-f]{24}", seg["trace_id"])
    assert seg["end_time"] >= seg["start_time"]
