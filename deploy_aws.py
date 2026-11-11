"""One-command real-AWS deployment + evidence for the MCP Aggregator.

Prereqs: `aws configure` done, Docker Desktop running.  Run:   python deploy_aws.py
It: finds your default VPC -> builds/pushes the image to ECR -> deploys deploy/template.yaml
(ECS Fargate, ALB, API Gateway, DynamoDB, Secrets Manager, CloudWatch/X-Ray, EventBridge+Lambda)
-> smoke-tests the live URL -> invokes the re-discovery Lambda -> writes results/aws_deploy.md.
Tear down afterwards (costs a few $/day):   python deploy_aws.py --destroy
"""
import json, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path
import boto3

ROOT = Path(__file__).resolve().parent
STACK, REPO = "mcp-aggregator", "mcp-aggregator"
session = boto3.session.Session()
REGION = session.region_name or "us-east-1"
ACCT = boto3.client("sts").get_caller_identity()["Account"]
cf, ecr, ec2 = (boto3.client(s, region_name=REGION) for s in ("cloudformation", "ecr", "ec2"))
lines = []


def say(m):
    lines.append(m); print(m, flush=True)


def run(cmd, **kw):
    say("$ " + (cmd if isinstance(cmd, str) else " ".join(cmd)))
    subprocess.run(cmd, check=True, shell=isinstance(cmd, str), **kw)


def destroy():
    cf.delete_stack(StackName=STACK)
    cf.get_waiter("stack_delete_complete").wait(StackName=STACK)
    say("Stack deleted. (ECR repo kept: delete with `aws ecr delete-repository --repository-name mcp-aggregator --force`)")


def outputs():
    st = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in st.get("Outputs", [])}


def get(url, path):
    t = time.perf_counter()
    with urllib.request.urlopen(url.rstrip("/") + path, timeout=30) as r:
        body = r.read().decode()
    return json.loads(body), (time.perf_counter() - t) * 1000


def post(url, path, payload):
    req = urllib.request.Request(url.rstrip("/") + path, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode()
    return json.loads(body), (time.perf_counter() - t) * 1000


def main():
    if "--destroy" in sys.argv:
        return destroy()
    say(f"AWS account {ACCT}, region {REGION}")
    vpc = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    subs = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["Subnets"]
    by_az = {}
    for s in subs:
        by_az.setdefault(s["AvailabilityZone"], s["SubnetId"])
    subnets = list(by_az.values())[:2]
    say(f"Default VPC {vpc}, subnets {subnets}")

    try:
        ecr.create_repository(repositoryName=REPO)
    except ecr.exceptions.RepositoryAlreadyExistsException:
        pass
    image = f"{ACCT}.dkr.ecr.{REGION}.amazonaws.com/{REPO}:latest"
    pw = subprocess.run(["aws", "ecr", "get-login-password", "--region", REGION], capture_output=True, text=True, check=True).stdout
    subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin", f"{ACCT}.dkr.ecr.{REGION}.amazonaws.com"], input=pw, text=True, check=True, capture_output=True)
    run(["docker", "build", "--platform", "linux/amd64", "-t", image, "."], cwd=ROOT)
    run(["docker", "push", image])

    body = (ROOT / "deploy" / "template.yaml").read_text()
    params = [{"ParameterKey": "ImageUri", "ParameterValue": image}, {"ParameterKey": "VpcId", "ParameterValue": vpc},
              {"ParameterKey": "SubnetIds", "ParameterValue": ",".join(subnets)}]
    exists = True
    try:
        cf.describe_stacks(StackName=STACK)
    except Exception:
        exists = False
    fn = cf.update_stack if exists else cf.create_stack
    try:
        fn(StackName=STACK, TemplateBody=body, Parameters=params, Capabilities=["CAPABILITY_IAM"])
        say("Deploying CloudFormation stack (about 5-10 minutes)...")
        cf.get_waiter("stack_update_complete" if exists else "stack_create_complete").wait(StackName=STACK, WaiterConfig={"Delay": 15, "MaxAttempts": 80})
    except Exception as e:
        if "No updates are to be performed" not in str(e):
            ev = cf.describe_stack_events(StackName=STACK)["StackEvents"]
            for x in ev[:10]:
                say(f"  {x['LogicalResourceId']}: {x['ResourceStatus']} {x.get('ResourceStatusReason','')}")
            raise
    out = outputs()
    url = out["ApiUrl"]
    say(f"Live URL: {url}")

    say("Waiting for the service to become healthy...")
    for _ in range(40):
        try:
            h, _ = get(url, "/healthz")
            break
        except Exception:
            time.sleep(15)
    else:
        raise SystemExit("service did not become healthy; check ECS logs in CloudWatch")

    res = {"url": url, "region": REGION, "account": ACCT}
    tools, _ = get(url, "/tools")
    res["tools_discovered"] = len(tools) if isinstance(tools, list) else len(tools.get("tools", tools))
    lat = []
    for i in range(20):
        r, ms = post(url, "/call", {"tool": "demo__add", "arguments": {"a": i, "b": 1}})
        lat.append(ms)
    lat.sort()
    res["latency_ms_via_api_gateway"] = {"n": 20, "p50": round(lat[10], 1), "p95": round(lat[18], 1)}
    res["call_sample"] = r
    res["health"], _ = post(url, "/health/check", {})
    res["rediscover"], _ = post(url, "/rediscover", {})
    try:
        res["route"], _ = post(url, "/route", {"query": "remember my idea"})
    except urllib.error.HTTPError as e:
        res["route"] = f"HTTP {e.code}"
    lam = boto3.client("lambda", region_name=REGION)
    fname = next(r["PhysicalResourceId"] for r in cf.describe_stack_resources(StackName=STACK)["StackResources"] if r["LogicalResourceId"] == "RediscoveryFunction")
    inv = lam.invoke(FunctionName=fname)
    res["eventbridge_lambda_invoke"] = {"status": inv["StatusCode"], "payload": inv["Payload"].read().decode()[:300]}
    say(json.dumps(res, indent=2))
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "aws_deploy.json").write_text(json.dumps(res, indent=2))
    (ROOT / "results" / "aws_deploy.md").write_text("# Real AWS deployment evidence\n\n```\n" + "\n".join(lines) + "\n```\n")
    say("Saved results/aws_deploy.md. Take screenshots of the console (ECS, DynamoDB, CloudWatch, X-Ray, EventBridge) too. "
        "Run `python deploy_aws.py --destroy` when done.")


if __name__ == "__main__":
    main()
