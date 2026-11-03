import pytest
from fastapi.testclient import TestClient

from aggregator.api import create_app
from aggregator.upstream import UpstreamManager


@pytest.fixture
def client(make_config, monkeypatch):
    monkeypatch.setenv("NOTES_TOKEN", "t")
    app = create_app(UpstreamManager(make_config()), health_interval=None)
    with TestClient(app) as c:
        yield c


def test_tools_and_call(client):
    names = {t["name"] for t in client.get("/tools").json()}
    assert "demo__add" in names and "demo__delete_note" not in names
    r = client.post("/call", json={"tool": "demo__add", "arguments": {"a": 4, "b": 5}})
    assert r.status_code == 200 and r.json()["content"][0]["text"] == "9"


def test_blocked_tool_is_404(client):
    assert client.post("/call", json={"tool": "demo__delete_note", "arguments": {"note_id": "1"}}).status_code == 404


def test_add_remove_server_via_api(client):
    r = client.post("/servers", json={"name": "extra", "command": "python", "args": ["demo_server/echo_server.py"]})
    assert r.status_code == 200
    assert client.post("/servers", json={"name": "extra", "command": "python", "args": []}).status_code == 409
    assert client.delete("/servers/extra").status_code == 200
    assert client.delete("/servers/extra").status_code == 404


def test_route_and_execute(client):
    r = client.post("/route", json={"query": "add numbers", "execute": True, "arguments": {"a": 1, "b": 2}}).json()
    assert r["executed"] == "demo__add" and r["result"][0]["text"] == "3"


def test_websocket_call_and_live_feed(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"tool": "demo__add", "arguments": {"a": 2, "b": 2}})
        seen = [ws.receive_json() for _ in range(3)]
    assert any(m.get("type") == "result" and m["content"][0]["text"] == "4" for m in seen)
    assert any(m.get("hop") == "gateway" for m in seen)


def test_servers_status_hides_secrets(client):
    assert "NOTES_TOKEN" not in client.get("/servers").text
    notes = next(s for s in client.get("/servers").json() if s["name"] == "notes")
    assert notes["has_secret"] is True and "token" not in notes


def test_healthz_and_manual_health_check(client):
    assert client.get("/healthz").json()["ok"] is True
    assert client.post("/health/check").json() == {"demo": True, "notes": True}
