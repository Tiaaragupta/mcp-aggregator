"""End-to-end demo of every AWS integration against a LOCAL AWS emulator (moto server).

Real boto3 calls over HTTP, but the 'AWS' is moto running on localhost. It proves the code
paths work; it does not prove a deployment on real AWS.   Run:  python demo_aws_local.py
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from moto.server import ThreadedMotoServer

ROOT = Path(__file__).resolve().parent
PORT = 5055
os.environ.update({
    "AWS_ENDPOINT_URL": f"http://127.0.0.1:{PORT}", "AWS_ACCESS_KEY_ID": "local", "AWS_SECRET_ACCESS_KEY": "local",
    "AWS_DEFAULT_REGION": "us-east-1", "SECRETS_BACKEND": "aws",
})
sys.path.insert(0, str(ROOT))

import boto3  # noqa: E402

from aggregator import secrets, tracing  # noqa: E402
from aggregator.router import Router  # noqa: E402
from aggregator.store import DynamoStore, SCHEMA_VERSION  # noqa: E402
from aggregator.upstream import UpstreamManager  # noqa: E402


class FakeBedrock:
    def converse(self, **kw):
        return {"output": {"message": {"content": [{"text": '["notes__save_note"]'}]}}}


async def main():
    out, lines = {}, []
    say = lambda s: (lines.append(s), print(s))  # noqa: E731

    ddb = boto3.client("dynamodb")
    ddb.create_table(TableName="registry", BillingMode="PAY_PER_REQUEST",
                     KeySchema=[{"AttributeName": "name", "KeyType": "HASH"}],
                     AttributeDefinitions=[{"AttributeName": "name", "AttributeType": "S"}])
    boto3.client("secretsmanager").create_secret(Name="mcp-aggregator/NOTES_TOKEN", SecretString="from-secrets-manager")
    logs = boto3.client("logs")
    tracing.SINKS.append(tracing.CloudWatchSink("/mcp-aggregator/gateway", "demo", client=logs))
    secrets.set_aws_client(None)

    store = DynamoStore("registry")
    m = UpstreamManager(store=store)
    t = time.perf_counter()
    await m.start()
    out["startup_s"] = round(time.perf_counter() - t, 2)

    items = store.all()
    out["dynamodb_registry"] = {r["name"]: {"tools": len(r["tools"]), "schema_version": r["schema_version"]} for r in items}
    say(f"[DynamoDB]       registry rows: {out['dynamodb_registry']}  (schema v{SCHEMA_VERSION})")

    tok = (await m.call("notes__auth_status", {})).content[0].text
    out["secrets_manager"] = tok
    say(f"[SecretsManager] downstream server reports: {tok}   (expected length 20)")

    lat = []
    for i in range(50):
        s = time.perf_counter()
        await tracing.traced_call(m, "demo__add", {"a": i, "b": 1})
        lat.append((time.perf_counter() - s) * 1000)
    lat.sort()
    out["calls_with_cloudwatch_ms"] = {"n": 50, "p50": round(lat[25], 2), "p95": round(lat[47], 2)}
    ev = logs.get_log_events(logGroupName="/mcp-aggregator/gateway", logStreamName="demo")["events"]
    parsed = [json.loads(e["message"]) for e in ev]
    traces = {p["trace_id"] for p in parsed}
    out["cloudwatch"] = {"events": len(ev), "distinct_trace_ids": len(traces), "xray_format": all(t.startswith("1-") for t in traces)}
    say(f"[CloudWatch]     {len(ev)} log events, {len(traces)} X-Ray-format trace IDs (2 hops per call)")
    say(f"[Latency]        50 calls incl. CloudWatch write: p50 {out['calls_with_cloudwatch_ms']['p50']} ms, p95 {out['calls_with_cloudwatch_ms']['p95']} ms")

    r = Router(m, bedrock_client=FakeBedrock()).route("remember my idea")
    out["bedrock_router"] = r
    say(f"[Bedrock router] method={r['method']} -> {r['matches'][0]['tool']}   (fake model client)")

    up = m.upstreams["demo"]
    up.stop.set()
    await up.task
    s = time.perf_counter()
    res = await m.health_check()
    out["eventbridge_style_health_recovery"] = {"recovered": res["demo"], "ms": round((time.perf_counter() - s) * 1000)}
    say(f"[Health/EventBridge job] killed 'demo' server -> recovered={res['demo']} in {out['eventbridge_style_health_recovery']['ms']} ms")

    await m.rediscover()
    say(f"[Re-discovery]   tools after re-discovery: {len(m.registry)}")
    await m.close()

    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "aws_local_demo.json").write_text(json.dumps(out, indent=2))
    say("\nAll AWS code paths exercised against a LOCAL emulator (moto). Not a real AWS deployment.")


if __name__ == "__main__":
    server = ThreadedMotoServer(port=PORT)
    server.start()
    try:
        asyncio.run(main())
    finally:
        server.stop()
