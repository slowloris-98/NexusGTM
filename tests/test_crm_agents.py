"""HubSpot is the system of record, which is exactly why these tests are strict.

The load-bearing behaviour is `crm_sync`'s refusal to write inferred firmographics
under canonical property names. A model's guess at headcount landing in
`numberofemployees` would be read as measured by every report downstream --
silently, and for as long as the record exists.
"""

from __future__ import annotations

import pytest

from departments.revops import crm
from departments.revops.crm import CrmLookupAgent, CrmSyncAgent

CLAY_FIRMOGRAPHICS = {
    "company_name": "Northwind Logistics",
    "domain": "northwind.com",
    "industry": "Logistics Software",
    "employee_count": 420,
    "revenue_band": "$25M-$50M",
    "hq_region": "North America",
    "source": "clay",
}

INFERRED_FIRMOGRAPHICS = {**CLAY_FIRMOGRAPHICS, "source": "inferred"}

ROUTED = {
    "fit_score": 88,
    "segment": "mid_market",
    "assigned_rep": "Dana Whitlock",
    "queue": "ae_direct",
    "reasons": ["in-region", "clear buying signal"],
}


@pytest.fixture(autouse=True)
def hubspot_env(monkeypatch):
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "test-token")
    monkeypatch.delenv("CRM_WRITE_ENABLED", raising=False)


@pytest.fixture
def hubspot(monkeypatch):
    """Record what would be sent to HubSpot, and script what comes back."""
    sent: list[tuple[str, tuple, dict]] = []

    def install(search_result=None, *, record_id="99"):
        def fake_search(self, object_type, prop, value, properties):
            sent.append(("search", (object_type, prop, value), {}))
            return search_result

        def fake_create(self, object_type, properties):
            sent.append(("create", (object_type,), properties))
            return {"id": record_id}

        def fake_update(self, object_type, rid, properties):
            sent.append(("update", (object_type, rid), properties))
            return {"id": rid}

        monkeypatch.setattr(crm.HubspotClient, "search", fake_search)
        monkeypatch.setattr(crm.HubspotClient, "create", fake_create)
        monkeypatch.setattr(crm.HubspotClient, "update", fake_update)
        return sent

    return install


# ------------------------------------------------------------------- crm_sync


def test_inferred_firmographics_never_reach_canonical_properties(
    hubspot, spender, monkeypatch
):
    """The guard this whole module exists for."""
    monkeypatch.setenv("CRM_WRITE_ENABLED", "true")
    sent = hubspot(search_result=None)

    CrmSyncAgent().execute(
        {"firmographics": INFERRED_FIRMOGRAPHICS, **ROUTED}, spender
    )

    written = next(props for action, _, props in sent if action == "create")
    assert "numberofemployees" not in written
    assert "annualrevenue" not in written
    assert written["numberofemployees_inferred"] == 420
    # The provenance itself is recorded, so a human reading the record can tell.
    assert written["nexusgtm_firmographics_source"] == "inferred"


def test_retrieved_firmographics_are_written_as_fact(hubspot, spender, monkeypatch):
    monkeypatch.setenv("CRM_WRITE_ENABLED", "true")
    sent = hubspot(search_result=None)

    CrmSyncAgent().execute({"firmographics": CLAY_FIRMOGRAPHICS, **ROUTED}, spender)

    written = next(props for action, _, props in sent if action == "create")
    assert written["numberofemployees"] == 420
    assert "numberofemployees_inferred" not in written
    assert written["nexusgtm_firmographics_source"] == "clay"


def test_the_verdict_is_written_whatever_the_provenance(hubspot, spender, monkeypatch):
    """The score is this system's own output, not a vendor's data."""
    monkeypatch.setenv("CRM_WRITE_ENABLED", "true")
    sent = hubspot(search_result=None)

    CrmSyncAgent().execute(
        {"firmographics": INFERRED_FIRMOGRAPHICS, **ROUTED}, spender
    )

    written = next(props for action, _, props in sent if action == "create")
    assert written["nexusgtm_fit_score"] == 88
    assert written["nexusgtm_assigned_rep"] == "Dana Whitlock"


