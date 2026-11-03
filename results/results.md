# Evaluation results

| Metric | Value |
|---|---|
| Downstream servers connected | 3 |
| Tools exposed after policy | 7 |
| Resources / prompts aggregated | 1 / 1 |
| Initial discovery time | 804 ms |
| Tool call latency (n=100) | mean 3.85 ms, p50 3.74 ms, p95 4.82 ms, max 5.87 ms |
| Routing accuracy | 10/10 (100.0%) |
| Fan-out across overlapping servers | 2 sources in 5.35 ms |
| Health check (all servers) | 7.87 ms, all healthy: True |
| Failure recovery (killed server restarted) | recovered: True in 573 ms |
| Test suite | 52 passed in 40.01s |
