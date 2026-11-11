# Evaluation results

| Metric | Value |
|---|---|
| Downstream servers connected | 3 |
| Tools exposed after policy | 7 |
| Resources / prompts aggregated | 1 / 1 |
| Initial discovery time | 871 ms |
| Tool call latency (n=100) | mean 3.77 ms, p50 3.6 ms, p95 4.58 ms, max 6.67 ms |
| Routing accuracy | 10/10 (100.0%) |
| Routing benchmark (keyword, 40 blind queries, 12 tools) | top-1 57.5%, top-3 77.5% |
| Fan-out across overlapping servers | 2 sources in 11.57 ms |
| Health check (all servers) | 9.43 ms, all healthy: True |
| Failure recovery (killed server restarted) | recovered: True in 593 ms |
| Test suite | 52 passed in 43.09s |