def test_writes_are_off_by_default_and_show_the_payload(hubspot, spender):
    """A dry run that hides what it would have done teaches nothing."""
    sent = hubspot(search_result=None)

    output = CrmSyncAgent().execute(
        {"firmographics": CLAY_FIRMOGRAPHICS, **ROUTED}, spender
    )

    assert output["crm_sync"]["dry_run"] is True
    assert output["crm_sync"]["action"] == "skipped"
    assert output["crm_sync"]["written_properties"]["numberofemployees"] == 420
    assert sent == []  # the portal was never touched


def test_an_existing_record_is_updated_rather_than_duplicated(
    hubspot, spender, monkeypatch
):
    monkeypatch.setenv("CRM_WRITE_ENABLED", "true")
    sent = hubspot(search_result={"id": "555", "properties": {}})

    output = CrmSyncAgent().execute(
        {"firmographics": CLAY_FIRMOGRAPHICS, **ROUTED}, spender
    )

    assert output["crm_sync"]["action"] == "updated"
    assert output["crm_sync"]["record_id"] == "555"
    assert not any(action == "create" for action, _, _ in sent)


def test_a_prior_lookup_saves_the_search(hubspot, spender, monkeypatch):
    monkeypatch.setenv("CRM_WRITE_ENABLED", "true")
    sent = hubspot(search_result=None)

    CrmSyncAgent().execute(
        {
            "firmographics": CLAY_FIRMOGRAPHICS,
            "crm_account": {"record_id": "777"},
            **ROUTED,
        },
        spender,
    )

    assert not any(action == "search" for action, _, _ in sent)
    assert ("update", ("companies", "777")) in [(a, args) for a, args, _ in sent]


def test_sync_requires_enrichment_first(spender):
    with pytest.raises(ValueError, match="firmographics"):
        CrmSyncAgent().execute({}, spender)


def test_sync_requires_a_domain_to_key_on(spender):
    with pytest.raises(ValueError, match="domain"):
        CrmSyncAgent().execute({"firmographics": {"company_name": "X"}}, spender)


# ----------------------------------------------------------------- crm_lookup


def test_lookup_prefers_email_over_the_internal_reference(hubspot, spender):
    """An internal ref like ACME-1001 usually exists only in this system.

    Keying on it first would make the agent miss on accounts HubSpot does have.
    """
    sent = hubspot(search_result={"id": "42", "properties": {"lifecyclestage": "lead"}})

    output = CrmLookupAgent().execute(
        {"email": "priya@northwind.com", "crm_reference_id": "ACME-1001"}, spender
    )

    assert sent[0][1] == ("contacts", "email", "priya@northwind.com")
    assert output["crm_account"]["exists"] is True
    assert output["crm_account"]["matched_on"] == "contacts.email"


def test_an_unknown_account_is_a_normal_answer(hubspot, spender):
    hubspot(search_result=None)

    output = CrmLookupAgent().execute({"domain": "northwind.com"}, spender)

    assert output["crm_account"]["exists"] is False
    assert output["crm_account"]["record_id"] == ""


def test_lookup_needs_something_to_look_up_by(spender):
    with pytest.raises(ValueError, match="email, domain, or crm_reference_id"):
        CrmLookupAgent().execute({}, spender)


def test_a_missing_token_fails_the_call_rather_than_the_process(monkeypatch):
    monkeypatch.delenv("HUBSPOT_ACCESS_TOKEN", raising=False)

    envelope = CrmLookupAgent().run({"domain": "northwind.com"})

    assert envelope["status"] == "error"
    assert "HUBSPOT_ACCESS_TOKEN" in envelope["output"]["error"]
