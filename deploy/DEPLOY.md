# Deploying to AWS (optional, costs money — set a billing alarm first)

1. Build and push the image
   ```
   aws ecr create-repository --repository-name mcp-aggregator
   aws ecr get-login-password | docker login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com
   docker build -t mcp-aggregator .
   docker tag mcp-aggregator <acct>.dkr.ecr.<region>.amazonaws.com/mcp-aggregator:latest
   docker push <acct>.dkr.ecr.<region>.amazonaws.com/mcp-aggregator:latest
   ```
2. Deploy
   ```
   aws cloudformation deploy --stack-name mcp-aggregator --template-file deploy/template.yaml \
     --capabilities CAPABILITY_IAM \
     --parameter-overrides ImageUri=<image uri> VpcId=<vpc> SubnetIds=<subnet-a>,<subnet-b>
   ```
3. Open the `ApiUrl` output; the dashboard and `/healthz` are served through API Gateway.
4. Tear down when finished: `aws cloudformation delete-stack --stack-name mcp-aggregator`
