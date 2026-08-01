/**
 * Engineering vocabulary -> the language leadership actually uses.
 *
 * PRODUCT.md: "Translate agent names, halt codes, and payloads into funnel outcomes
 * without softening what actually happened. A halted run is not a successful one."
 *
 * Every mapping keeps the raw value reachable -- the detail pane prints it verbatim.
 * Nothing here invents a state the orchestrator cannot produce; the literals below
 * come from core/orchestrator.py.
 */

export type Tone = "good" | "attention" | "fault" | "neutral" | "live";

export type Reading = {
  /** What a GTM lead reads. */
  label: string;
  /** One sentence of what it means, for the detail pane. */
  gloss: string;
  tone: Tone;
};

/** Orchestration status. Literals: core/orchestrator.py. */
const STATUS: Record<string, Reading> = {
  running: {
    label: "Running",
    gloss: "Still working through the lead.",
    tone: "live",
  },
  completed: {
    label: "Completed",
    gloss: "Ran to a conclusion the planner was satisfied with.",
    tone: "good",
  },
  no_agent: {
    label: "No next step",
    gloss: "The planner could not find an agent that fit the work remaining.",
    tone: "attention",
  },
  failed: {
    label: "Failed",
    gloss: "An agent kept erroring and the run was abandoned.",
    tone: "fault",
  },
  halted_budget: {
    label: "Stopped · over budget",
    gloss: "Spend passed the ceiling mid-run, so the run was cut off.",
    tone: "attention",
  },
  halted_steps: {
    label: "Stopped · step limit",
    gloss: "The run used its whole step allowance without finishing.",
    tone: "attention",
  },
  halted_loop: {
    label: "Stopped · repeating",
    gloss: "The same agent was picked twice with the same input, so the run was cut off.",
    tone: "attention",
  },
};

/** Orchestration outcome. Disqualified is a correct ending, not a problem. */
const OUTCOME: Record<string, Reading> = {
  qualified: {
    label: "Qualified",
    gloss: "Scored above the routing threshold, assigned a rep, and drafted a first touch.",
    tone: "good",
  },
  disqualified: {
    label: "Disqualified",
    gloss: "Judged not a fit and terminated early, with the reason recorded.",
    tone: "neutral",
  },
  failed: {
    label: "Failed",
    gloss: "Ended without a verdict on the lead.",
    tone: "fault",
  },
  /** The store groups outcome-less orchestrations under this literal. */
  unknown: {
    label: "No verdict",
    gloss: "The run stopped before it reached a judgement on the lead.",
    tone: "neutral",
  },
};

/** Per-agent run status. Literals: core/orchestrator.py. */
const RUN_STATUS: Record<string, Reading> = {
  ok: { label: "OK", gloss: "Returned what the planner expected.", tone: "good" },
  invalid_input: {
    label: "Bad handoff",
    gloss: "The payload did not match this agent's schema, so the planner chose again.",
    tone: "attention",
  },
  error: { label: "Error", gloss: "The agent raised.", tone: "fault" },
};

function unknown(raw: string): Reading {
  return { label: humanize(raw), gloss: "", tone: "neutral" };
}

export function readStatus(raw: string | null): Reading {
  if (!raw) return { label: "Unknown", gloss: "", tone: "neutral" };
  return STATUS[raw] ?? unknown(raw);
}

export function readOutcome(raw: string | null): Reading | null {
  if (!raw) return null;
  return OUTCOME[raw] ?? unknown(raw);
}

export function readRunStatus(raw: string | null): Reading {
  if (!raw) return { label: "Unknown", gloss: "", tone: "neutral" };
  return RUN_STATUS[raw] ?? unknown(raw);
}

/** Statuses that mean a person should look. Disqualified and completed do not. */
const NEEDS_ATTENTION = new Set([
  "halted_budget",
  "halted_steps",
  "halted_loop",
  "no_agent",
  "failed",
]);

export const needsAttention = (status: string | null) =>
  status != null && NEEDS_ATTENTION.has(status);

/** `outreach_draft` -> `Outreach draft`. Used for agent and stage identifiers. */
export function humanize(raw: string): string {
  const spaced = raw.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * Agent identifiers arrive either bare (`enrichment`) or qualified
 * (`marketing/enrichment`). The department is shown separately, so drop it here.
 */
export function readAgent(raw: string | null): string {
  if (!raw) return "—";
  const bare = raw.includes("/") ? raw.slice(raw.lastIndexOf("/") + 1) : raw;
  return humanize(bare);
}
