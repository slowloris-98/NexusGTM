"""Clay is the retrieval half of the enrichment waterfall.

What matters is not that a happy-path lookup maps fields, but that a *miss*
behaves correctly: it must not look like an error, must not write a
`firmographics` key that would unblock scoring on nothing, and must leave enough
on the blackboard for the planner to reach for the fallback instead of retrying.

One routine now returns firmographics AND the raw signal columns, so the second
half of this file pins the normaliser that turns those columns into phrases --
including the thing it exists to guarantee, that the raw blobs never reach the
blackboard at all.

No network: the vendor clients are patched at the method boundary, and the
transport-level behaviour is pinned separately in test_vendor_clients.py.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
import yaml

from core import config
from departments.revops import clay
from departments.revops.clay import ClayEnrichAgent, ClaySourceAgent


@pytest.fixture(autouse=True)
def clay_env(monkeypatch):
    monkeypatch.setenv("CLAY_API_KEY", "test-key")
    monkeypatch.setenv("CLAY_ENRICH_ROUTINE_ID", "routine-enrich")


@pytest.fixture
def clay_returns(monkeypatch):
    """Script what Clay's routine/search endpoints hand back."""

    def install(response: dict, *, method: str = "run_routine"):
        calls: list = []

        def fake(self, *args, **kwargs):
            calls.append((args, kwargs))
            return response

        monkeypatch.setattr(clay.ClayClient, method, fake)
        return calls

    return install


@pytest.fixture
def signals_policy(tmp_path, monkeypatch):
    """Point the playbook loader at a temp file carrying a known signals block."""

    def write(block: dict | None = {"recency_days": 180, "max_per_category": 3}):
        pb = {} if block is None else {"signals": block}
        (tmp_path / "playbook.yaml").write_text(yaml.safe_dump(pb), encoding="utf-8")
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        config.load.cache_clear()
        return pb

    yield write
    config.load.cache_clear()  # do not leak the temp playbook into later tests


POLICY = {"recency_days": 180, "max_per_category": 3}

LEAD = {
    "name": "Priya Raman",
    "email": "priya@northwind.com",
    "company": "Northwind Logistics",
}


def days_ago(n: int) -> str:
    """Dates in these fixtures must be relative.

    A literal like "2025-11-03" passes on the day it is written and starts
    failing once it drifts past `recency_days` -- months later, in a test that
    looks nothing like a calendar bug.
    """
    return (date.today() - timedelta(days=n)).isoformat()


def routine_result(*results, failed: int = 0) -> dict:
    """A completed routine run, in the shape Clay's results endpoint returns.

    Rows come back wrapped as {id, status, result}; a search returns records
    bare. That difference is what `_records_of` keys on.
    """
    data = [
        {"id": f"row-{i}", "status": "complete", "result": r}
        for i, r in enumerate(results, start=1)
    ]
    data += [
        {"id": f"fail-{i}", "status": "failed", "error": {"message": "no match"}}
        for i in range(failed)
    ]
    return {
        "routine_run_id": "run-1",
        "status": "complete",
        "total": len(data),
        "finished": len(data),
        "data": data,
    }


# ---------------------------------------------------------- the routine's columns
# Built to the shapes the live routine actually emits, nesting and column names
# included, so that a test going red because Clay changed reads as a Clay change
# rather than a puzzle. Every field these carry that the readers deliberately do
# NOT read is here on purpose -- it is what the leakage test asserts against.


def jobs_column(found: int, returned: int | None = None, titles=()) -> dict:
    return {
        "total jobs found": found,
        "total jobs returned": len(titles) if returned is None else returned,
        "Jobs": [
            {"id": f"job-{i}", "job_title": title, "url": "https://example.com/j"}
            for i, title in enumerate(titles)
        ],
    }


def news_column(*items) -> dict:
    """Each item is (title, days_old) or (title, days_old, description)."""
    return {
        "News": [
            {
                "Domain": "northwind.com",
                "Id": f"news-{i}",
                "News Title": title,
                "Publish Date": None if age is None else days_ago(age),
                "News URL": "https://example.com/n",
                "Publisher": "Business Wire",
                **({"Description": rest[0]} if rest else {}),
            }
            for i, (title, age, *rest) in enumerate(items)
        ]
    }


