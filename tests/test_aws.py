"""AWS integrations exercised against moto (no real AWS calls)."""
import json

import boto3
import pytest
from moto import mock_aws

from aggregator import secrets, tracing
from aggregator.router import Router, keyword_rank

pytestmark = pytest.mark.anyio


@mock_aws
def test_secrets_manager_backend(monkeypatch):
    sm = boto3.client("secretsmanager", region_name="us-east-1")
    sm.create_secret(Name="mcp-aggregator/NOTES_TOKEN", SecretString="from-aws")
    monkeypatch.setenv("SECRETS_BACKEND", "aws")
    secrets.set_aws_client(sm)
    try:
        assert secrets.get_secret("NOTES_TOKEN") == "from-aws"
        assert secrets.get_secret("MISSING") is None
    finally:
        secrets.set_aws_client(None)


@mock_aws
def test_cloudwatch_sink_receives_events():
    logs = boto3.client("logs", region_name="us-east-1")
    sink = tracing.CloudWatchSink("/mcp/gateway", "test", client=logs)
    sink.emit({"ts": __import__("time").time(), "trace_id": "1-abc-def", "hop": "gateway", "latency_ms": 3.2})
    events = logs.get_log_events(logGroupName="/mcp/gateway", logStreamName="test")["events"]
    assert len(events) == 1 and json.loads(events[0]["message"])["trace_id"] == "1-abc-def"


async def test_traced_call_reaches_cloudwatch(manager):
    with mock_aws():
        logs = boto3.client("logs", region_name="us-east-1")
        sink = tracing.CloudWatchSink("/mcp/gateway", "calls", client=logs)
        tracing.SINKS.append(sink)
        try:
            await tracing.traced_call(manager, "demo__add", {"a": 1, "b": 1})
        finally:
            tracing.SINKS.remove(sink)
        events = logs.get_log_events(logGroupName="/mcp/gateway", logStreamName="calls")["events"]
        assert {json.loads(e["message"])["hop"] for e in events} == {"upstream", "gateway"}


class FakeBedrock:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def converse(self, **kw):
        self.calls += 1
        return {"output": {"message": {"content": [{"text": self.text}]}}}


async def test_keyword_router_picks_note_tool(manager):
    r = Router(manager).route("please save this note for me")
    assert r["method"] == "keyword" and r["matches"][0]["tool"] == "notes__save_note"


async def test_keyword_router_picks_math_tool(manager):
    r = Router(manager).route("add two numbers")
    assert r["matches"][0]["tool"] == "demo__add"


async def test_bedrock_router_used_when_available(manager):
    fake = FakeBedrock('Sure: ["demo__echo", "demo__add"]')
    r = Router(manager, bedrock_client=fake).route("repeat my text")
    assert r["method"] == "bedrock" and r["matches"][0]["tool"] == "demo__echo" and fake.calls == 1


async def test_bedrock_failure_falls_back_to_keywords(manager):
    r = Router(manager, bedrock_client=FakeBedrock("no json here")).route("save a note")
    assert r["method"] == "keyword" and r["matches"][0]["tool"] == "notes__save_note"


async def test_bedrock_hallucinated_tool_ignored(manager):
    r = Router(manager, bedrock_client=FakeBedrock('["made__up"]')).route("save a note")
    assert r["method"] == "keyword"


def test_router_accuracy_on_labelled_set():
    catalog = {"notes__save_note": "Save a note and return its number.",
               "notes__list_notes": "List all saved notes.",
               "demo__add": "Add two numbers.",
               "demo__echo": "Return the text unchanged."}
    cases = [("save a note", "notes__save_note"), ("show my notes", "notes__list_notes"),
             ("add numbers", "demo__add"), ("echo this text", "demo__echo")]
    hits = sum(keyword_rank(q, catalog, 1)[0]["tool"] == want for q, want in cases)
    assert hits == len(cases)
