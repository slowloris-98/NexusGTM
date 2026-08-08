"""Clay agents. RevOps owns the data foundation, so Clay lives here.

Clay is the retrieval half of the enrichment waterfall. `marketing.enrichment`
infers firmographics from a model; these agents fetch them from Clay's provider
marketplace. Same output shape on purpose -- `scoring` and `outreach_draft`
consume either interchangeably -- with a `source` stamp so downstream work can
tell a retrieved headcount from a guessed one.

Three conventions worth knowing before editing:

    A miss is not an error. When Clay has no match, these agents return
    status="ok" with `clay_enrichment.matched = false` and deliberately do NOT
    write a `firmographics` key. Writing an empty one would satisfy `scoring`'s
    required field and let the planner score nothing at all; withholding it keeps
    scoring blocked until either Clay or the marketing fallback produces real
    data, while still recording the miss so the planner does not retry Clay.
    `buying_signals` is withheld on the same miss for the same reason: an empty
    list would assert an absence nobody checked. See `ClayEnrichAgent.execute`.

    The signal fields are pinned to one workspace's routine, by name, once each.
    This module used to guess instead, from a dozen tuples of plausible column
    names, on the theory that routines are workspace-defined and a rename should
    not break the read. What it actually bought was four dead columns: the
    routine returned job postings, funding, news and technology, and every one of
    them normalised to nothing, for as long as nobody looked. An empty
    `buying_signals` is a documented real answer -- "we looked and this company is
    quiet" -- so a mis-mapped column and a genuine silence produced output no
    consumer could tell apart. Adding a thirteenth alias would have kept that
    property. Porting to another workspace means naming the function's outputs to
    match the constants below, or editing four short readers.

    Firmographics are a different bet and still read through `_pluck`'s aliases:
    those columns come straight out of Clay's own "Enrich Company", whose titles
    genuinely vary, and a wrong read there is loud -- `company_name` missing is
    the miss path, not a silent empty list.

    Signals are normalised in Python, not by a model. One routine returns
    firmographics AND the signal fields, and `_buying_signals` turns them into
    short phrases here. Two reasons, and the second is the load-bearing one. A
    model asked to summarise retrieved data guesses again, which is the thing
    Clay is here to remove. And the raw blobs must never reach the blackboard,
    because `sales.outreach_draft` serialises its ENTIRE payload into a prompt
    and charges it against the budget guardrail. Only the phrases go out.

    On that last point the function's output picker does most of the work: it
    emits scalar leaves only, so the `Jobs` array, the `Products` array and a
    paragraph of `Description` per news story never leave Clay at all. Cheaper
    than fetching them to drop them, and the readers are flatter for it.

    A signal column the routine did not send is reported, not raised. It lands in
    `clay_enrichment.reason` while the columns that did arrive still publish --
    the distinction the old aliasing destroyed, made explicit. `_missing_columns`
    is honest about what the flat shape costs that distinction.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

from core.agent import Agent, Spender
from core.llm import strict_schema

from departments.revops.clients import CLAY_CREDIT_USD, ClayClient, VendorError

CONSUMER_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "proton.me",
    "protonmail.com",
    "icloud.com",
    "aol.com",
}


def _norm(key: str) -> str:
    """Reduce a column name to letters and digits, lowercased."""
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def _pluck(record: dict, *keys: str, default: Any = None) -> Any:
    """First non-empty value among the given column names.

    Matching is on the normalised name, because Clay's own enrichments emit
    display-style titles: "Employee Count", "employee_count" and "employeeCount"
    are the same field, and an exact lookup would miss two of the three silently
    -- which reads downstream as "Clay had no data" rather than "we asked wrong".

    Two callers, two different uses. The firmographic reads and `_bill_credits`
    pass several names, because Clay's enrichment titles genuinely vary between
    workspaces. **The signal readers pass exactly one** and rely on `_norm`
    alone; guessing among alternatives there is what hid four dead columns, and
    the module docstring has the long version.
    """
    if not isinstance(record, dict):
        return default

    normalized = {_norm(k): v for k, v in record.items()}
    for key in keys:
        value = normalized.get(_norm(key))
        if value not in (None, "", [], {}):
            return value
    return default


def _as_int(value: Any) -> int:
    """Coerce a headcount that may arrive as text.

    Clay output columns can be typed Text, and providers format headcounts as
    "1,200", "500+", or "420.0". A bare int() rejects all three and would fall
    back to 0 -- which is not a harmless default: scoring reads a zero-employee
    company, trips the under_10_employees negative signal, and disqualifies a
    good lead with a plausible-looking reason. Parse the leading number instead.
    """
    if isinstance(value, bool) or value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return int(value)

    digits = ""
    for char in str(value).strip():
        if char.isdigit():
            digits += char
        elif char in ",_ ":
            continue  # thousands separators
        elif char == "." and digits:
            break  # "420.7" -> 420, not 4207
        elif digits:
            break  # "500+", "1200 employees"
        else:
            continue  # leading "~", "$", ">"
    return int(digits) if digits else 0


def _domain_of(lead: dict, firmographics: dict | None = None) -> str:
    for candidate in (
        (firmographics or {}).get("domain"),
        lead.get("domain"),
        lead.get("website"),
    ):
        if candidate:
            return str(candidate).strip().lower().removeprefix("https://").removeprefix(
                "http://"
            ).removeprefix("www.").split("/")[0]
    email = str(lead.get("email") or "")
    return email.split("@")[-1].strip().lower() if "@" in email else ""


def _is_consumer_email(lead: dict) -> bool:
    """Computed, not inferred -- this is a set membership test, not a judgment."""
    email = str(lead.get("email") or "")
    if "@" not in email:
        return False
    return email.rsplit("@", 1)[-1].strip().lower() in CONSUMER_EMAIL_DOMAINS


def _bill_credits(llm: Spender, response: dict) -> None:
    """Charge Clay credits to the run's budget.

    Credits are real spend, and the budget guardrail halts on a total. Without
    this an agent could burn a vendor quota and report zero.

    Clay does not put a per-call credit count in the documented response, so
    this reads several plausible fields and falls back to the item count against
    CLAY_CREDIT_USD. An unset rate prices at zero, the same convention
    `core.llm.price_of` uses for a model missing from the table: report nothing
    rather than invent a number.
    """
    credits = _pluck(response, "credits_used", "credits", "creditsUsed", default=0)
    usage = response.get("usage") or {}
    if not credits and isinstance(usage, dict):
        credits = _pluck(usage, "credits_used", "credits", default=0)
    if not credits:
        credits = response.get("finished") or len(_records_of(response))
    try:
        billable = float(credits or 0)
    except (TypeError, ValueError):
        billable = 0.0
    # Gate on the priced amount, not the credit count: with no rate configured
    # every call would otherwise log a zero-dollar event and clutter the ledger.
    amount = billable * CLAY_CREDIT_USD
    if amount:
        llm.record_external(amount)


def _records_of(response: dict) -> list[dict]:
    """Pull the record dicts out of a Clay response.

    Routines and Searches both answer under `data`, but a routine wraps each row
    as `{id, status, result}` while a search returns the record directly. The
    `result` key is what tells them apart. A row Clay marks `failed` is dropped:
    a partial answer is still an answer, and the miss path handles an empty list.
    """
    rows = response.get("data")
    if not isinstance(rows, list):
        return []

    records = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "result" in row:
            if row.get("status") == "failed":
                continue
            result = row.get("result")
            if isinstance(result, dict):
                records.append(result)
        else:
            records.append(row)
    return records


# ---------------------------------------------------------------- signal fields
# The routine's signal outputs arrive FLAT. Clay's function output picker only
# emits scalar leaves -- you select `recent_news > News > 0 > News Title`, not the
# `recent_news` object -- so nothing here is nested and there is nothing to
# unwrap. That is a better deal than it sounds: the `Jobs` array, the `Products`
# array and every `Description` paragraph stay in Clay instead of crossing the
# wire to be discarded inside `execute`.
#
# One name per field, no alternatives. Guessing among plausible names is what hid
# four dead columns; see the module docstring.
JOBS_TOTAL = "Total Jobs Found"
FUNDING_STAGE = "Funding Stage"
FUNDING_AMOUNT = "Latest Funding Amount"
FUNDING_DATE = "Last Funding Date"
NEWS_TITLE = "News Title"
NEWS_DATE = "Publish Date"
TECH_NAMES = "Product Names"

# Clay numbers repeated leaves from the second one: "News Title", "News Title 2",
# "News Title 3". `_news_slots` walks that sequence for as far as the function
# maps it, so adding a fourth pair in Clay needs no edit here.
def _slot(field: str, index: int) -> str:
    return field if index == 1 else f"{field} {index}"


# Which flat fields came from which Clay column. Used only by `_missing_columns`,
# to answer "did the routine send this column at all" now that a column is no
# longer one key. The readers below name the same constants at their point of
# use, so the two cannot drift apart into half-agreeing lists.
SIGNAL_COLUMNS = {
    "job_postings": (JOBS_TOTAL,),
    "latest_funding": (FUNDING_STAGE, FUNDING_AMOUNT, FUNDING_DATE),
    "recent_news": (NEWS_TITLE, NEWS_DATE),
    "technologies": (TECH_NAMES,),
}

# What a failed enrichment cell holds instead of data. These are not speculative:
# the live table shows all three, and `technologies` misses on most rows because
# the provider simply has no record for many domains.
#
# A miss is data about the lookup, never data about the company -- the same
# statement as `clay_enrichment.matched = false`, one column down. Left unread it
# becomes `uses No company with that domain found`, and `sales.outreach_draft`
# opens an email with it. Matched on a prefix, since providers append the domain.
VENDOR_MISS_PREFIXES = (
    "no company with",
    "company not found",
    "news not found",
    "not found",
    "no results",
    "no data",
)

# The one column still read permissively, because a person wrote it -- see
# `_explicit_phrases`. Narrowed to a single name: this tuple used to include
# `buying_signals`, this agent's own output key, so a routine column named
# "Buying Signals" would have flowed unvalidated to the top of the phrase list
# and into the outreach prompt.
EXPLICIT_KEYS = ("signals",)

# Presentation, not policy -- the same category as queue names and segments,
# which stay in the agent's own contract rather than the playbook. One headline
# stays one line in a prompt.
SIGNAL_PHRASE_MAX_CHARS = 140


def _as_date(value: Any) -> date | None:
    """Best-effort date from a loosely typed Clay column, or None.

    Unreadable is not the same as old, and an unparseable date is None -- which
    survives the recency filter rather than silently deleting a real event. A
    date format nobody anticipated should cost a filter, never a signal.

    `Publish Date` and `Last Funding Date` both arrive as "...T00:00:00.000Z",
    which the isoformat branch handles once the Z is swapped for an offset.
    """
    if value in (None, "", [], {}) or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value) / (1000.0 if abs(float(value)) > 1e11 else 1.0)
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y",
                "%B %d, %Y", "%d %b %Y", "%b %Y", "%B %Y", "%Y-%m"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _within(when: date | None, days: int) -> bool:
    """A dated item ages out; an undated one does not.

    Dropping stale events is not tidiness -- `outreach_draft` opens with whatever
    it is handed, and congratulating someone on a round they closed eleven months
    ago is worse than saying nothing. But dropping UNdated items would delete
    real events over a formatting mismatch, which is the failure `_as_date`
    returns None for in the first place.

    Only funding and news reach this. Job postings carry no date field this
    routine maps, so rather than publish titles that would never age out,
    `_job_phrases` publishes a count -- true about now whatever a posting's age.
    """
    return True if when is None else (date.today() - when).days <= days


def _phrase(text: Any) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= SIGNAL_PHRASE_MAX_CHARS:
        return cleaned
    return cleaned[: SIGNAL_PHRASE_MAX_CHARS - 3].rstrip() + "..."


def _split_list(text: str) -> list[str]:
    """A Clay column typed Text holding a list: "Salesforce, Marketo; Segment"."""
    for separator in ("\n", ";", "|", "/"):
        text = text.replace(separator, ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def _dedupe(phrases: list[str]) -> list[str]:
    seen: set[str] = set()
    unique = []
    for phrase in phrases:
        if phrase and phrase.lower() not in seen:
            seen.add(phrase.lower())
            unique.append(phrase)
    return unique


def _vendor_missed(value: Any) -> bool:
    """True when a cell holds a provider's "nothing found" message.

    Deliberately not folded into `_phrase`, which `_money` also calls: a company
    genuinely named "Not Found Labs" must survive. Signal labels only.
    """
    if not isinstance(value, str):
        return False
    return " ".join(value.split()).lower().startswith(VENDOR_MISS_PREFIXES)


def _signal_label(value: Any) -> str:
    """A phrase-ready label, or "" when the field holds a provider's miss."""
    return "" if _vendor_missed(value) else _phrase(value)