def tech_column(*names, joined: str | None = None, description: str | None = None) -> dict:
    column: dict = {
        "Products": [
            {
                "Hg Product Id": 20000 + i,
                "Product Name": name,
                "Vendor Name": name,
                "Vendor Domain": "example.com",
                "Most Specific Category": "Sales",
                "Product Last Verified Date": days_ago(300),
                **({"Product Description": description} if description else {}),
            }
            for i, name in enumerate(names)
        ]
    }
    if joined is not None:
        column["Product Names"] = joined
    return column


def test_a_match_produces_firmographics_stamped_as_retrieved(clay_returns, spender):
    clay_returns(
        routine_result(
            {
                "company_name": "Northwind Logistics",
                "domain": "northwind.com",
                "industry": "Logistics Software",
                "employee_count": 420,
                "revenue_band": "$25M-$50M",
                "hq_region": "North America",
            }
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert output["clay_enrichment"]["matched"] is True
    firmographics = output["firmographics"]
    assert firmographics["employee_count"] == 420
    # The stamp is load-bearing: crm_sync reads it to decide whether these
    # numbers may be written to HubSpot under their canonical property names.
    assert firmographics["source"] == "clay"


def test_a_miss_withholds_firmographics_so_scoring_stays_blocked(clay_returns, spender):
    """The whole point of the miss path.

    Returning an empty `firmographics` would satisfy ScoringAgent's required
    field and let the planner score a company it knows nothing about. The same
    argument covers `buying_signals`: Clay never identified the company, so an
    empty list would assert an absence nobody actually checked.
    """
    clay_returns(routine_result())

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert "firmographics" not in output
    assert "buying_signals" not in output
    assert "signal_source" not in output
    assert output["clay_enrichment"]["matched"] is False
    assert "northwind.com" in output["clay_enrichment"]["reason"]


def test_a_miss_is_not_an_error(clay_returns):
    """It must reach the planner as an observation, not a failed run."""
    clay_returns(routine_result())

    envelope = ClayEnrichAgent().run({"lead": LEAD})

    assert envelope["status"] == "ok"


def test_a_consumer_address_is_answered_without_paying_clay(clay_returns, spender):
    calls = clay_returns(routine_result())

    output = ClayEnrichAgent().execute(
        {"lead": {"email": "alexdoe94@gmail.com", "company": "self-employed"}}, spender
    )

    assert output["clay_enrichment"]["matched"] is False
    assert "gmail.com" in output["clay_enrichment"]["reason"]
    assert "buying_signals" not in output  # no call means nothing to report
    assert calls == []  # never dispatched
    assert spender.events == []


def test_credits_are_billed_to_the_run(clay_returns, monkeypatch):
    """Clay bills credits, and the budget guardrail halts on a total.

    Without this an agent could burn a vendor quota and report a spend of zero.
    Driven through `run` rather than `execute` so the attribution the
    orchestrator will actually record is what gets asserted.

    The call count is asserted alongside the cost event, not instead of it: a
    second routine call that happened to report zero credits would slip past a
    cost-event count. One qualified lead is one Clay run.
    """
    monkeypatch.setattr(clay, "CLAY_CREDIT_USD", 0.002)
    calls = clay_returns(
        {
            **routine_result({"company_name": "Northwind", "domain": "northwind.com"}),
            "credits_used": 5,
        }
    )

    envelope = ClayEnrichAgent().run({"lead": LEAD})

    assert envelope["status"] == "ok"
    assert len(calls) == 1
    assert len(envelope["cost_events"]) == 1
    event = envelope["cost_events"][0]
    assert event["amount_usd"] == pytest.approx(0.010)
    assert (event["department"], event["agent"]) == ("revops", "clay_enrich")


def test_a_missing_key_fails_the_call_rather_than_the_process(monkeypatch):
    """A partial vendor setup is a re-plan, not a crash."""
    monkeypatch.delenv("CLAY_API_KEY", raising=False)

    envelope = ClayEnrichAgent().run({"lead": LEAD})

    assert envelope["status"] == "error"
    assert "CLAY_API_KEY" in envelope["output"]["error"]


def test_a_lead_or_an_account_will_do(clay_returns, spender):
    """The expansion flow has an account, not a lead.

    Both key off the domain, so requiring `lead` would cost the planner a
    rejected dispatch and a re-plan every time it followed the pattern prose.
    """
    clay_returns(routine_result({"company_name": "Northwind", "domain": "northwind.com"}))

    output = ClayEnrichAgent().execute(
        {"account": {"name": "Northwind", "domain": "northwind.com"}}, spender
    )

    assert output["firmographics"]["company_name"] == "Northwind"


def test_neither_a_lead_nor_an_account_raises(spender):
    with pytest.raises(ValueError, match="lead or an account"):
        ClayEnrichAgent().execute({}, spender)


def test_sourcing_promotes_one_candidate_and_keeps_the_rest(
    clay_returns, spender, monkeypatch
):
    """One orchestration qualifies one lead; the list stays for visibility."""
    monkeypatch.setattr(clay, "default_source_limit", lambda: 10)
    # A search returns records bare under `data`, with no per-row result wrapper.
    clay_returns(
        {
            "data": [
                {"company_name": "Northwind", "domain": "northwind.com"},
                {"company_name": "Quillstack", "domain": "quillstack.com"},
            ],
            "has_more": False,
        },
        method="search",
    )

    output = ClaySourceAgent().execute({"icp_brief": "B2B software in NA"}, spender)

    assert len(output["sourced_accounts"]) == 2
    assert output["lead"]["company"] == "Northwind"
    assert output["lead"]["source"] == "clay_source"


def test_sourcing_requires_a_brief(spender):
    with pytest.raises(ValueError, match="icp_brief"):
        ClaySourceAgent().execute({"icp_brief": "   "}, spender)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (420, 420),
        (420.7, 420),
        ("420", 420),
        ("1,200", 1200),
        ("1_200", 1200),
        ("500+", 500),
        ("420.0", 420),
        ("~350", 350),
        (">1000 employees", 1000),
        ("", 0),
        (None, 0),
        ("unknown", 0),
    ],
)
def test_headcount_survives_a_text_typed_clay_column(raw, expected, clay_returns, spender):
    """A Clay output column typed Text is a legitimate configuration.

    Falling back to 0 would not be a harmless default -- it trips the
    under_10_employees negative signal and disqualifies a good lead.
    """
    clay_returns(
        routine_result(
            {"company_name": "Northwind", "domain": "northwind.com", "employee_count": raw}
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert output["firmographics"]["employee_count"] == expected


def test_clays_own_display_titles_map_without_renaming(clay_returns, spender):
    """A function wired straight from "Enrich Company" emits titles like this.

    Exact-key lookup would miss every one of them, and a miss reads downstream
    as "Clay had no data" rather than "we asked for the wrong key". Firmographics
    only: the signal columns are pinned to one routine's names and read through
    the four readers below, not through this aliasing.
    """
    clay_returns(
        routine_result(
            {
                "Name": "Northwind Logistics",
                "Domain": "northwind.com",
                "Industry": "Logistics Software",
                "Employee Count": "1,200",
                "Estimated Annual Revenue": "$50M-$100M",
                "Headquarters": "Chicago, IL",
            }
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)
    firmographics = output["firmographics"]

    assert firmographics["company_name"] == "Northwind Logistics"
    assert firmographics["industry"] == "Logistics Software"
    assert firmographics["employee_count"] == 1200
    assert firmographics["revenue_band"] == "$50M-$100M"
    assert firmographics["hq_region"] == "Chicago, IL"
    assert output["signal_source"] == "clay"


@pytest.mark.parametrize(
    "column", ["employee_count", "Employee Count", "employeeCount", "EMPLOYEE_COUNT"]
)
def test_column_naming_style_does_not_change_the_answer(column, clay_returns, spender):
    clay_returns(
        routine_result(
            {"company_name": "Northwind", "domain": "northwind.com", column: 420}
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert output["firmographics"]["employee_count"] == 420


# ------------------------------------------------------------ signal normaliser


def test_a_hand_written_signals_column_is_still_honoured(clay_returns, spender):
    """A workspace that already emits a `signals` column keeps working.

    A human's phrasing beats anything assembled here, so it leads the list.
    Note the record carries a company_name: without one the miss path swallows
    it and this asserts on nothing.
    """
    clay_returns(
        routine_result(
            {
                "company_name": "Northwind",
                "signals": [
                    "raised Series B",
                    {"label": "hired VP RevOps"},
                    {"type": "job_change"},
                ],
            }
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert output["buying_signals"] == [
        "raised Series B",
        "hired VP RevOps",
        "job_change",
    ]
    assert output["signal_source"] == "clay"


def test_no_signals_is_an_answer_not_a_failure(clay_returns, spender):
    """"We looked and there is nothing" must not read as "we never looked".

    Three different answers from the same agent, and this pins the first: every
    column arrived and every one is empty, so the list is empty and `reason` says
    nothing. An absent `buying_signals` key means Clay never matched the company
    at all; a populated `reason` means the routine did not send a column. Do not
    "simplify" any of the three into the others -- the planner reads them apart.
    """
    clay_returns(
        routine_result(
            {
                "company_name": "Northwind",
                "domain": "northwind.com",
                "job_postings": {"total jobs found": 0, "Jobs": []},
                "latest_funding": {},
                "recent_news": {"News": []},
                "technologies": {"Products": []},
            }
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert output["buying_signals"] == []
    assert output["clay_enrichment"]["matched"] is True
    assert output["clay_enrichment"]["reason"] == ""


@pytest.mark.parametrize(
    "raw, expected",
    [
        (jobs_column(1195, titles=["VP RevOps"]), ["1195 open roles"]),
        (jobs_column(3), ["3 open roles"]),
        (jobs_column(0), []),
        ({"Jobs": [{"job_title": "VP RevOps"}]}, []),   # no count column
        ({}, []),
        (None, []),
        # A failed cell holds the provider's message, not a shape to parse.
        ("No company with that domain found", []),
    ],
)
def test_the_jobs_column_becomes_an_open_roles_count(raw, expected):
    assert clay._job_phrases({"job_postings": raw}) == expected


def test_job_titles_are_never_published():
    """A title would assert a freshness this column cannot support.

    Nothing under `Jobs` is a posted date this routine maps, so `_within` would
    keep every posting forever -- and outreach_draft reads this list top-down for
    an opening line. Map a posted date and this is the test to revisit.
    """
    column = jobs_column(42, titles=[f"Role {i}" for i in range(9)])

    phrases = clay._job_phrases({"job_postings": column})

    assert phrases == ["42 open roles"]
    assert not any(phrase.startswith("hiring:") for phrase in phrases)


def test_total_jobs_returned_is_not_the_count_we_publish():
    """It is the API's page size, not the company's hiring volume.

    Publishing it would understate a big employer as reliably as it flatters a
    small one, and it sits one key away from the field we do want.
    """
    column = jobs_column(137, returned=10, titles=[f"Role {i}" for i in range(10)])

    assert clay._job_phrases({"job_postings": column}) == ["137 open roles"]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ({"Funding Stage": "Series B", "Latest Funding Amount": 30_000_000},
         ["funding: raised Series B $30M"]),
        ({"Funding Stage": "Series D", "Latest Funding Amount": 1_200_000_000},
         ["funding: raised Series D $1.2B"]),
        ({"Funding Stage": "Seed", "Latest Funding Amount": 750_000},
         ["funding: raised Seed $750K"]),
        # "$30M" is already formatted; _as_int would read it as 30.
        ({"Funding Stage": "Series B", "Latest Funding Amount": "$30M"},
         ["funding: raised Series B $30M"]),
        ({"Funding Stage": "Series A"}, ["funding: raised Series A"]),
        ({"Latest Funding Amount": 5_000_000}, ["funding: raised $5M"]),
        # A bare string is the provider's message, never prose worth splitting.
        ("Series B, $30M, Jan 2025", []),
        ("Company not found", []),
        ({}, []),
        (None, []),
    ],
)
def test_the_funding_column_becomes_one_assembled_phrase(raw, expected):
    assert clay._funding_phrases({"latest_funding": raw}, 180) == expected


def test_a_stale_round_is_not_a_signal():
    """Congratulating someone on a round they closed two years ago.

    Worth its own test because it is the live behaviour on most rows of the real
    table -- without it the empty result reads as "the mapping is still broken"
    and someone re-breaks the recency filter chasing it.
    """
    column = {
        "Funding Stage": "Series E",
        "Latest Funding Amount": 250_000_000,
        "Last Funding Date": days_ago(400),
    }

    assert clay._funding_phrases({"latest_funding": column}, 180) == []


def test_the_funding_headline_is_not_the_funding_phrase():
    """Funding News Title is human-written and richer. It is still not used.

    Its shape changes with every publisher, it eats the whole phrase budget, and
    it names the company back at itself in an email addressed to that company.
    """
    column = {
        "Funding Stage": "Series E",
        "Latest Funding Amount": 250_000_000,
        "Funding News Title": "Northwind Raises $250M in Series E at $3.25B Valuation",
        "Company Valuation": 3_250_000_000,
        "Investors": "Accel, CRV, GV",
    }

    phrases = clay._funding_phrases({"latest_funding": column}, 180)

    assert phrases == ["funding: raised Series E $250M"]
    assert "Valuation" not in " ".join(phrases)
    assert "Accel" not in " ".join(phrases)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (news_column(("Opens Berlin office", 5)), ["news: Opens Berlin office"]),
        # Newest first: outreach_draft opens with whatever leads the list.
        (news_column(("Opens Berlin office", 5), ("Names new CFO", 2)),
         ["news: Names new CFO", "news: Opens Berlin office"]),
        (news_column(), []),
        ({"News": "News not found"}, []),
        ("News not found", []),
        ({}, []),
        (None, []),
    ],
)
def test_the_news_column_becomes_news_phrases(raw, expected):
    assert clay._news_phrases({"recent_news": raw}, 180, 3) == expected


@pytest.mark.parametrize(
    "raw",
    [
        tech_column("Salesforce", "Marketo", "Segment"),
        tech_column("Salesforce", "Marketo", "Segment", "salesforce"),
        tech_column(joined="Salesforce, Marketo, Segment"),
        tech_column(joined="Salesforce; Marketo | Segment"),
    ],
)
def test_technologies_collapse_to_one_phrase(raw):
    """A stack is context, not an event.

    One phrase per tool would let a long tech list out-vote a funding round on
    phrase count alone.
    """
    assert clay._tech_phrases({"technologies": raw}, 3) == [
        "uses Salesforce, Marketo, Segment"
    ]


def test_the_product_list_wins_over_the_pre_joined_names_when_they_disagree():
    """Product Names is derived from Products, so the array is right by
    construction -- and reading the joined string means guessing which separator
    the provider chose, which is the guessing this module is done with."""
    assert clay._tech_phrases({"technologies": tech_column("Segment", joined="Salesforce")}, 3) == [
        "uses Segment"
    ]


def test_the_pre_joined_product_names_are_a_fallback_when_products_is_absent():
    """A workspace can enable the summary column without the detail array."""
    column = {"Product Names": "Salesforce, Segment"}

    assert clay._tech_phrases({"technologies": column}, 3) == ["uses Salesforce, Segment"]


@pytest.mark.parametrize("age, kept", [(10, True), (179, True), (400, False)])
def test_stale_items_are_dropped_and_recent_ones_kept(age, kept):
    """Only the two columns that carry a date -- jobs publish a count instead."""
    record = {
        "recent_news": news_column(("Opens Berlin office", age)),
        "latest_funding": {
            "Funding Stage": "Series B",
            "Last Funding Date": days_ago(age),
        },
    }

    signals = clay._buying_signals(record, POLICY)

    assert bool(signals) is kept


@pytest.mark.parametrize("stamp", [None, "", "sometime last spring", "not a date"])
def test_an_unparseable_date_is_kept_rather_than_dropped(stamp):
    """Unreadable is not the same as old.

    Dropping these would delete real events over a formatting mismatch -- and
    read downstream as "the company is quiet".
    """
    record = {
        "recent_news": {
            "News": [{"News Title": "Opens Berlin office", "Publish Date": stamp}]
        }
    }

    assert clay._news_phrases(record, 180, 3) == ["news: Opens Berlin office"]


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-03-14",
        "2026-03-14T09:00:00Z",
        # What Publish Date and Last Funding Date actually emit.
        "2026-03-14T00:00:00.000Z",
        "Mar 14, 2026",
        "March 14, 2026",
        "03/14/2026",
    ],
)
def test_the_date_formats_a_clay_column_actually_emits_all_parse(stamp):
    assert clay._as_date(stamp) == date(2026, 3, 14)


def test_the_signal_order_puts_funding_before_hiring_before_news():
    """outreach_draft reads the list top-down looking for an opening line."""
    record = {
        "technologies": tech_column("Segment"),
        "recent_news": news_column(("Opens Berlin office", 9)),
        "job_postings": jobs_column(42),
        "latest_funding": {"Funding Stage": "Series B"},
        "signals": ["champion changed jobs"],
    }

    assert clay._buying_signals(record, POLICY) == [
        "champion changed jobs",
        "funding: raised Series B",
        "42 open roles",
        "news: Opens Berlin office",
        "uses Segment",
    ]


def test_the_phrase_cap_comes_from_the_playbook(signals_policy):
    signals_policy({"recency_days": 180, "max_per_category": 1})
    record = {"recent_news": news_column(("first", 1), ("second", 2), ("third", 3))}

    assert clay._news_phrases(record, 180, clay.signal_policy()["max_per_category"]) == [
        "news: first"
    ]


def test_the_recency_window_comes_from_the_playbook(signals_policy):
    signals_policy({"recency_days": 7, "max_per_category": 3})
    record = {"recent_news": news_column(("Opens Berlin office", 30))}

    assert clay._buying_signals(record, clay.signal_policy()) == []


def test_missing_signal_policy_costs_nothing(signals_policy, clay_returns):
    """A misconfigured playbook must fail free, not after paying Clay.

    The policy is read at the top of execute(), before the vendor call, so this
    reaches the planner as a free, re-plannable observation rather than a credit
    spent on a response the agent then cannot normalise.
    """
    signals_policy(None)
    calls = clay_returns(routine_result({"company_name": "Northwind"}))

    envelope = ClayEnrichAgent().run({"lead": LEAD})

    assert envelope["status"] == "error"
    assert calls == []
    assert envelope["cost_events"] == []


def test_raw_signal_blobs_never_reach_the_blackboard(clay_returns, spender):
    """The reason the normaliser is in Python at all.

    sales/server.py:56 serialises its ENTIRE payload into the outreach prompt.
    Every news item carries a paragraph of Description and every product another,
    so the raw columns must stay inside execute() and only the phrases go out.

    Each assertion below names a field a reader deliberately does not read. They
    are cheap and they are the only thing standing between a helpful-looking
    "just pass the whole column through" edit and a re-tokenised blob charged
    against the budget guardrail at every later step.
    """
    filler = "a very long description that must never reach a prompt " * 5
    clay_returns(
        routine_result(
            {
                "company_name": "Northwind",
                "domain": "northwind.com",
                "job_postings": jobs_column(42, titles=[f"Role {i}" for i in range(40)]),
                "latest_funding": {
                    "Funding Stage": "Series B",
                    "Latest Funding Amount": 30_000_000,
                    "Investors": "Accel, CRV, GV, Notable Capital",
                    "Funding News Title": "Northwind Raises $30M in Series B",
                    "Funding News URL": "https://example.com/funding",
                },
                "recent_news": news_column(("Opens Berlin office", 3, filler)),
                "technologies": tech_column("Salesforce", description=filler),
            }
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)
    serialised = json.dumps(output)

    assert set(output) == {
        "firmographics",
        "buying_signals",
        "signal_source",
        "clay_enrichment",
    }
    assert "very long description" not in serialised
    assert "Accel" not in serialised              # Investors
    assert "Northwind Raises" not in serialised   # Funding News Title
    assert "example.com" not in serialised        # every URL in every column
    assert "Role 0" not in serialised             # Jobs[].job_title
    assert "Business Wire" not in serialised      # News[].Publisher
    assert "42 open roles" in output["buying_signals"]


def test_one_routine_call_per_invocation(clay_returns, spender):
    """The merge's whole economic claim: one qualified lead is one Clay run."""
    calls = clay_returns(
        routine_result(
            {
                "company_name": "Northwind",
                "domain": "northwind.com",
                "latest_funding": {
                    "Funding Stage": "Series B",
                    "Last Funding Date": days_ago(10),
                },
            }
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert len(calls) == 1
    assert output["firmographics"]["source"] == "clay"
    assert output["buying_signals"] == ["funding: raised Series B"]


def test_the_real_routine_shape_produces_all_four_phrase_kinds(clay_returns, spender):
    """One record in exactly the shape the live routine emits.

    Every other test in this half narrows to one column. This one exists so that
    when the routine changes, a single failure names the whole contract -- rather
    than a scatter of unit failures nobody reads as "the mapping moved". It is
    also the test to diff against a real response when adding a column.
    """
    clay_returns(
        routine_result(
            {
                "domain": "northwind.com",
                "Name": "Northwind Logistics",
                "Website": "https://northwind.com",
                "Employee Count": 1011,
                "Industry": "Logistics Software",
                "Annual Revenue": "200M-500M",
                "hq_region": "San Francisco, California",
                "job_postings": jobs_column(1195, titles=["VP RevOps"]),
                "latest_funding": {
                    "Domain": "northwind.com",
                    "Company Name": "Northwind Logistics",
                    "Funding Stage": "Series E",
                    "Latest Funding Amount": 250_000_000,
                    "Company Valuation": 3_250_000_000,
                    "Last Funding Date": days_ago(20),
                    "Investors": "Accel, CRV, GV",
                    "Funding News Title": "Northwind Raises $250M in Series E",
                },
                "recent_news": news_column(("Opens Berlin office", 9)),
                "technologies": tech_column("Salesforce", "Segment",
                                            joined="Salesforce, Segment"),
                "Product Names": "Salesforce, Segment",
            }
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    firmographics = output["firmographics"]
    assert firmographics["company_name"] == "Northwind Logistics"
    assert firmographics["domain"] == "northwind.com"
    assert firmographics["employee_count"] == 1011
    assert firmographics["revenue_band"] == "200M-500M"
    assert output["buying_signals"] == [
        "funding: raised Series E $250M",
        "1195 open roles",
        "news: Opens Berlin office",
        "uses Salesforce, Segment",
    ]
    assert output["clay_enrichment"] == {"matched": True, "reason": ""}


@pytest.mark.parametrize(
    "column", ["job_postings", "latest_funding", "recent_news", "technologies"]
)
@pytest.mark.parametrize(
    "value",
    [
        "No company with that domain found",
        "Company not found",
        "News not found",
        None,
    ],
)
def test_a_failed_cells_error_text_never_becomes_a_signal(column, value):
    """The stakes are a fabricated claim in a customer-facing email.

    sales/server.py:56 serialises its entire payload into the outreach prompt,
    so `uses No company with that domain found` is not a cosmetic bug. This is
    not hypothetical either -- most rows of the real table have a failed cell,
    and technologies misses on the large majority of them.
    """
    assert clay._buying_signals({column: value}, POLICY) == []


def test_a_vendor_miss_is_not_reported_as_a_mapping_fault():
    """The provider having no record is an answer, not a fault.

    It is the same statement as clay_enrichment.matched=false, one column down.
    Flagging it would put a note on nearly every run and teach everyone to skip
    reading the field -- which is how the original bug survived.
    """
    record = {
        "job_postings": jobs_column(3),
        "latest_funding": "Company not found",
        "recent_news": "News not found",
        "technologies": "No company with that domain found",
    }

    assert clay._mapping_note(record) == ""
    assert clay._buying_signals(record, POLICY) == ["3 open roles"]


def test_a_missing_signal_column_is_named_in_the_reason_not_swallowed(
    clay_returns, spender
):
    """The failure this whole rewrite exists to end.

    Four columns normalised to nothing for as long as nobody looked, because a
    mis-mapped column and a genuinely quiet company published the same empty
    list. Now the phrases that did arrive still publish and the gap has a name.
    """
    clay_returns(
        routine_result(
            {
                "company_name": "Northwind",
                "domain": "northwind.com",
                "job_postings": jobs_column(42),
                "recent_news": news_column(("Opens Berlin office", 9)),
            }
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert output["clay_enrichment"]["matched"] is True
    assert output["buying_signals"] == ["42 open roles", "news: Opens Berlin office"]
    assert output["clay_enrichment"]["reason"] == (
        "no latest_funding, technologies columns in response"
    )


def test_one_missing_column_reads_as_one_column():
    record = {
        "job_postings": jobs_column(42),
        "latest_funding": {"Funding Stage": "Series B"},
        "recent_news": news_column(),
    }

    assert clay._mapping_note(record) == "no technologies column in response"


def test_a_column_in_an_unknown_shape_is_reported_rather_than_absorbed():
    """Present, not a miss, and not readable -- almost always a changed mapping."""
    record = {
        "job_postings": jobs_column(42),
        "latest_funding": {"Funding Stage": "Series B"},
        "recent_news": ["Opens Berlin office"],
        "technologies": ["Salesforce"],
    }

    assert clay._mapping_note(record) == "recent_news, technologies columns unreadable"


def test_a_clean_match_leaves_the_reason_empty(clay_returns, spender):
    """Almost every other record in this file omits signal columns, so this is
    the only place the healthy-row property is actually exercised."""
    clay_returns(
        routine_result(
            {
                "company_name": "Northwind",
                "domain": "northwind.com",
                "job_postings": jobs_column(42),
                "latest_funding": {"Funding Stage": "Series B"},
                "recent_news": news_column(),
                "technologies": tech_column(),
            }
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert output["clay_enrichment"]["reason"] == ""


def test_a_container_key_that_is_not_a_list_yields_no_phrases():
    """The shape polymorphism is gone, and must not creep back in."""
    assert clay._news_phrases({"recent_news": {"News": {"News Title": "x"}}}, 180, 3) == []
    assert clay._tech_phrases({"technologies": {"Products": "Salesforce"}}, 3) == []


@pytest.mark.parametrize("column", ["technologies", "Technologies", "TECHNOLOGIES"])
def test_signal_column_naming_style_does_not_change_the_answer(column):
    """One name, matched on letters and digits -- not a list of alternatives.

    The narrow thing `_pluck` still buys in the signal path. Replacing it with a
    bare record["technologies"] would break a Clay-wired column that merely
    capitalises differently, which is the silent-empty failure all over again.
    """
    assert clay._tech_phrases({column: tech_column("Segment")}, 3) == ["uses Segment"]


# ------------------------------------------------------- handoff into scoring


def test_signals_survive_the_handoff_into_scoring(monkeypatch):
    """The silent-drop risk in this change.

    clay_enrich publishes buying_signals at the top level, while the old
    enrichment nested them inside firmographics. If ScoringAgent's schema does
    not declare the property, the orchestrator's pre-dispatch validation still
    passes and the signals simply never reach the prompt -- no error anywhere.
    """
    import jsonschema

    from core import llm as llm_module
    from conftest import result as llm_result
    from departments.revops.server import ScoringAgent

    payload = {
        "firmographics": {"company_name": "Northwind", "source": "clay"},
        "buying_signals": ["funding: raised Series B $30M"],
    }

    # Exactly the check core.orchestrator._invoke runs before dispatching.
    jsonschema.validate(payload, ScoringAgent.input_schema)

    seen: dict = {}

    def capture(prompt, **kwargs):
        seen["prompt"] = prompt
        seen["instructions"] = kwargs.get("instructions", "")
        return llm_result(parsed={"fit_score": 80})

    monkeypatch.setattr(llm_module, "llm_call", capture)
    ScoringAgent().run(payload)

    assert "raised Series B" in seen["prompt"]
    # Reaching the prompt is not the same as being weighed. Without this the
    # signals arrive as unexplained JSON the scorer is free to ignore.
    assert "buying signal" in seen["instructions"].lower()
    assert "negative_signals" in seen["instructions"]
