# MCP Server Aggregator & Dynamic Router

A gateway that connects to many downstream MCP servers, discovers their tools / resources / prompts at runtime,
merges them behind one endpoint, and routes each request to the right server.

![dashboard](docs/dashboard.png)

## Architecture

```
 MCP client (Claude Desktop / IDE)          Browser dashboard
        │ stdio (gateway.py)                       │ HTTP + WebSocket
        └──────────────┬───────────────────────────┘
                       ▼
              ┌──────────────────┐   routing: keyword scoring (offline) or Amazon Bedrock
              │ UpstreamManager  │   policy: block patterns, namespacing  server__tool
              │  + Router        │   fan-out: call_all() merges overlapping servers
              └───┬────┬────┬────┘   health loop + re-discovery, per-server secrets
                  ▼    ▼    ▼
              demo  notes  …  (downstream MCP servers over stdio)

 Registry store : SQLite (local)  ⇄  DynamoDB (AWS)     schema_version=2, v1→v2 migration
 Secrets        : .env (local)    ⇄  AWS Secrets Manager
 Logs / traces  : JSONL + stderr  ⇄  CloudWatch Logs, X-Ray-format trace IDs, per-hop latency
```

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                               # Windows: copy .env.example .env
uvicorn aggregator.api:app --reload                # dashboard at http://localhost:8000
python -m pytest -v                                # run all tests
python evaluate.py                                 # numeric results -> results/results.md
python demo_aws_local.py                           # every AWS integration end-to-end against a LOCAL AWS emulator
python -m aggregator.gateway                       # run as a stdio MCP server for any MCP client
```

## Checklist coverage — what is verified, and how

| Checklist item | Implementation | Verified by |
|---|---|---|
| MCP fundamentals (tools / resources / prompts) | `upstream.py`, `gateway.py` aggregate all three | `test_manager`, `test_resources`, `test_contract` |
| Dynamic tool discovery at runtime | `add_server` / `remove_server` / `rediscover` | `test_dynamic`, dashboard "Add server" |
| Routing / tool selection | `router.py` keyword (IDF) + Bedrock with fallback | `test_aws` (router tests), `evaluate.py` accuracy |
| Aggregation of overlapping requests | `call_all()` fan-out, `/fanout` | `test_fanout` |
| Per-server auth | secret name per server, injected as `MCP_TOKEN`; never logged or returned | `test_auth` |
| Registry (SQLite → DynamoDB) | `store.py` both backends | `test_store` (DynamoDB via moto) |
| Versioned schema + back-compat test | `SCHEMA_VERSION`, `migrate()` | `test_store::test_backward_compat_v1_record_still_readable` |
| Secrets Manager | `secrets.py` aws backend | `test_aws` (moto) |
| CloudWatch + X-Ray | `CloudWatchSink`; `XRaySink` sends real segments/subsegments to the X-Ray daemon (UDP); daemon sidecar + IAM in template | `test_aws`, `test_tracing` (daemon-format test) |
| WebSocket | `/ws` live feed + tool calls | `test_api::test_websocket_call_and_live_feed` |
| Health-check automation | `health_check()` pings, restarts dead servers, and **removes unrecoverable ones from the registry**; `/health/check` | `test_health` (incl. removal test) |
| Contract tests | real MCP client against the gateway over stdio | `test_contract` |
| CI with mock servers | `.github/workflows/ci.yml`; demo + notes servers are the mocks | GitHub Actions |
| ECS Fargate / API Gateway / EventBridge / Lambda / Bedrock IAM | `deploy/template.yaml` (CloudFormation) | `cfn-lint`; deployed on real AWS via `deploy_aws.py` |

### AWS status (verified on a real AWS account, 11 Nov 2026)

**Deployed and tested on real AWS** with `python deploy_aws.py` (evidence: `results/aws_deploy.md`, `results/aws_deploy.json`) and `python aws_live_check.py` (evidence: `results/aws_live_check.md`):

* **ECS Fargate + ALB + API Gateway (HTTP)**: the gateway container ran from ECR behind API Gateway; the dashboard and API were reachable at the generated `execute-api` URL.
* **DynamoDB registry**: 2 servers and their tools stored with `schema_version=2`.
* **Secrets Manager**: the notes server received its token from a real secret (value never printed or logged).
* **CloudWatch Logs + X-Ray**: 40 events and 20 X-Ray-format trace IDs from 20 traced calls; the X-Ray daemon runs as a sidecar in the task.
* **EventBridge + Lambda**: the scheduled health-check/re-discovery Lambda was invoked against the live gateway and returned both servers healthy and 5 tools after re-discovery.
* Live gateway results: 5 tools discovered, health check all healthy, re-discovery 5 tools.
* Latency through API Gateway, measured from a laptop in India to us-east-1 (20 calls): p50 889 ms, p95 1022 ms. This is dominated by the internet round trip, not by the gateway; the gateway's own per-call latency measured locally is about 3.7 ms (`results/results.md`).

**Not working on real AWS: Bedrock.** The routing code calls Bedrock `converse`, and it is covered by tests with a fake client. On the real account every Bedrock model call (Claude Haiku 4.5, Amazon Nova) returned `ValidationException: Operation not allowed`, an account-level restriction on this newly activated account (an AWS Support case is open). Routing therefore ran in **keyword fallback mode** on AWS (`"method": "keyword"` in the evidence file). The default model id is `anthropic.claude-haiku-4-5-20251001-v1:0` (the older Claude 3 Haiku has reached end of life on Bedrock). The model's real routing accuracy has **not** been measured.

**MCP over HTTP and the live feed on AWS.** The gateway is also served as a real MCP server over streamable HTTP at `/mcp` (same server object as the stdio `gateway.py`; stateless JSON mode so it works behind API Gateway). `tests/test_mcp_http.py` connects a real MCP client to it. API Gateway HTTP APIs cannot carry WebSockets, so the dashboard falls back to polling `/trace/recent` when `/ws` cannot connect; the feed then shows "live (polling)". `deploy_aws.py` checks both against the live URL (`mcp_over_http`, `trace_feed_events` in `results/aws_deploy.json`).

* The MCP-protocol interface is available over stdio (`aggregator/gateway.py`) and HTTP (`/mcp`); contract tests cover both.
* Routing: the keyword router scores 10/10 on the 4-tool demo catalog, but only **57.5% top-1 / 77.5% top-3** on a harder blind benchmark (40 queries, 12 tools, many paraphrases; `tests/routing_benchmark.py`, written before running and never tuned). Keyword matching fails on paraphrases ("will it rain" -> weather), which is why the Bedrock router exists.
* To redeploy: see `deploy/DEPLOY.md`. ALB + Fargate cost a few dollars per day if left running; tear down with `python deploy_aws.py --destroy`.

## Results (from `python evaluate.py`)

See `results/results.md` (regenerated on every run).
