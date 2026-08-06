"""GET /flows is the console's only view of the playbook, so what it withholds
matters as much as what it ships: patterns and success criteria are prompt text
for the planner, and putting them on the wire invites the dashboard to start
rendering policy it does not own.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from core import planner


@pytest.fixture
def client(tmp_path, monkeypatch):
    # The lifespan creates the schema; point it at a temp file rather than the
    # checked-in store.
    monkeypatch.setenv("NEXUSGTM_DB", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def test_flows_returns_one_entry_per_declared_flow(client):
    body = client.get("/flows").json()

    assert [f["id"] for f in body["flows"]] == [f["id"] for f in planner.flows()]


def test_every_flow_carries_what_a_button_needs(client):
    for flow in client.get("/flows").json()["flows"]:
        assert flow["label"] and flow["when"] and flow["goal"]
        trigger = flow["trigger"]
        if trigger is None:
            continue
        assert trigger["department"]
        assert trigger["label"]
        assert trigger["input"]["kind"] in ("lead", "brief", "account", "none")


def test_flows_withholds_the_planner_only_sections(client):
    for flow in client.get("/flows").json()["flows"]:
        assert "patterns" not in flow
        assert "success_criteria" not in flow


def test_launching_an_unknown_flow_is_a_422(client):
    response = client.post("/orchestrations", json={"flow": "nope", "lead": {"a": 1}})

    assert response.status_code == 422
    # The message names the flows that do exist, which is the whole point of
    # raising rather than silently falling back to the default.
    assert "unknown flow" in response.json()["detail"]


def test_launching_with_nothing_at_all_is_a_422(client):
    response = client.post("/orchestrations", json={})

    assert response.status_code == 422