def _recent_first(
    items: list[tuple[str, date | None]], days: int
) -> list[tuple[str, date | None]]:
    fresh = [item for item in items if _within(item[1], days)]
    # Undated items sort last rather than jumping the queue on a date never read.
    fresh.sort(key=lambda item: item[1] or date.min, reverse=True)
    return fresh


def _money(value: Any) -> str:
    """Format a raised amount; a pre-formatted string is trusted as-is.

    `_as_int("$30M")` is 30, and "raised $30" is a worse answer than the string
    the provider already wrote -- so only a real number gets humanised here.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        amount = float(value)
        for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
            if abs(amount) >= size:
                text = f"{amount / size:.1f}"
                return f"${text[:-2] if text.endswith('.0') else text}{unit}"
        return f"${int(amount)}"
    return _phrase(value)


def _explicit_phrases(record: dict) -> list[str]:
    """A workspace that already emits a `signals` column still works.

    Kept from the agent this one absorbed. A hand-authored column is a human's
    phrasing and beats anything assembled here, so it leads the list.

    The one reader that stays shape-tolerant, and the line is worth stating: the
    *vendor* columns are pinned because a provider's undocumented output changing
    shape is a fault to surface, while this column is a person's and bending to
    however they wrote it is the whole courtesy. Not a column this routine emits.
    """
    found = _pluck(record, *EXPLICIT_KEYS, default=[])
    if isinstance(found, str):
        return [_phrase(found)]

    phrases = []
    for item in found if isinstance(found, list) else []:
        if isinstance(item, str):
            phrases.append(_phrase(item))
        elif isinstance(item, dict):
            label = _pluck(item, "label", "type", "name", "signal", "title")
            if label:
                phrases.append(_phrase(label))
    return [phrase for phrase in phrases if phrase]


def _funding_phrases(record: dict, window: int) -> list[str]:
    """At most one phrase: the round before the latest is history, not a signal.

    The provider returns the single most recent round, so there is nothing to
    sort and no cap to apply.

    Ignored on purpose, and worth not re-adding: Funding News Title, Investors,
    Company Valuation. The headline is the tempting one -- human-written and
    richer, it carries the valuation -- but its shape changes with every
    publisher, it eats the whole phrase budget, and it names the company back at
    itself in an email addressed to that company. Leave them unmapped in Clay.
    """
    amount = _pluck(record, FUNDING_AMOUNT)
    parts = [
        part
        for part in (
            _signal_label(_pluck(record, FUNDING_STAGE, default="")),
            _money(amount) if amount not in (None, "") else "",
        )
        if part
    ]
    if not parts:
        return []

    when = _as_date(_pluck(record, FUNDING_DATE))
    return [f"funding: raised {' '.join(parts)}"] if _within(when, window) else []


def _job_phrases(record: dict) -> list[str]:
    """One count phrase, and never a job title.

    Takes no window and no cap, which is the whole point. The `Jobs` array does
    carry titles, but no posted-date field in it is mapped, so a published title
    would assert a freshness the data cannot support -- and `outreach_draft`
    reads this list top-down for an opening line, which is how a two-year-old
    requisition becomes the first sentence of an email. A count is true about now
    whatever any single posting's age.

    So the Clay function maps `Total Jobs Found` and nothing else from that
    column. Map a posted date alongside the titles and phrases become defensible
    again -- until then, leaving `Jobs` unmapped also keeps it off the wire.

    Do not reach for `Total Jobs Returned` if you ever map it: that is the API's
    page size, not the company's hiring volume, and it understates a big employer
    as reliably as it flatters a small one.
    """
    openings = _as_int(_pluck(record, JOBS_TOTAL))
    return [f"{openings} open roles"] if openings else []


def _news_slots(record: dict) -> list[tuple[str, date | None]]:
    """Walk `News Title` / `News Title 2` / ... for as far as the function maps.

    The stop condition is the *key* being absent, not the value being empty --
    "slot mapped but this company had only two stories" and "only two slots were
    ever mapped" are different facts, and stopping on an empty value would end
    the walk early on the first company with a short news list.
    """
    present = {_norm(key) for key in record}
    items = []
    index = 1
    while _norm(_slot(NEWS_TITLE, index)) in present:
        label = _signal_label(_pluck(record, _slot(NEWS_TITLE, index), default=""))
        if label:
            items.append((label, _as_date(_pluck(record, _slot(NEWS_DATE, index)))))
        index += 1
    return items


def _news_phrases(record: dict, window: int, cap: int) -> list[str]:
    """Up to `cap` phrases, newest first.

    Only the title and date of each story are mapped. Description, News Topics,
    News URL and Publisher are not, which is what keeps a paragraph per story out
    of a payload `sales.outreach_draft` serialises whole into a prompt.
    """
    return [
        f"news: {label}" for label, _ in _recent_first(_news_slots(record), window)[:cap]
    ]


def _tech_phrases(record: dict, cap: int) -> list[str]:
    """One phrase, or none.

    No window, deliberately: a stack is context rather than an event, and the
    provider's verification date runs a year old without meaning the company
    stopped using the tool. One phrase rather than one per tool, so a long stack
    cannot out-vote a funding round on phrase count alone.

    `Product Names` is the provider's own joined list across every product it
    found, which is why the per-product `Product Name` leaf is not mapped -- that
    one is a single product, and on a multi-product company it would silently
    report one of several.
    """
    joined = _pluck(record, TECH_NAMES, default="")
    if not isinstance(joined, str) or _vendor_missed(joined):
        return []

    names = _dedupe([_phrase(name) for name in _split_list(joined)])
    return [f"uses {', '.join(names[:cap])}"] if names else []


def _missing_columns(record: dict) -> list[str]:
    """Clay columns the routine did not send, in declaration order.

    A column is now several flat fields rather than one key, so "did the routine
    send it" is "are any of its fields here". Absent means nobody mapped it --
    a mapping fault, and the thing worth saying out loud.

    What this can no longer tell you, and the honest cost of the flat shape: a
    column whose provider found nothing arrives with its leaves null, exactly
    like one nobody mapped. When the columns were whole objects, a miss carried
    the provider's message and was distinguishable. It is not any more. The
    remaining tell is not per-run: a mapping fault is identical on every row,
    while a vendor miss varies from company to company.
    """
    present = {_norm(key) for key in record}
    return [
        column
        for column, fields in SIGNAL_COLUMNS.items()
        if not any(_norm(field) in present for field in fields)
    ]


def _mapping_note(record: dict) -> str:
    """What to put in `clay_enrichment.reason` on a match, usually nothing.

    Terse, and never phrased as a claim about the company: `clay_enrichment`
    lands on the blackboard and `sales.outreach_draft` serialises its entire
    payload into a prompt, so "Northwind has no funding data" would be a
    fabricated fact in an email-writing context. This says what *we* failed to
    read.
    """
    missing = _missing_columns(record)
    if not missing:
        return ""
    plural = "s" if len(missing) > 1 else ""
    return f"no {', '.join(missing)} column{plural} in response"


def _buying_signals(record: dict, policy: dict) -> list[str]:
    """Raw Clay columns -> short phrases. Deterministic, and deliberately so.

    Clay's AI columns hit a token limit synthesising this inside the workspace,
    so the synthesis moved here -- but it moved into Python, not into an LLM
    call. See the module docstring for why that distinction is load-bearing.

    Order is strongest-first and matters: `outreach_draft` reads the list
    top-down looking for an opening line.
    """
    window, cap = policy["recency_days"], policy["max_per_category"]
    return _dedupe(
        [
            *_explicit_phrases(record),
            *_funding_phrases(record, window),
            *_job_phrases(record),
            *_news_phrases(record, window, cap),
            *_tech_phrases(record, cap),
        ]
    )


class ClayEnrichAgent(Agent):
    department = "revops"
    name = "clay_enrich"
    description = (
        "Look up a company in Clay in ONE call: real firmographics -- industry, "
        "employee count, revenue band, HQ region -- plus live buying signals "
        "(funding rounds, hiring, recent news, technology adoption) normalised "
        "into short phrases. Firmographics are retrieved from data providers and "
        "signals are monitored events, so no model can produce either: prefer "
        "this over 'enrichment', which only estimates firmographics and returns "
        "no signals at all. Signals are published as top-level buying_signals "
        "for 'scoring'. Takes a lead, or an account when the subject is a "
        "customer we already serve -- it keys off the domain either way. Returns "
        "clay_enrichment.matched=false when Clay has no record for the company: "
        "a normal result, and the cue to fall back to 'enrichment'. Never run "
        "this twice in a run, and never run 'enrichment' once this one matched."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "lead": {
                "type": "object",
                "description": "Raw lead as captured: name, email, company, source.",
            },
            "account": {
                "type": "object",
                "description": (
                    "An account we already serve, when there is no lead. Must "
                    "carry a domain."
                ),
            },
        },
        # Either will do, so neither is individually required; the check is in
        # execute(), where "at least one of" can actually be said -- the same
        # reason routing and crm_lookup declare no required properties.
        "required": [],
    }

    # Declared as the published output contract, not fed to a model: this agent
    # maps a vendor response, and running retrieved data back through an LLM
    # would reintroduce exactly the guessing Clay is here to remove. `required`
    # is written out rather than left to strict mode's every-property rule,
    # because on a miss only clay_enrichment is published -- and a schema that
    # never reaches a provider should say what is guaranteed, not what strict
    # mode would demand.
    output_schema = strict_schema(
        "clay_enrichment",
        {
            "firmographics": {
                "type": "object",
                "description": "Absent on a miss, deliberately -- see the module docstring.",
                "properties": {
                    "company_name": {"type": "string"},
                    "domain": {"type": "string"},
                    "industry": {"type": "string"},
                    "employee_count": {"type": "integer"},
                    "revenue_band": {"type": "string"},
                    "hq_region": {"type": "string"},
                    "free_email_domain": {"type": "boolean"},
                    "source": {"type": "string", "description": "Always 'clay'."},
                },
            },
            "buying_signals": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Short normalised phrases: 'funding: raised Series B $30M', "
                    "'12 open roles', 'news: ...', 'uses Salesforce, Segment'. "
                    "Top level, where scoring declares it. Empty means Clay "
                    "matched the company and found nothing -- unless "
                    "clay_enrichment.reason names a column, in which case the "
                    "routine did not return it and the emptiness is a mapping "
                    "fault, not a quiet company. Absent means Clay never matched "
                    "the company at all."
                ),
            },
            "signal_source": {"type": "string", "description": "Always 'clay'."},
            "clay_enrichment": {
                "type": "object",
                "properties": {
                    "matched": {"type": "boolean"},
                    "reason": {
                        "type": "string",
                        "description": (
                            "Empty on a clean match. On a match where the routine "
                            "did not send an expected signal column, names those "
                            "columns; phrases from the columns that did arrive "
                            "still publish and matched stays true."
                        ),
                    },
                },
            },
        },
        required=["clay_enrichment"],
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        subject = payload.get("lead") or payload.get("account")
        if not subject:
            raise ValueError("payload needs a lead or an account to look up")

        # Read before the vendor call, not after: a playbook missing the signals
        # block should cost nothing, rather than a Clay credit for a response
        # this agent is then unable to normalise.
        policy = signal_policy()

        domain = _domain_of(subject)
        if not domain or domain in CONSUMER_EMAIL_DOMAINS:
            # Nothing to look a company up by. A consumer address is itself the
            # answer, so report the miss rather than paying Clay for it. No
            # buying_signals key either -- we never looked, so we cannot say.
            return {
                "clay_enrichment": {
                    "matched": False,
                    "reason": (
                        "no company domain on the lead"
                        if not domain
                        else f"consumer email domain ({domain})"
                    ),
                }
            }

        client = ClayClient()
        response = client.run_routine(
            os.getenv("CLAY_ENRICH_ROUTINE_ID", ""),
            [
                {
                    "domain": domain,
                    "company": subject.get("company") or subject.get("name"),
                    "email": subject.get("email"),
                }
            ],
        )
        _bill_credits(llm, response)

        records = _records_of(response)
        record = records[0] if records else {}
        if not record or not _pluck(record, "company_name", "name", "companyName"):
            return {
                "clay_enrichment": {
                    "matched": False,
                    "reason": f"Clay returned no match for {domain}",
                }
            }

        # Aliases cover Clay's own enrichment titles alongside the names this
        # system asks for, so a function wired straight from "Enrich Company"
        # works without renaming every output column by hand.
        employees = _as_int(
            _pluck(record, "employee_count", "employees", "headcount", "size", default=0)
        )

        return {
            "firmographics": {
                "company_name": _pluck(record, "company_name", "name", "companyName", default=""),
                "domain": _pluck(record, "domain", "website", "url", default=domain),
                "industry": _pluck(record, "industry", "sector", default=""),
                "employee_count": employees,
                "revenue_band": _pluck(
                    record,
                    "revenue_band",
                    "revenue",
                    "annual_revenue",
                    "estimated_annual_revenue",
                    default="",
                ),
                "hq_region": _pluck(
                    record,
                    "hq_region",
                    "region",
                    "headquarters",
                    "hq_location",
                    "location",
                    "country",
                    default="",
                ),
                "free_email_domain": _is_consumer_email(subject),
                "source": "clay",
            },
            # An empty list here is a real answer -- "we looked at this company
            # and there is nothing" -- and the planner should not retry. That is
            # a different statement from the miss paths above, which withhold the
            # key because Clay never identified the company to look at.
            #
            # And a third, which the old aliasing could not express: we looked,
            # but the routine never sent the column. `_mapping_note` puts that in
            # `reason` rather than letting it wear the same empty list as a
            # company that is genuinely quiet.
            #
            # The raw signal columns stay in this frame and are never published:
            # every news item carries a paragraph of Description and every
            # product another, and sales.outreach_draft serialises its ENTIRE
            # payload into a prompt.
            "buying_signals": _buying_signals(record, policy),
            "signal_source": "clay",
            "clay_enrichment": {"matched": True, "reason": _mapping_note(record)},
        }


class ClaySourceAgent(Agent):
    department = "revops"
    name = "clay_source"
    description = (
        "Source net-new accounts matching an ICP brief from Clay's GTM database. "
        "Use this to start an outbound run that has no lead yet. Returns the "
        "candidate list plus the strongest candidate promoted to 'lead', so the "
        "rest of the flow proceeds normally. Requires an icp_brief; do not call "
        "this when a lead already exists."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "icp_brief": {
                "type": "string",
                "description": "Prose description of the accounts to find.",
            },
            "limit": {"type": "integer", "description": "Max accounts to return."},
        },
        "required": ["icp_brief"],
    }

    # The brief is prose and Clay Search takes structured filters, so this is the
    # one place a model belongs in the Clay path: translating intent into filters.
    # It never invents company data -- Clay supplies that.
    filter_schema = strict_schema(
        "search_filters",
        {
            "industries": {"type": "array", "items": {"type": "string"}},
            "employee_count_min": {"type": "integer"},
            "employee_count_max": {"type": "integer"},
            "regions": {"type": "array", "items": {"type": "string"}},
            "keywords": {"type": "array", "items": {"type": "string"}},
        },
    )

    output_schema = strict_schema(
        "sourced_accounts",
        {
            "sourced_accounts": {"type": "array", "items": {"type": "object"}},
            "lead": {"type": "object"},
        },
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        brief = (payload.get("icp_brief") or "").strip()
        if not brief:
            raise ValueError("payload.icp_brief is required")

        limit = int(payload.get("limit") or default_source_limit())

        translated = llm(
            brief,
            instructions=(
                "You translate a go-to-market ICP brief into structured search "
                "filters for a company database. Extract only what the brief "
                "states or clearly implies. Leave a field empty rather than "
                "inventing a constraint the brief does not support."
            ),
            schema=self.filter_schema,
        )

        client = ClayClient()
        response = client.search(translated.parsed or {}, limit)
        _bill_credits(llm, response)

        accounts = []
        for record in _records_of(response)[:limit]:
            accounts.append(
                {
                    "company": _pluck(record, "company_name", "name", "companyName", default=""),
                    "domain": _pluck(record, "domain", "website", default=""),
                    "industry": _pluck(record, "industry", "sector", default=""),
                    "employee_count": _pluck(record, "employee_count", "employees", default=None),
                    "hq_region": _pluck(record, "hq_region", "region", "country", default=""),
                }
            )

        if not accounts:
            raise VendorError(f"Clay search returned no accounts for: {brief[:120]}")

        # One orchestration qualifies one lead. The full list stays on the
        # blackboard for visibility; fanning it out into N runs is a separate
        # feature, not something to smuggle in here.
        top = accounts[0]
        return {
            "sourced_accounts": accounts,
            "lead": {
                "company": top["company"],
                "domain": top["domain"],
                "source": "clay_source",
                "note": f"sourced from ICP brief: {brief[:200]}",
            },
        }


def default_source_limit() -> int:
    """Sourcing breadth is policy, so it lives in the playbook.

    Missing keys raise rather than falling back to a literal -- a default here
    would be the duplicated constant this indirection exists to remove.
    """
    from core.config import playbook

    return int(playbook()["sourcing"]["limit"])


def signal_policy() -> dict:
    """Signal recency and phrase caps are policy, so they live in the playbook.

    Same contract as `default_source_limit`: missing keys raise. `clay_enrich`
    calls this before the vendor call, so a playbook without a `signals:` block
    fails free and fast instead of paying Clay for a response it cannot then
    normalise. The failure reaches the planner as an observation like any other.
    """
    from core.config import playbook

    block = playbook()["signals"]
    window, cap = int(block["recency_days"]), int(block["max_per_category"])
    if window < 1 or cap < 1:
        raise ValueError("signals.recency_days and signals.max_per_category must be >= 1")
    return {"recency_days": window, "max_per_category": cap}
