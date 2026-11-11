# Real AWS check

```
Connected to AWS account XXXXXXXXXXXX in us-east-1
[PASS] DynamoDB registry: 2 servers stored, schema v2: ['demo:2 tools', 'notes:3 tools']
[PASS] Secrets Manager injection: downstream server reported: token present (length 16) (secret value never printed)
[PASS] CloudWatch Logs: 40 events in /mcp-aggregator/live-check-6348a3, 20 X-Ray-format trace IDs; p50 613.3 ms, p95 618.5 ms
[FAIL] Bedrock routing: method=keyword -> notes__save_note (model anthropic.claude-3-haiku-20240307-v1:0)
Cleaning up temporary resources...

3/4 real-AWS checks passed.
```

