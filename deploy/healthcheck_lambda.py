"""EventBridge-triggered Lambda: asks the running gateway to health-check and re-discover.

Env: GATEWAY_URL (e.g. https://abc.execute-api.ap-south-1.amazonaws.com)
"""
import json
import os
import urllib.request


def _post(path: str) -> dict:
    req = urllib.request.Request(os.environ["GATEWAY_URL"].rstrip("/") + path, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def handler(event, context):
    health = _post("/health/check")
    discovery = _post("/rediscover")
    result = {"health": health, "tools_after_rediscovery": discovery.get("tools")}
    print(json.dumps(result))
    return result
