import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import app  # noqa: E402


def event(body):
    return {"body": json.dumps(body), "requestContext": {"identity": {"apiKeyId": "key123"}}}


def fake_message(text="hi", stop="end_turn", i=10, o=20):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop,
        usage=SimpleNamespace(input_tokens=i, output_tokens=o),
    )


def test_ok(monkeypatch):
    client = MagicMock()
    client.messages.create.return_value = fake_message()
    monkeypatch.setattr(app, "get_client", lambda: client)
    monkeypatch.setattr(app, "get_table", lambda: None)
    r = app.handler(event({"prompt": "hello"}), None)
    assert r["statusCode"] == 200
    assert json.loads(r["body"])["response"] == "hi"


def test_max_tokens_capped(monkeypatch):
    client = MagicMock()
    client.messages.create.return_value = fake_message()
    monkeypatch.setattr(app, "get_client", lambda: client)
    monkeypatch.setattr(app, "get_table", lambda: None)
    app.handler(event({"prompt": "x", "max_tokens": 999999}), None)
    assert client.messages.create.call_args.kwargs["max_tokens"] == app.MAX_TOKENS_CAP


def test_missing_prompt(monkeypatch):
    monkeypatch.setattr(app, "get_table", lambda: None)
    assert app.handler(event({}), None)["statusCode"] == 400


def test_bad_json(monkeypatch):
    monkeypatch.setattr(app, "get_table", lambda: None)
    assert app.handler({"body": "{nope"}, None)["statusCode"] == 400


def test_usage_record_written(monkeypatch):
    client = MagicMock()
    client.messages.create.return_value = fake_message(i=1000, o=2000)
    table = MagicMock()
    monkeypatch.setattr(app, "get_client", lambda: client)
    monkeypatch.setattr(app, "get_table", lambda: table)
    app.handler(event({"prompt": "hello"}), None)
    item = table.put_item.call_args.kwargs["Item"]
    assert item["api_key_id"] == "key123"
    assert item["input_tokens"] == 1000 and item["output_tokens"] == 2000
    assert item["status"] == "ok"


def test_estimate_cost():
    assert app.estimate_cost("anthropic.claude-opus-5-5", 1_000_000, 1_000_000) == 24.0
