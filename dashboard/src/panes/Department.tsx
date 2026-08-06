import { useState } from "react";
import {
  api,
  plural,
  shortWhen,
  usd,
  type Departments,
  type Flow,
  type Flows,
  type LaunchBody,
  type Orchestration,
} from "../api";
import { Block, Dot } from "../components";
import { readAgent, readOutcome, readStatus } from "../vocabulary";

/**
 * One department's console: what it can start, what it can do, and what it has
 * lately been part of.
 *
 * Generic on purpose — it never names a department. Its tab comes from
 * `/departments`, its buttons from `/flows`, its agents from `/departments`, and
 * its runs from `/orchestrations`. A new department is a server plus a line of
 * config; a new trigger is a line of playbook. Neither is an edit to this file.
 */
export function Department({
  id,
  depts,
  flows,
  rows,
  onOpen,
}: {
  id: string;
  depts: Departments | null;
  flows: Flows | null;
  rows: Orchestration[];
  onOpen: (orchestrationId: string) => void;
}) {
  const found = depts?.departments.find((d) => d.id === id);
  const down = depts?.unreachable.find((d) => d.id === id);
  const name = found?.name ?? down?.name ?? id;

  const triggers = (flows?.flows ?? []).filter((f) => f.trigger?.department === id);
  const agents = found?.agents ?? [];
  const recent = rows.filter((r) => r.agent_path.some((s) => s.department === id));

  return (
    <>
      <header className="pane-head">
        <h1>{name}</h1>
        <p className="meta">
          {down ? (
            <>Not answering on <span className="mono">{down.endpoint}</span></>
          ) : (
            <>
              {plural(agents.length, "agent")} · {plural(triggers.length, "trigger")}
            </>
          )}
        </p>
      </header>

      <Block title="Start a run">
        {triggers.length === 0 ? (
          <p className="gloss">
            No flow in the playbook declares a trigger for this department. Triggers are
            declared per flow in <span className="mono">config/playbook.yaml</span>.
          </p>
        ) : (
          <div className="triggers">
            {triggers.map((flow) => (
              <TriggerForm key={flow.id} flow={flow} onOpen={onOpen} />
            ))}
          </div>
        )}
      </Block>

      <Block title="Agents">
        {agents.length === 0 ? (
          <p className="gloss">
            {down
              ? "The server is not answering, so its agents cannot be listed. Past runs still show what it did."
              : "This department registers no agents."}
          </p>
        ) : (
          <ul className="plain-list">
            {agents.map((agent) => (
              <li key={agent.name}>
                <span className="who">{readAgent(agent.name)}</span>
                <span className="gloss">{agent.description}</span>
              </li>
            ))}
          </ul>
        )}
      </Block>

      <Block title="Recent runs">
        {recent.length === 0 ? (
          <p className="gloss">Nothing has come through this department yet.</p>
        ) : (
          <ul className="plain-list">
            {recent.slice(0, 12).map((row) => {
              const status = readStatus(row.status);
              const outcome = readOutcome(row.outcome);
              return (
                <li key={row.id}>
                  <button type="button" className="linkish" onClick={() => onOpen(row.id)}>
                    <span className="who">{row.crm_reference_id}</span>
                    <span className="sub">
                      <Dot tone={status.tone} />
                      {status.label}
                      {outcome && row.status !== "running" ? ` · ${outcome.label}` : ""}
                      {" · "}
                      {usd(row.total_cost_usd)}
                      {" · "}
                      {shortWhen(row.started_at)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Block>
    </>
  );
}

/**
 * One trigger, and whatever it needs before it can start.
 *
 * The POST blocks for the whole orchestration — six or so sequential LLM calls —
 * so the button says what it is doing rather than spinning. On success the reader
 * lands on the finished run, which is the only thing they wanted from pressing it.
 */
function TriggerForm({
  flow,
  onOpen,
}: {
  flow: Flow;
  onOpen: (orchestrationId: string) => void;
}) {
  const trigger = flow.trigger!;
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [fault, setFault] = useState<string | null>(null);

  const kind = trigger.input.kind;
  const ready =
    kind === "none" ||
    (kind === "brief" && text.trim() !== "") ||
    (kind === "account" && text.trim() !== "") ||
    (kind === "lead" && email.trim() !== "");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFault(null);

    const body: LaunchBody = { flow: flow.id };
    if (kind === "lead") {
      body.lead = { name, email, company, source: "console" };
      if (company) body.crm_reference_id = company;
    } else if (kind === "brief") {
      body.brief = text.trim();
    } else if (kind === "account") {
      // The domain is what keys the CRM record, so it travels as the account and
      // as the run's reference rather than being re-typed into both.
      const domain = text.trim();
      body.seed = { account: { name: domain, domain } };
      body.crm_reference_id = domain;
    }

    try {
      const result = await api.launch(body);
      onOpen(result.orchestration_id);
    } catch (e) {
      setFault(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="trigger" onSubmit={submit}>
      <div className="trigger-head">
        <span className="who">{trigger.label}</span>
        <span className="gloss">{trigger.description}</span>
      </div>

      {kind === "lead" && (
        <div className="trigger-fields">
          <input
            type="text"
            value={name}
            placeholder="Name"
            aria-label="Lead name"
            onChange={(e) => setName(e.target.value)}
          />
          <input
            type="email"
            value={email}
            placeholder="Work email"
            aria-label="Lead email"
            required
            onChange={(e) => setEmail(e.target.value)}
          />
          <input
            type="text"
            value={company}
            placeholder="Company"
            aria-label="Lead company"
            onChange={(e) => setCompany(e.target.value)}
          />
        </div>
      )}

      {kind === "brief" && (
        <div className="trigger-fields">
          <textarea
            value={text}
            rows={3}
            placeholder="Who should we go after? Industry, size, region, signal."
            aria-label="ICP brief"
            onChange={(e) => setText(e.target.value)}
          />
        </div>
      )}

      {kind === "account" && (
        <div className="trigger-fields">
          <input
            type="text"
            value={text}
            placeholder="Account domain — acme.com"
            aria-label="Account domain"
            onChange={(e) => setText(e.target.value)}
          />
        </div>
      )}

      <div className="trigger-foot">
        <button type="submit" className="action" disabled={busy || !ready} aria-busy={busy}>
          {busy ? "Running…" : "Start"}
        </button>
        <span className="gloss" aria-live="polite">
          {busy
            ? `Running ${flow.label}. The console opens the run when it finishes.`
            : flow.when}
        </span>
      </div>

      {fault && <p className="fault-note">{fault}</p>}
    </form>
  );
}
