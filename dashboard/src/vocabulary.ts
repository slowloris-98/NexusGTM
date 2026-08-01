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

import type { OrchestrationDetail } from "./api";

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

/* --------------------------------------------------------- what a run was about */

/**
 * There is no goal column and no prompt. The orchestrator seeds the blackboard with
 * whatever the caller handed `Orchestrator.run` and finalizes it into `final_result`,
 * so the seed is the only record of what a run was for.
 *
 * Today that seed is always a lead, but a freemium sign-up, a target account with an
 * intent surge, or a closed-won hand-off arrive through the same slot. So this reads
 * the seed structurally and never keys off `lead`: a payload carrying `plan` and
 * `trigger` reads here exactly as well as one carrying `title` and `source`, with no
 * change to this file. Nothing is inferred -- a field that is not in the payload does
 * not appear in the line.
 */
export type Subject = {
  /** Who or what the run worked on. Never empty. */
  who: string;
  /** The agents it actually invoked, in order. Null before the first one runs. */
  work: string | null;
};

/** Where a GTM payload keeps its subject, when it wraps one rather than being one. */
const SUBJECT_KEYS = [
  "lead",
  "account",
  "deal",
  "opportunity",
  "contact",
  "signup",
  "customer",
  "company",
];

/** Identity, in priority order. First hit wins within each group. */
const WHO_KEYS = ["name", "full_name", "contact_name", "email", "id"];
const ROLE_KEYS = ["title", "role", "job_title", "plan", "tier"];
/** Standing in a product, not a job. `free` alone reads as a job title; `free plan` does not. */
const LABELLED_ROLE_KEYS = ["plan", "tier"];
const ORG_KEYS = [
  "company",
  "company_name",
  "account",
  "account_name",
  "organization",
  "domain",
];
const VIA_KEYS = ["source", "channel", "trigger", "campaign", "signal"];

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/**
 * The first of `keys` holding a scalar worth printing, with the key it came from --
 * which of them matched changes how it reads. Objects are skipped rather than
 * stringified: `account` is a name in one flow and a nested record in another, and
 * `[object Object]` is worse than saying nothing.
 */
function pickEntry(
  from: Record<string, unknown>,
  keys: string[],
): [key: string, value: string] | null {
  for (const key of keys) {
    const value = from[key];
    if (typeof value === "string" && value.trim()) return [key, value.trim()];
    if (typeof value === "number") return [key, String(value)];
  }
  return null;
}

const pick = (from: Record<string, unknown>, keys: string[]): string | null =>
  pickEntry(from, keys)?.[1] ?? null;

/**
 * `final_result` once the run finalizes, the first agent's input while it is still in
 * flight -- the planner assembles that from the same blackboard. A crash writes
 * `{"error": ...}` into `final_result`, which is not a subject.
 */
function seedOf(detail: OrchestrationDetail): Record<string, unknown> | null {
  const final = detail.final_result;
  if (isObject(final) && !("error" in final)) return final;
  const first = [...detail.runs].sort((a, b) => a.step_no - b.step_no)[0];
  return first && isObject(first.input) ? first.input : null;
}

/** The seed either wraps the subject under a known key, or is the subject itself. */
function subjectOf(seed: Record<string, unknown>): Record<string, unknown> | null {
  for (const key of SUBJECT_KEYS) {
    const nested = seed[key];
    if (isObject(nested)) return nested;
  }
  if (pick(seed, WHO_KEYS) || pick(seed, ORG_KEYS)) return seed;
  for (const value of Object.values(seed)) if (isObject(value)) return value;
  return null;
}

export function readSubject(detail: OrchestrationDetail): Subject | null {
  const seed = seedOf(detail);
  const subject = seed ? subjectOf(seed) : null;

  let who: string | null = null;
  if (subject) {
    const name = pick(subject, WHO_KEYS);
    const found = pickEntry(subject, ROLE_KEYS);
    const role = found
      ? LABELLED_ROLE_KEYS.includes(found[0])
        ? `${found[1]} ${found[0]}`
        : found[1]
      : null;
    const org =
      pick(subject, ORG_KEYS) ??
      (isObject(subject.firmographics) ? pick(subject.firmographics, ORG_KEYS) : null);
    const via = pick(subject, VIA_KEYS);

    // Lead with the name; fall back to the organisation when the payload is an account
    // rather than a person. An opportunity is often named after its account, so the
    // organisation is only appended when the head does not already carry it.
    const head = name ?? org;
    if (head) {
      const carries = org != null && head.toLowerCase().includes(org.toLowerCase());
      who =
        head +
        (role ? `, ${role}` : "") +
        (org && !carries ? ` at ${org}` : "") +
        (via ? ` · ${humanize(via).toLowerCase()}` : "");
    }
  }

  // The path is the one description always available: every run has its agents, whatever
  // the payload looked like. Distinct and in order -- a retried agent is not new work.
  const labels: string[] = [];
  for (const run of [...detail.runs].sort((a, b) => a.step_no - b.step_no)) {
    const label = readAgent(run.agent).toLowerCase();
    if (label !== "—" && !labels.includes(label)) labels.push(label);
  }
  const work = labels.length ? labels.join(", ") : null;

  if (!who && !work) return null;
  return { who: who ?? "This run", work };
}
