"""Lambda handler: API Gateway -> Claude on Amazon Bedrock, with usage logging."""
import json
import logging
import os
import time
import uuid
from decimal import Decimal

import boto3
from anthropic import AnthropicBedrockMantle, APIConnectionError, APIStatusError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MODEL_ID = os.environ.get("MODEL_ID", "anthropic.claude-opus-5-5")
AWS_REGION = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))
USAGE_TABLE = os.environ.get("USAGE_TABLE", "")
MAX_TOKENS_CAP = int(os.environ.get("MAX_TOKENS_CAP", "2048"))
MAX_PROMPT_CHARS = int(os.environ.get("MAX_PROMPT_CHARS", "8000"))

# $ per 1M tokens (input, output). Estimates only: check the Bedrock pricing page.
PRICES = {
    "anthropic.claude-opus-5-5": (4.00, 20.00),
    "anthropic.claude-sonnet-5-5": (2.00, 10.00),
    "anthropic.claude-haiku-5-5": (0.10, 0.50),
}

_client = None
_table = None


def get_client():
    global _client
    if _client is None:
        _client = AnthropicBedrockMantle(aws_region=AWS_REGION)
    return _client


def get_table():
    global _table
    if _table is None and USAGE_TABLE:
        _table = boto3.resource("dynamodb").Table(USAGE_TABLE)
    return _table


def estimate_cost(model, input_tokens, output_tokens):
    in_price, out_price = PRICES.get(model, (0.0, 0.0))
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000


def response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def log_usage(record):
    """One structured JSON line to CloudWatch, plus one row in DynamoDB."""
    logger.info(json.dumps({"event": "usage", **record}))
    table = get_table()
    if table is None:
        return
    try:
        item = {k: (Decimal(str(v)) if isinstance(v, float) else v) for k, v in record.items()}
        table.put_item(Item=item)
    except Exception:  # never fail the user's request because logging failed
        logger.exception("failed to write usage record")


def handler(event, context):
    started = time.time()
    request_id = str(uuid.uuid4())
    identity = (event.get("requestContext") or {}).get("identity") or {}
    api_key_id = identity.get("apiKeyId") or "unknown"

    record = {
        "api_key_id": api_key_id,
        "request_id": request_id,
        "timestamp": int(started),
        "model": MODEL_ID,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "status": "error",
    }

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        record["status"] = "bad_request"
        record["latency_ms"] = int((time.time() - started) * 1000)
        log_usage(record)
        return response(400, {"error": "body must be valid JSON"})

    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        record["status"] = "bad_request"
        record["latency_ms"] = int((time.time() - started) * 1000)
        log_usage(record)
        return response(400, {"error": "'prompt' (non-empty string) is required"})
    if len(prompt) > MAX_PROMPT_CHARS:
        record["status"] = "bad_request"
        record["latency_ms"] = int((time.time() - started) * 1000)
        log_usage(record)
        return response(400, {"error": f"prompt exceeds {MAX_PROMPT_CHARS} characters"})

    try:
        max_tokens = min(int(body.get("max_tokens", 1024)), MAX_TOKENS_CAP)
    except (TypeError, ValueError):
        max_tokens = 1024

    try:
        message = get_client().messages.create(
            model=MODEL_ID,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    except APIStatusError as e:
        record["status"] = f"upstream_{e.status_code}"
        record["latency_ms"] = int((time.time() - started) * 1000)
        log_usage(record)
        code = 429 if e.status_code == 429 else 502
        return response(code, {"error": "model call failed", "request_id": request_id})
    except APIConnectionError:
        record["status"] = "upstream_unreachable"
        record["latency_ms"] = int((time.time() - started) * 1000)
        log_usage(record)
        return response(502, {"error": "could not reach model", "request_id": request_id})

    text = "".join(b.text for b in message.content if b.type == "text")
    in_tok = message.usage.input_tokens
    out_tok = message.usage.output_tokens
    record.update(
        status="refusal" if message.stop_reason == "refusal" else "ok",
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=round(estimate_cost(MODEL_ID, in_tok, out_tok), 6),
        stop_reason=message.stop_reason,
        latency_ms=int((time.time() - started) * 1000),
    )
    log_usage(record)

    return response(200, {
        "request_id": request_id,
        "response": text,
        "stop_reason": message.stop_reason,
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
    })
