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
