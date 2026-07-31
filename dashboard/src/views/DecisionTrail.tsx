import { useState } from "react";
import { api, type Decision, when } from "../api";
import { Empty, Panel, useAsync } from "../components";

export function Trail({ decisions }: { decisions: Decision[] }) {
  if (!decisions.length) return <Empty>No decisions recorded.</Empty>;
  return (
    <ol className="trail">
      {decisions.map((d, i) => (
        <li key={i} className={d.chosen_agent ? "" : "skipped"}>
          <div className="step-agent">
            {d.step_no}. {d.chosen_agent ?? "finish"}
          </div>
          <div className="step-rationale">{d.rationale}</div>
          {d.candidates_considered?.length ? (
            <div className="step-candidates">
              Considered: {d.candidates_considered.join(", ")} · {when(d.timestamp)}
            </div>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

/** Standalone tab: pick an orchestration and read the path the planner took. */
export function DecisionTrailView() {
  const { data: list } = useAsync(() => api.orchestrations(), []);
  const [id, setId] = useState<string>("");

  const chosen = id || list?.[0]?.id || "";
  const { data: detail } = useAsync(
    () => (chosen ? api.orchestration(chosen) : Promise.resolve(null)),
    [chosen],
  );

  if (!list?.length) return <Empty>No orchestrations yet. Run scripts/seed.py.</Empty>;

  return (
    <>
      <div className="controls">
        <label htmlFor="orch" style={{ fontSize: 13, color: "var(--text-secondary)" }}>
          Orchestration
        </label>
        <select id="orch" value={chosen} onChange={(e) => setId(e.target.value)}>
          {list.map((o) => (
            <option key={o.id} value={o.id}>
              {o.crm_reference_id} — {o.status}
              {o.outcome ? ` / ${o.outcome}` : ""}
            </option>
          ))}
        </select>
      </div>

      <Panel
        title="Decision trail"
        hint="The planner chooses at runtime, so the path differs per run. This is the record of why."
      >
        {detail ? <Trail decisions={detail.decisions} /> : <Empty>Loading…</Empty>}
      </Panel>
    </>
  );
}
