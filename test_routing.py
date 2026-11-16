"""Namespace-based channel routing. Run: python -m pytest test_routing.py"""
import json
import os

import pytest
from fastapi.testclient import TestClient

import main

ROUTES = {"*-staging": "staging", "kube-*": "infra"}
WEBHOOKS = {"staging": "https://discord.com/api/webhooks/2/staging"}
DEFAULT = "https://discord.com/api/webhooks/1/default"


class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"id": "1"}


@pytest.fixture
def sent(monkeypatch):
    calls = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json):
            calls.append((url, json))
            return _Resp()

        async def patch(self, url, json):
            calls.append((url, json))
            return _Resp()

    monkeypatch.setattr(main.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(main, "save_message_id", lambda *a: None)
    monkeypatch.setattr(main, "get_message_id", lambda *a: None)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", DEFAULT)
    monkeypatch.setenv("DISCORD_ROUTES", json.dumps(ROUTES))
    monkeypatch.setenv("DISCORD_WEBHOOKS", json.dumps(WEBHOOKS))
    monkeypatch.delenv("DISCORD_ONCALL_ROLE_ID", raising=False)
    return calls


def _post(resource_labels=None, metric_labels=None, severity="Warning"):
    payload = json.load(open(os.path.join(os.path.dirname(__file__), "test_payload.json")))
    inc = payload["incident"]
    inc["resource"]["labels"] = resource_labels or {}
    inc["metric"]["labels"] = metric_labels or {}
    inc["severity"] = severity
    return TestClient(main.app).post("/api/discord_webhook", json=payload, auth=("secator", "secator"))


def test_resource_namespace_routed(sent):
    assert _post({"namespace_name": "app-staging"}).status_code == 200
    assert sent[0][0].startswith(WEBHOOKS["staging"])


def test_metric_namespace_routed(sent):
    _post(metric_labels={"namespace": "app-staging"})
    assert sent[0][0].startswith(WEBHOOKS["staging"])


def test_unmatched_namespace_goes_to_default(sent):
    _post({"namespace_name": "app"})
    assert sent[0][0].startswith(DEFAULT)


def test_no_namespace_goes_to_default(sent):
    _post()
    assert sent[0][0].startswith(DEFAULT)


def test_unconfigured_channel_is_dropped_not_defaulted(sent):
    r = _post({"namespace_name": "kube-system"})
    assert r.status_code == 200 and r.json()["status"] == "dropped"
    assert sent == []


def test_first_matching_route_wins(sent, monkeypatch):
    monkeypatch.setenv("DISCORD_ROUTES", json.dumps({"app-*": "staging", "*-staging": "infra"}))
    _post({"namespace_name": "app-staging"})
    assert sent[0][0].startswith(WEBHOOKS["staging"])


def test_no_routes_configured_keeps_default(sent, monkeypatch):
    monkeypatch.delenv("DISCORD_ROUTES")
    monkeypatch.delenv("DISCORD_WEBHOOKS")
    _post({"namespace_name": "app-staging"})
    assert sent[0][0].startswith(DEFAULT)


def test_oncall_mention_only_in_default_channel(sent, monkeypatch):
    monkeypatch.setenv("DISCORD_ONCALL_ROLE_ID", "42")
    _post({"namespace_name": "app-staging"}, severity="Critical")
    assert "content" not in sent[0][1]


def test_namespace_field_in_embed(sent):
    _post({"namespace_name": "app-staging"})
    fields = {f["name"]: f["value"] for f in sent[0][1]["embeds"][0]["fields"]}
    assert fields["Namespace"] == "app-staging"


def test_closed_patch_404_posts_new_message(monkeypatch):
    calls = []

    class _R:
        def __init__(self, code):
            self.status_code = code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

        def json(self):
            return {"id": "9"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json):
            calls.append(("post", url))
            return _R(200)

        async def patch(self, url, json):
            calls.append(("patch", url))
            return _R(404)

    deleted = []
    monkeypatch.setattr(main.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(main, "get_message_id", lambda *a: "123")
    monkeypatch.setattr(main, "delete_message_id", lambda i: deleted.append(i))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", DEFAULT)
    monkeypatch.delenv("DISCORD_ROUTES", raising=False)
    payload = json.load(open(os.path.join(os.path.dirname(__file__), "test_payload.json")))
    payload["incident"]["state"] = "closed"
    r = TestClient(main.app).post("/api/discord_webhook", json=payload, auth=("secator", "secator"))
    assert r.status_code == 200
    assert [c[0] for c in calls] == ["patch", "post"]
    assert deleted == [payload["incident"]["incident_id"]]
