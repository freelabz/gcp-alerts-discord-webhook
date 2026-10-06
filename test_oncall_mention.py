"""On-call mention only for opened CRITICAL incidents. Run: python -m pytest test_oncall_mention.py"""
import json
import os

from fastapi.testclient import TestClient

import main


class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"id": "1"}


def _send(monkeypatch, severity, state="open"):
    sent = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json):
            sent.append(json)
            return _Resp()

        async def patch(self, url, json):
            sent.append(json)
            return _Resp()

    monkeypatch.setattr(main.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(main, "save_message_id", lambda *a: None)
    monkeypatch.setattr(main, "get_message_id", lambda *a: None)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/t")
    monkeypatch.setenv("DISCORD_ONCALL_ROLE_ID", "42")
    payload = json.load(open(os.path.join(os.path.dirname(__file__), "test_payload.json")))
    payload["incident"].update(severity=severity, state=state)
    r = TestClient(main.app).post("/api/discord_webhook", json=payload, auth=("secator", "secator"))
    assert r.status_code == 200
    return sent[0]


def test_critical_open_mentions_oncall(monkeypatch):
    msg = _send(monkeypatch, "Critical")
    assert msg["content"] == "<@&42>"
    assert msg["allowed_mentions"] == {"roles": ["42"]}


def test_warning_does_not_mention(monkeypatch):
    assert "content" not in _send(monkeypatch, "Warning")


def test_closed_critical_does_not_mention(monkeypatch):
    assert "content" not in _send(monkeypatch, "Critical", state="closed")
