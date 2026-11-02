import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path

from aggregator.upstream import UpstreamManager

ROOT = Path(__file__).resolve().parent
DEMO = {"command": "python", "args": [str(ROOT / "demo_server" / "echo_server.py")]}
NOTES = {"command": "python", "args": [str(ROOT / "notes_server.py")]}


class DeadSession:
    """Simulates a crashed server: every request fails."""

    async def list_tools(self):
        raise ConnectionError("server went away")


def ms(seconds):
    return round(seconds * 1000, 2)


async def main():
    tmp = Path(tempfile.mkdtemp())
    cfg = tmp / "servers.json"
    cfg.write_text(json.dumps({
        "servers": {"demo": DEMO, "notes": NOTES},
        "policy": {"block": ["*delete*"]},
    }))

    m = UpstreamManager(str(cfg))
    r = {}

    # 1. Startup and discovery
    t = time.perf_counter()
    await m.start()
    r["startup_seconds"] = round(time.perf_counter() - t, 2)
    r["servers_connected"] = len(m.upstreams)
    r["tools_exposed"] = len(m.list_tools())
    r["resources_exposed"] = len(m.list_resources())
    r["prompts_exposed"] = len(m.list_prompts())

    try:
        # 2. Routing correctness: 40 calls across two servers
        correct = total = 0
        for i in range(10):
            checks = [
                ("demo__add", {"a": i, "b": i + 1}, lambda x, i=i: x == str(2 * i + 1)),
                ("demo__echo", {"text": f"msg{i}"}, lambda x, i=i: x == f"msg{i}"),
                ("notes__save_note", {"text": f"n{i}"}, lambda x: x.startswith("saved note")),
                ("notes__list_notes", {}, lambda x: "n" in x),
            ]
            for tool, args, ok in checks:
                res = await m.call(tool, args)
                total += 1
                correct += 1 if ok(res.content[0].text) else 0
        r["routing_correct"] = f"{correct}/{total}"
        r["routing_accuracy_pct"] = round(100 * correct / total, 1)

        # 3. Latency and throughput: 100 calls through the gateway
        times = []
        start = time.perf_counter()
        for i in range(100):
            t = time.perf_counter()
            await m.call("demo__add", {"a": i, "b": 1})
            times.append(time.perf_counter() - t)
        elapsed = time.perf_counter() - start
        times.sort()
        r["latency_avg_ms"] = ms(statistics.mean(times))
        r["latency_p50_ms"] = ms(times[49])
        r["latency_p95_ms"] = ms(times[94])
        r["latency_max_ms"] = ms(times[-1])
        r["throughput_calls_per_sec"] = round(100 / elapsed, 1)

        # 4. Policy enforcement
        blocked_hidden = "demo__delete_note" not in m.list_tools()
        try:
            await m.call("demo__delete_note", {"note_id": "1"})
            blocked_rejected = False
        except ValueError:
            blocked_rejected = True
        r["policy_hides_blocked_tool"] = blocked_hidden
        r["policy_rejects_blocked_call"] = blocked_rejected

        # 5. Failure detection and recovery
        m.upstreams["notes"].session = DeadSession()
        t = time.perf_counter()
        health = await m.health_check()
        r["failure_detected"] = health["notes"].startswith("unhealthy")
        r["failure_detection_ms"] = ms(time.perf_counter() - t)
        r["dead_server_tools_removed"] = not any(k.startswith("notes__") for k in m.list_tools())
        r["healthy_server_unaffected"] = "demo__add" in m.list_tools()

        await m.remove_server("notes")
        t = time.perf_counter()
        await m.add_server("notes", NOTES)
        r["recovery_readd_seconds"] = round(time.perf_counter() - t, 2)
        r["tools_after_recovery"] = len(m.list_tools())

        # 6. Runtime add and remove
        t = time.perf_counter()
        await m.remove_server("demo")
        r["runtime_remove_ms"] = ms(time.perf_counter() - t)
        t = time.perf_counter()
        await m.add_server("demo", DEMO)
        r["runtime_add_seconds"] = round(time.perf_counter() - t, 2)
    finally:
        await m.close()

    # Print results
    print("\n" + "=" * 56)
    print(" MCP AGGREGATOR - EVALUATION RESULTS")
    print("=" * 56)
    for key, value in r.items():
        print(f" {key:<32} {value}")
    print("=" * 56)

    Path("results.json").write_text(json.dumps(r, indent=2))
    print(" saved to results.json\n")


if __name__ == "__main__":
    asyncio.run(main())