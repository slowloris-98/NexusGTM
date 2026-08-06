import { type FormEvent } from "react";
import { elapsed, plural, shortWhen, usd, type Orchestration, type SearchHit } from "./api";
import { Dot } from "./components";
import { readAgent, readOutcome, readStatus } from "./vocabulary";

export type Selection =
  | { kind: "briefing" }
  | { kind: "spend" }
  | { kind: "map" }
  | { kind: "department"; id: string }
  | { kind: "orchestration"; id: string };

/** Compares on kind, then on id when the variant carries one. Written this way
 *  rather than casting to a single id-bearing variant, now that there are two. */
export const sameSelection = (a: Selection, b: Selection) =>
  a.kind === b.kind &&
  (!("id" in a) || !("id" in b) || a.id === b.id);

function Row({
  row,
  selected,
  onSelect,
}: {
  row: Orchestration;
  selected: boolean;
  onSelect: () => void;
}) {
  const status = readStatus(row.status);
  const outcome = readOutcome(row.outcome);
  const running = row.status === "running";

  return (
    <button
      type="button"
      className="rail-row"
      aria-current={selected}
      onClick={onSelect}
    >
      <span className="headline">
        <span className="who">{row.crm_reference_id}</span>
        <span className="cost">{usd(row.total_cost_usd)}</span>
      </span>
      <span className="sub">
        <Dot tone={status.tone} />
        <span className="state">
          {status.label}
          {outcome && !running ? ` · ${outcome.label}` : ""}
        </span>
        <span className="when">
          {running ? elapsed(row.started_at) : shortWhen(row.started_at)}
        </span>
      </span>
    </button>
  );
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <div className="rail-section">
      <h2>
        <span>{title}</span>
        {count != null && <span className="count">{count}</span>}
      </h2>
      {children}
    </div>
  );
}

export function Rail({
  rows,
  term,
  onTerm,
  onSearch,
  searchHits,
  searchError,
  selection,
  onSelect,
  spendToday,
  attentionCount,
}: {
  rows: Orchestration[];
  term: string;
  onTerm: (value: string) => void;
  onSearch: (query: string) => void;
  searchHits: SearchHit[] | null;
  searchError: string | null;
  selection: Selection;
  onSelect: (next: Selection) => void;
  spendToday: number | null;
  attentionCount: number;
}) {
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSearch(term.trim());
  };

  const inFlight = rows.filter((r) => r.status === "running");
  const rest = rows.filter((r) => r.status !== "running");

  const isOpen = (id: string) =>
    selection.kind === "orchestration" && selection.id === id;

  return (
    <aside className="rail" aria-label="Orchestrations">
      <form className="rail-search" onSubmit={submit} role="search">
        <label className="visually-hidden" htmlFor="rail-filter">
          Filter by account, or press Enter to search every past run
        </label>
        <input
          id="rail-filter"
          type="search"
          value={term}
          placeholder="Filter accounts — Enter to search runs"
          onChange={(e) => {
            onTerm(e.target.value);
            if (e.target.value === "") onSearch("");
          }}
        />
      </form>

      <div className="rail-scroll">
        {searchHits !== null || searchError ? (
          <Section
            title="Search results"
            count={searchError ? undefined : searchHits?.length}
          >
            {searchError ? (
              <p className="rail-empty fault-note">Search failed: {searchError}</p>
            ) : searchHits && searchHits.length > 0 ? (
              searchHits.map((hit) => (
                <button
                  key={hit.id}
                  type="button"
                  className="rail-row"
                  aria-current={isOpen(hit.orchestration_id)}
                  onClick={() =>
                    onSelect({ kind: "orchestration", id: hit.orchestration_id })
                  }
                >
                  <span className="headline">
                    <span className="who">{hit.crm_reference_id}</span>
                    <span className="cost">{usd(hit.cost_usd)}</span>
                  </span>
                  <span className="sub">
                    <span className="state">
                      {readAgent(hit.agent)} · {hit.department}
                    </span>
                    <span className="when">{shortWhen(hit.started_at)}</span>
                  </span>
                </button>
              ))
            ) : (
              <p className="rail-empty">
                Nothing in any past run matches that. The search reads every agent input
                and output — company names, industries, and the planner's reasons.
              </p>
            )}
          </Section>
        ) : (
          <>
            <Section title="Overview">
              <button
                type="button"
                className="rail-row pinned"
                aria-current={selection.kind === "briefing"}
                onClick={() => onSelect({ kind: "briefing" })}
              >
                Today
                {attentionCount > 0 && (
                  <span className="trailing">
                    {plural(attentionCount, "needs you", "need you")}
                  </span>
                )}
              </button>
              {/* Map moved to the system bar, where it is one of the whole-screen
                  tabs. A rail row for it would be a second door to the same room. */}
              <button
                type="button"
                className="rail-row pinned"
                aria-current={selection.kind === "spend"}
                onClick={() => onSelect({ kind: "spend" })}
              >
                Spend
                {spendToday != null && (
                  <span className="trailing">{usd(spendToday)}</span>
                )}
              </button>
            </Section>

            {inFlight.length > 0 && (
              <Section title="In flight" count={inFlight.length}>
                {inFlight.map((row) => (
                  <Row
                    key={row.id}
                    row={row}
                    selected={isOpen(row.id)}
                    onSelect={() => onSelect({ kind: "orchestration", id: row.id })}
                  />
                ))}
              </Section>
            )}

            <Section title={term ? "Matching" : "Recent"} count={rest.length}>
              {rest.length === 0 ? (
                <p className="rail-empty">
                  {term
                    ? "No account matches that. Press Enter to search inside the runs instead."
                    : "No orchestrations yet. Run scripts/seed.py to put three through."}
                </p>
              ) : (
                rest.map((row) => (
                  <Row
                    key={row.id}
                    row={row}
                    selected={isOpen(row.id)}
                    onSelect={() => onSelect({ kind: "orchestration", id: row.id })}
                  />
                ))
              )}
            </Section>
          </>
        )}
      </div>
    </aside>
  );
}
