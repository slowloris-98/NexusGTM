import { api, plural, usd, when } from "../api";
import { Block, Chip, Skeleton, Spread, useAsync } from "../components";
import {
  readAgent,
  readOutcome,
  readRunStatus,
  readStatus,
  readSubject,
} from "../vocabulary";
import { RunGraph } from "./RunGraph";
import type { Decision, Departments } from "../api";

/**
 * One lead's whole story. The decision trail leads, ahead of the runs table:
 * PRODUCT.md holds that a decision is incomplete without its rationale.
 *
 * `depts` is threaded down rather than fetched here: /departments does live registry
 * discovery on every call, so a second poller would double that work for data App is
 * already holding.
 */
export function Orchestration({ id, depts }: { id: string; depts: Departments | null }) {
  const { data, error, loading } = useAsync(() => api.orchestration(id), [id], 4000);

  if (error)
    return (
      <>
        <header className="pane-head">
          <h1>Could not load this lead</h1>
        </header>
        <p className="fault-note">{error}</p>
        <p className="gloss">
          The control plane reads the store directly. If the API is down, everything here
          is unavailable — the orchestrations themselves are unaffected.
        </p>
      </>
    );

  if (loading && !data)
    return (
      <header className="pane-head">
        <h1>
          <Skeleton width="180px" height={20} />
        </h1>
        <p className="meta">
          <Skeleton width="240px" height={12} />
        </p>
      </header>
    );

  if (!data) return null;

  const status = readStatus(data.status);
  const outcome = readOutcome(data.outcome);
  const subject = readSubject(data);

  return (
    <>
      <header className="pane-head">
        <h1>
          <span className="ref">{data.crm_reference_id}</span>
          <Chip reading={status} />
          {outcome && <Chip reading={outcome} />}
        </h1>
        <p className="meta">
          Started {when(data.started_at)}
          {data.finished_at ? ` · finished ${when(data.finished_at)}` : " · still running"}
          {" · "}
          {plural(data.runs.length, "agent run")}
          {" · "}
          {usd(data.total_cost_usd)} in LLM cost
        </p>
        {/* What the run was about, before what became of it. Read from the payload the
            orchestrator was seeded with -- there is no goal field to print. */}
        {subject && (
          <p className="subject">
            {subject.who}
            {subject.work && <span className="work"> — {subject.work}</span>}
          </p>
        )}
        {status.gloss && <p className="gloss">{status.gloss}</p>}
      </header>

      {/* The rationale beside the shape it produced. The trail still leads -- PRODUCT.md
          holds that a decision is incomplete without it -- and the graph is the same
          route drawn, so reading one against the other is the point of the pairing. */}
      <Spread>
        <Block title="Why the planner did what it did">
          <Trail decisions={data.decisions} />
        </Block>

        {/* Keyed by id so switching leads remounts it: the relay's cursor is a position in
            one run's path, and resuming it partway through a different route is nonsense. */}
        <Block title="How this run moved">
          <RunGraph key={data.id} depts={depts} detail={data} />
        </Block>
      </Spread>

      <Spread>
        <Block title="What each agent did">
          {data.runs.length === 0 ? (
            <p className="empty">
              No agent ran. The planner stopped before invoking one.
            </p>
          ) : (
            <div className="scroll-x">
              <table>
                <thead>
                  <tr>
                    <th className="num">Step</th>
                    <th>Department</th>
                    <th>Agent</th>
                    <th>Result</th>
                    <th className="num">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {data.runs.map((run) => {
                    const runStatus = readRunStatus(run.status);
                    return (
                      <tr key={run.id}>
                        <td className="num">{run.step_no}</td>
                        <td>{run.department}</td>
                        <td>{readAgent(run.agent)}</td>
                        <td>
                          <Chip reading={runStatus} />
                        </td>
                        <td className="num">{usd(run.cost_usd)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Block>

        <Block title="What the run knew when it stopped">
          <pre className="payload">{JSON.stringify(data.final_result, null, 2)}</pre>
        </Block>
      </Spread>

      <Block title="Raw values">
        <p className="support">
          <span>
            status <span className="mono">{data.status}</span>
          </span>
          <span className="sep">
            outcome <span className="mono">{data.outcome ?? "null"}</span>
          </span>
          <span className="sep">
            id <span className="mono">{data.id}</span>
          </span>
        </p>
      </Block>
    </>
  );
}

/** The signature component: the path this run actually took, and why. */
export function Trail({ decisions }: { decisions: Decision[] }) {
  if (!decisions.length)
    return (
      <p className="empty">
        No decisions recorded. The run ended before the planner logged a choice.
      </p>
    );

  return (
    <ol className="trail">
      {decisions.map((d, i) => (
        <li key={i} className={d.chosen_agent ? "" : "terminal"}>
          <div className="step-agent">
            {d.step_no}. {d.chosen_agent ? readAgent(d.chosen_agent) : "Stopped here"}
            {d.chosen_agent?.includes("/") && (
              <span className="dept"> · {d.chosen_agent.split("/")[0]}</span>
            )}
          </div>
          <div className="step-rationale">{d.rationale}</div>
          {d.candidates_considered?.length ? (
            <div className="step-candidates">
              Also considered{" "}
              {d.candidates_considered.map((c) => readAgent(c)).join(", ")} ·{" "}
              {when(d.timestamp)}
            </div>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
