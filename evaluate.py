"""Produces the numeric results for the report. Run:  python evaluate.py

Measures discovery time, call latency (p50/p95), routing accuracy, fan-out time,
failure-recovery time, and the pytest pass count. Writes results/results.json and results/results.md.
"""
import asyncio
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("NOTES_TOKEN", "eval-token")
from aggregator.router import Router  # noqa: E402
from aggregator.upstream import UpstreamManager  # noqa: E402

ROOT = Path(__file__).resolve().parent
N_CALLS = 100

ROUTING_SET = [
    ("save this note for later", "notes__save_note"),
    ("show me all my notes", "notes__list_notes"),
    ("add two numbers together", "demo__add"),
    ("echo back this text", "demo__echo"),
    ("remember a note", "notes__save_note"),
    ("list saved notes", "notes__list_notes"),
    ("sum of numbers", "demo__add"),
    ("repeat the text", "demo__echo"),
    ("check authentication status", "notes__auth_status"),
    ("is a token present", "notes__auth_status"),
]


def pct(values, p):
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


async def main():
    out = {}
    cfg = json.loads((ROOT / "servers.json").read_text())
    cfg["servers"]["demo2"] = {"command": "python", "args": ["demo_server/echo_server.py"]}
    tmp = Path(tempfile.mkdtemp()) / "servers.json"
    tmp.write_text(json.dumps(cfg))

    m = UpstreamManager(str(tmp))
    t = time.perf_counter()
    await m.start()
    out["discovery_ms"] = round((time.perf_counter() - t) * 1000)
    out["servers"] = len(m.upstreams)
    out["tools_exposed"] = len(m.registry)
    out["policy_patterns"] = cfg["policy"]["block"]
    out["resources"] = len(m.resources)
    out["prompts"] = len(m.prompts)

    # call latency through the aggregator
    lat = []
    for i in range(N_CALLS):
        s = time.perf_counter()
        r = await m.call("demo__add", {"a": i, "b": 1})
        assert r.content[0].text == str(i + 1)
        lat.append((time.perf_counter() - s) * 1000)
    out["call_latency_ms"] = {
        "n": N_CALLS, "mean": round(statistics.mean(lat), 2), "p50": round(pct(lat, 50), 2),
        "p95": round(pct(lat, 95), 2), "max": round(max(lat), 2),
    }

    # routing accuracy
    router = Router(m)
    hits = 0
    for q, want in ROUTING_SET:
        got = router.route(q, 1)["matches"]
        # overlapping servers expose the same tool; either copy counts as correct
        hits += bool(got) and got[0]["tool"].split("__", 1)[1] == want.split("__", 1)[1]
    out["routing"] = {"cases": len(ROUTING_SET), "correct": hits, "accuracy_pct": round(100 * hits / len(ROUTING_SET), 1)}

    # fan-out aggregation across overlapping servers
    s = time.perf_counter()
    fan = await m.call_all("echo", {"text": "hi"})
    out["fanout"] = {"sources": fan["sources"], "ms": round((time.perf_counter() - s) * 1000, 2)}

    # health check + recovery
    s = time.perf_counter()
    healthy = await m.health_check()
    out["health_check_ms"] = round((time.perf_counter() - s) * 1000, 2)
    out["all_healthy"] = all(healthy.values())

    up = m.upstreams["demo"]
    up.stop.set()
    await up.task
    s = time.perf_counter()
    res = await m.health_check()
    out["recovery"] = {"recovered": res["demo"], "ms": round((time.perf_counter() - s) * 1000)}
    await m.close()

    # test suite
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                       capture_output=True, text=True, cwd=ROOT)
    summary = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "no output"
    out["pytest"] = {"exit_code": p.returncode, "summary": summary}

    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "results.json").write_text(json.dumps(out, indent=2))
    c = out["call_latency_ms"]
    md = f"""# Evaluation results

| Metric | Value |
|---|---|
| Downstream servers connected | {out['servers']} |
| Tools exposed after policy | {out['tools_exposed']} |
| Resources / prompts aggregated | {out['resources']} / {out['prompts']} |
| Initial discovery time | {out['discovery_ms']} ms |
| Tool call latency (n={c['n']}) | mean {c['mean']} ms, p50 {c['p50']} ms, p95 {c['p95']} ms, max {c['max']} ms |
| Routing accuracy | {out['routing']['correct']}/{out['routing']['cases']} ({out['routing']['accuracy_pct']}%) |
| Fan-out across overlapping servers | {out['fanout']['sources']} sources in {out['fanout']['ms']} ms |
| Health check (all servers) | {out['health_check_ms']} ms, all healthy: {out['all_healthy']} |
| Failure recovery (killed server restarted) | recovered: {out['recovery']['recovered']} in {out['recovery']['ms']} ms |
| Test suite | {out['pytest']['summary']} |
"""
    (ROOT / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    asyncio.run(main())
