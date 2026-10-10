# Claude API Gateway (AWS)

Routes Claude calls through **API Gateway -> Lambda -> Amazon Bedrock** with API-key auth,
rate limiting, and per-request usage logging.

```
client --x-api-key--> API Gateway (usage plan: throttle + daily quota)
                          -> Lambda (validate, call Claude, count tokens, estimate cost)
                                -> Bedrock (Claude)
                                -> CloudWatch Logs (JSON line) + DynamoDB (one row per request)
```

## Step 0 - Prerequisites
1. AWS account, a budget alert (Billing -> Budgets, e.g. $10), and an IAM/SSO identity (not root).
2. Bedrock console -> Model access: enable the Claude model you will use (default `anthropic.claude-opus-5-5`;
   `anthropic.claude-haiku-5-5` is much cheaper for testing). Use a region where it is available, e.g. `us-east-1`.
3. Install: AWS CLI v2, AWS SAM CLI, Python 3.12.
4. `aws configure` (or `aws sso login`), then check with `aws sts get-caller-identity`.

## Step 1 - Test locally
```bash
pip install -r src/requirements.txt pytest boto3
python -m pytest -q tests
```

## Step 2 - Deploy
```bash
sam build
sam deploy --guided --parameter-overrides ModelId=anthropic.claude-haiku-5-5
```
Accept the defaults. Note the `ChatUrl` output.

## Step 3 - Create an API key and attach it to the usage plan
```bash
aws apigateway get-usage-plans --query "items[].{id:id,name:name}"
aws apigateway create-api-key --name my-first-key --enabled   # note id and value
aws apigateway create-usage-plan-key --usage-plan-id <PLAN_ID> --key-id <KEY_ID> --key-type API_KEY
```

## Step 4 - Call it
```bash
curl -X POST "<ChatUrl>" -H "x-api-key: <KEY_VALUE>" -H "content-type: application/json" \
  -d '{"prompt":"Explain API gateways in one sentence."}'
```
Without a key you get 403. Over the throttle you get 429.

## Step 5 - See the usage data
```bash
aws dynamodb scan --table-name <UsageTable output>
aws logs tail /aws/lambda/<function name> --follow
```
Next: CloudWatch dashboard, a daily-cost query per key, Firehose -> S3 -> Athena.

## Cleanup
`sam delete` removes everything.

## Caveats
- The IAM action `bedrock-mantle:CreateInference` comes from Anthropic's Bedrock docs and is scoped to `*`
  in `template.yaml`; if the call is denied, read the error and adjust, then tighten to your model ARNs.
- `cost_usd` is an estimate from hard-coded prices in `src/app.py`; verify against the Bedrock pricing page.
- Not yet deployed or tested against a real AWS account.
