"""Runs the project's AWS integrations against your REAL AWS account and prints evidence.

Needs credentials (aws configure). Creates temporary resources, tests them, then deletes them.
Cost: a few cents at most.   Run:  python aws_live_check.py
Evidence is written to results/aws_live_check.json and results/aws_live_check.md
"""
import asyncio
import json
import os
import secrets as pysecrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ["SECRETS_BACKEND"] = "aws"
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import boto3  # noqa: E402

from aggregator import secrets, tracing  # noqa: E402
from aggregator.router import Router  # noqa: E402
from aggregator.store import SCHEMA_VERSION, DynamoStore  # noqa: E402
from aggregator.upstream import UpstreamManager  # noqa: E402

SUFFIX = pysecrets.token_hex(3)
TABLE = f"mcp-registry-check-{SUFFIX}"
SECRET = "NOTES_TOKEN"
SECRET_ID = f"mcp-aggregator/{SECRET}"
GROUP = f"/mcp-aggregator/live-check-{SUFFIX}"
MODEL = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-haiku-4-5-20251001-v1:0")

report, lines = {"region": os.environ["AWS_DEFAULT_REGION"], "checks": {}}, []


def say(msg):
    lines.append(msg)
    print(msg, flush=True)


def record(name, ok, detail):
    report["checks"][name] = {"ok": ok, "detail": detail}
    say(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


async def main():
    sts = boto3.client("sts")
    ident = sts.get_caller_identity()
    report["account"] = ident["Account"]
    say(f"Connected to AWS account {ident['Account']} in {report['region']}")

    ddb, sm, logs = boto3.client("dynamodb"), boto3.client("secretsmanager"), boto3.client("logs")
    created = {"table": False, "secret": False, "group": False}
    try:
        # DynamoDB registry
        ddb.create_table(TableName=TABLE, BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": "name", "KeyType": "HASH"}],
                         AttributeDefinitions=[{"AttributeName": "name", "AttributeType": "S"}])
        created["table"] = True
        ddb.get_waiter("table_exists").wait(TableName=TABLE)

        # Secrets Manager
        value = pysecrets.token_urlsafe(12)
        sm.create_secret(Name=SECRET_ID, SecretString=value)
        created["secret"] = True
        secrets.set_aws_client(sm)

        # CloudWatch Logs sink
        tracing.SINKS.append(tracing.CloudWatchSink(GROUP, "check"))
        created["group"] = True

        store = DynamoStore(TABLE)
        m = UpstreamManager(store=store)
        await m.start()

        rows = store.all()
        record("DynamoDB registry", len(rows) == 2 and all(r["schema_version"] == SCHEMA_VERSION for r in rows),
               f"{len(rows)} servers stored, schema v{SCHEMA_VERSION}: {[r['name'] + ':' + str(len(r['tools'])) + ' tools' for r in rows]}")

        got = (await m.call("notes__auth_status", {})).content[0].text
        record("Secrets Manager injection", got == f"token present (length {len(value)})",
               f"downstream server reported: {got} (secret value never printed)")

        lat = []
        for i in range(20):
            t = time.perf_counter()
            await tracing.traced_call(m, "demo__add", {"a": i, "b": 1})
            lat.append((time.perf_counter() - t) * 1000)
        lat.sort()
        report["call_latency_ms_with_cloudwatch"] = {"n": 20, "p50": round(lat[10], 1), "p95": round(lat[18], 1)}
        events = []
        for _ in range(10):  # CloudWatch is eventually consistent
            events = logs.get_log_events(logGroupName=GROUP, logStreamName="check")["events"]
            if len(events) >= 40:
                break
            time.sleep(2)
        traces = {json.loads(e["message"])["trace_id"] for e in events}
        record("CloudWatch Logs", len(events) >= 40, f"{len(events)} events in {GROUP}, {len(traces)} X-Ray-format trace IDs; "
               f"p50 {report['call_latency_ms_with_cloudwatch']['p50']} ms, p95 {report['call_latency_ms_with_cloudwatch']['p95']} ms")

        try:
            br = Router(m, bedrock_client=boto3.client("bedrock-runtime"), model_id=MODEL).route("remember my idea")
            record("Bedrock routing", br["method"] == "bedrock", f"method={br['method']} -> {br['matches'][0]['tool']} (model {MODEL})")
        except Exception as e:  # noqa: BLE001
            record("Bedrock routing", False, f"could not run: {type(e).__name__}: {str(e)[:160]}")

        await m.close()
    finally:
        say("Cleaning up temporary resources...")
        for fn, label in ((lambda: ddb.delete_table(TableName=TABLE), "table"),
                          (lambda: sm.delete_secret(SecretId=SECRET_ID, ForceDeleteWithoutRecovery=True), "secret"),
                          (lambda: logs.delete_log_group(logGroupName=GROUP), "log group")):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                say(f"  (could not delete {label}: {type(e).__name__}) delete it manually in the console")
        secrets.set_aws_client(None)

    ok = sum(c["ok"] for c in report["checks"].values())
    say(f"\n{ok}/{len(report['checks'])} real-AWS checks passed.")
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "aws_live_check.json").write_text(json.dumps(report, indent=2))
    (ROOT / "results" / "aws_live_check.md").write_text("# Real AWS check\n\n```\n" + "\n".join(lines) + "\n```\n")


if __name__ == "__main__":
    asyncio.run(main())
