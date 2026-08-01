import {
  api,
  isToday,
  plural,
  shortWhen,
  usd,
  when,
  type Departments,
  type Orchestration,
} from "../api";
import { Block, Spread, useAsync } from "../components";
import { needsAttention, readStatus } from "../vocabulary";

/**
 * The zero-selection pane, and the reason this console suits a thirty-second glance:
 * health, what broke, and what it cost, answered before anything is clicked.
 *
 * Every figure is computed from the store. Where a number is all-time rather than
 * today's, it says so -- PRODUCT.md forbids implying volume that does not exist.
 */
export function Briefing({
  rows,
  depts,
  onOpen,
  onSpend,
}: {
  rows: Orchestration[];
  depts: Departments | null;
  onOpen: (id: string) => void;
  onSpend: () => void;
}) {
  const { data: costs } = useAsync(() => api.costs(), [], 10000);

  const today = rows.filter((r) => isToday(r.started_at));
  const inFlight = rows.filter((r) => r.status === "running");
  const attention = rows.filter((r) => needsAttention(r.status));
  const cleanToday = today.filter((r) => r.status === "completed");
  const spendToday = today.reduce((sum, r) => sum + (r.total_cost_usd ?? 0), 0);

  const unreachable = depts?.unreachable ?? [];
  const deptCount = depts?.departments.length ?? 0;
  const agentCount =
    depts?.departments.reduce((sum, d) => sum + d.agents.length, 0) ?? 0;

  const qualified = costs?.per_outcome.find((o) => o.outcome === "qualified");
  const totalRuns = costs?.per_outcome.reduce((sum, o) => sum + o.count, 0) ?? 0;
  const totalSpend = costs?.by_agent.reduce((sum, a) => sum + a.total_cost_usd, 0) ?? 0;

  const mostRecent = rows[0];

  return (
    <>
      <header className="pane-head">
        <h1>Today</h1>
        <p className="meta">
          {new Date().toLocaleDateString(undefined, {
            weekday: "long",
            day: "numeric",
            month: "long",
          })}
        </p>
      </header>

      {/* The two questions a glance is actually asking -- is it up, and does it want me --
          read as one band on a wide screen rather than as two scrolls of it. */}
      <Spread>
        <Block title="Health">
          {unreachable.length > 0 ? (
            <>
              <p className="answer alert">
                {unreachable.map((d) => d.name).join(" and ")}{" "}
                {unreachable.length === 1 ? "is not answering" : "are not answering"}.
              </p>
              <p className="support">
                <span>
                  The planner is still running, with the {plural(deptCount, "department")}{" "}
                  that responded — leads may skip work those agents would have done.
                </span>
              </p>
              <p className="support">
                {unreachable.map((d) => (
                  <span key={d.id} className="mono">
                    {d.endpoint}
                  </span>
                ))}
              </p>
            </>
          ) : deptCount === 0 ? (
            <>
              <p className="answer alert">No departments are answering.</p>
              <p className="support">
                <span>
                  Start the department servers before the API, or the registry comes up
                  with an empty action space and nothing can run.
                </span>
              </p>
            </>
          ) : inFlight.length > 0 ? (
            <>
              <p className="answer">
                <span className="fig">{inFlight.length}</span>{" "}
                {inFlight.length === 1 ? "lead is" : "leads are"} being worked right now.
              </p>
              <p className="support">
                <span>All {plural(deptCount, "department")} answering</span>
                <span className="sep">{plural(agentCount, "agent")} registered</span>
                <span className="sep">refreshed every 4 seconds</span>
              </p>
            </>
          ) : (
            <>
              <p className="answer">
                All {plural(deptCount, "department")} answering.{" "}
                <span className="quiet">Nothing is running.</span>
              </p>
              <p className="support">
                <span>{plural(agentCount, "agent")} registered</span>
                <span className="sep">refreshed every 4 seconds</span>
              </p>
            </>
          )}
        </Block>

        <Block title="Needs you">
          {attention.length === 0 ? (
            <>
              <p className="answer">Nothing needs you.</p>
              <p className="support">
                <span>
                  {today.length === 0
                    ? mostRecent
                      ? `No leads started today. The last one ran ${when(mostRecent.started_at)}.`
                      : "No leads have run yet."
                    : `${plural(cleanToday.length, "run")} finished cleanly today, out of ${today.length} started.`}
                </span>
              </p>
            </>
          ) : (
            <>
              <p className="answer">
                <span className="fig">{attention.length}</span>{" "}
                {attention.length === 1 ? "run needs" : "runs need"} a look.
              </p>
              <ul className="exceptions">
                {attention.map((row) => {
                  const status = readStatus(row.status);
                  return (
                    <li key={row.id}>
                      <button
                        type="button"
                        className="exception"
                        onClick={() => onOpen(row.id)}
                      >
                        <span className="who">{row.crm_reference_id}</span>
                        <span className="what">{status.gloss || status.label}</span>
                        <span className="when">{shortWhen(row.started_at)}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </Block>
      </Spread>

      <Block title="Spend">
        {today.length === 0 ? (
          <>
            <p className="answer">
              Nothing spent today. <span className="quiet">No leads started.</span>
            </p>
            <p className="support">
              {totalRuns > 0 ? (
                <>
                  <span>
                    <span className="mono">{usd(totalSpend)}</span> across{" "}
                    {plural(totalRuns, "orchestration")}, all time
                  </span>
                  <span className="sep">
                    <button type="button" className="linkish" onClick={onSpend}>
                      See the full breakdown
                    </button>
                  </span>
                </>
              ) : (
                <span>No cost recorded yet.</span>
              )}
            </p>
          </>
        ) : (
          <>
            <p className="answer">
              <span className="fig">{usd(spendToday)}</span> today, across{" "}
              {plural(today.length, "lead")}.
            </p>
            <p className="support">
              {qualified?.count ? (
                <span>
                  {usd(qualified.total_cost_usd / qualified.count)} per qualified lead,
                  all time
                </span>
              ) : (
                <span>No lead has qualified yet</span>
              )}
              {totalRuns > 0 && (
                <span className="sep">
                  {usd(totalSpend / totalRuns)} per orchestration, all time
                </span>
              )}
              <span className="sep">
                <button type="button" className="linkish" onClick={onSpend}>
                  See the full breakdown
                </button>
              </span>
            </p>
          </>
        )}
      </Block>
    </>
  );
}
