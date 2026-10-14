import asyncio
import json
from pathlib import Path

from aggregator import tracing
from aggregator.upstream import UpstreamManager

ROOT = Path(__file__).resolve().parent.parent
DEMO = {"command": "python", "args": [str(ROOT / "demo_server" / "echo_server.py")]}


def test_call_writes_two_hops_under_one_trace(tmp_path, monkeypatch):
    log_path = tmp_path / "gateway.jsonl"
    monkeypatch.setattr(tracing, "LOG_FILE", log_path)

    cfg = tmp_path / "servers.json"
    cfg.write_text(json.dumps({"servers": {"demo": DEMO}, "policy": {"block": []}}))

    async def main():
        manager = UpstreamManager(str(cfg))
        await manager.start()
        try:
            result = await tracing.traced_call(manager, "demo__add", {"a": 2, "b": 3}, source="test")
            assert result.content[0].text == "5"
        finally:
            await manager.close()

    asyncio.run(main())

    events = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert {e["hop"] for e in events} == {"gateway", "upstream"}
    assert len({e["trace_id"] for e in events}) == 1
    assert all(e["latency_ms"] >= 0 for e in events)
    assert all(e["server"] == "demo" for e in events)