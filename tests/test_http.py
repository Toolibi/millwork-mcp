# Copyright (c) 2026 Prince Boafo. All rights reserved.
# Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
# Commercial use requires a separate license from the author - see COMMERCIAL.md.

"""Hosted (HTTP) mode: key auth, health check, real MCP calls over streamable HTTP."""
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from millwork_mcp.http_server import build_app, load_keys

KEY = "k" * 40
ROOT = Path(__file__).resolve().parent.parent
SAMPLE = json.loads((ROOT / "examples" / "sample_kitchen.json").read_text())
HDRS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@pytest.fixture()
def client():
    with TestClient(build_app([KEY])) as c:
        yield c


def rpc(client, method, params=None, key=KEY, rid=1, header="authorization"):
    h = dict(HDRS)
    if key is not None:
        h[header] = f"Bearer {key}" if header == "authorization" else key
    body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    return client.post("/mcp", headers=h, json=body)


def test_health_is_open_and_empty(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.text == "ok"


@pytest.mark.parametrize("key", [None, "", "wrong" * 10, KEY[:-1]])
def test_requests_without_the_right_key_are_rejected(client, key):
    r = rpc(client, "tools/list", key=key)
    assert r.status_code == 401
    assert "millwork" not in r.text.lower()


def test_oversized_request_is_rejected(client):
    h = {**HDRS, "authorization": f"Bearer {KEY}", "content-length": str(5_000_000)}
    r = client.post("/mcp", headers=h, content=b"{}")
    assert r.status_code == 413


def test_server_refuses_to_start_without_a_strong_key(monkeypatch):
    monkeypatch.delenv("MILLWORK_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        load_keys()
    monkeypatch.setenv("MILLWORK_API_KEY", "short")
    with pytest.raises(SystemExit):
        load_keys()
    monkeypatch.setenv("MILLWORK_API_KEY", f"{KEY},{'n' * 40}")
    assert len(load_keys()) == 2      # two keys at once lets a key be rotated with no downtime


@pytest.mark.parametrize("header", ["authorization", "x-api-key"])
def test_list_and_call_tools_with_the_key(client, header):
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "test", "version": "1"}}, header=header)
    assert r.status_code == 200, r.text
    assert "PolyForm" in r.json()["result"]["instructions"]
    r = rpc(client, "tools/list", rid=2, header=header)
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"review_cabinet_schedule", "estimate_and_export", "nest_sheets"} <= names
    r = rpc(client, "tools/call", {"name": "review_cabinet_schedule",
                                   "arguments": {"job": SAMPLE}}, rid=3, header=header)
    payload = json.loads(r.json()["result"]["content"][0]["text"])
    assert payload["cabinets"] == 9


def test_tool_schemas_are_foundry_safe(client):
    """Foundry rejects schemas that use anyOf/allOf or multi-type parameters."""
    tools = rpc(client, "tools/list").json()["result"]["tools"]
    blob = json.dumps([t["inputSchema"] for t in tools])
    assert "anyOf" not in blob and "allOf" not in blob


def test_job_number_cannot_escape_the_output_folder(client, tmp_path, monkeypatch):
    import millwork_mcp.server as srv
    monkeypatch.setattr(srv, "OUTPUT_ROOT", tmp_path / "out")
    evil = dict(SAMPLE, job_number="../../etc/evil")
    r = rpc(client, "tools/call", {"name": "estimate_and_export", "arguments": {"job": evil}})
    assert r.status_code == 200
    written = [p for p in (tmp_path / "out").iterdir()]
    assert len(written) == 1 and written[0].parent == tmp_path / "out"
    assert not (tmp_path / "etc").exists()
