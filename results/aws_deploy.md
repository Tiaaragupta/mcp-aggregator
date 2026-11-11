# Real AWS deployment evidence

```
AWS account XXXXXXXXXXXX, region us-east-1
Default VPC vpc-096008a9b70b5a3da, subnets ['subnet-0694dadd3a248452b', 'subnet-02d4ffe8d6759a9ab']
$ docker build --platform linux/amd64 -t XXXXXXXXXXXX.dkr.ecr.us-east-1.amazonaws.com/mcp-aggregator:latest .
$ docker push XXXXXXXXXXXX.dkr.ecr.us-east-1.amazonaws.com/mcp-aggregator:latest
Deploying CloudFormation stack (about 5-10 minutes)...
Live URL: https://2ba2r5y7oa.execute-api.us-east-1.amazonaws.com
Waiting for the service to become healthy...
{
  "url": "https://2ba2r5y7oa.execute-api.us-east-1.amazonaws.com",
  "region": "us-east-1",
  "account": "XXXXXXXXXXXX",
  "tools_discovered": 5,
  "latency_ms_via_api_gateway": {
    "n": 20,
    "p50": 889.4,
    "p95": 1022.5
  },
  "call_sample": {
    "isError": false,
    "content": [
      {
        "type": "text",
        "text": "20",
        "annotations": null,
        "meta": null
      }
    ]
  },
  "health": {
    "demo": true,
    "notes": true
  },
  "rediscover": {
    "tools": 5
  },
  "route": {
    "method": "keyword",
    "matches": [
      {
        "tool": "notes__save_note",
        "score": 5.219
      }
    ]
  },
  "eventbridge_lambda_invoke": {
    "status": 200,
    "payload": "{\"health\": {\"demo\": true, \"notes\": true}, \"tools_after_rediscovery\": 5}"
  }
}
```

