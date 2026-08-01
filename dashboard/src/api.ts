/** One agent invocation in the order the planner decided it. */
export type PathStep = {
  department: string;
  agent: string;
  status: string;
};

/** The columns of the orchestrations table, which both endpoints return verbatim. */
export type OrchestrationCore = {
  id: string;
  crm_reference_id: string;
  status: string;
  outcome: string | null;
  started_at: string;
  finished_at: string | null;
  final_result: unknown;
  total_cost_usd: number;
};

/** A row from GET /orchestrations. */
export type Orchestration = OrchestrationCore & {
  step_count: number;
  current_stage: string | null;
  /**
   * The route this orchestration actually took, ordered by step. The map derives its
   * agent-to-agent edges from these, because agents never call each other and there is
   * no declared topology to read.
   */
  agent_path: PathStep[];
};

export type Run = {
  id: string;
  step_no: number;
  department: string;
  agent: string;
  status: string;
  input: unknown;
  output: unknown;
  cost_usd: number;
  started_at: string;
  finished_at: string | null;
};

export type Decision = {
  step_no: number;
  chosen_agent: string | null;
  rationale: string;
  candidates_considered: string[] | null;
  timestamp: string;
};

/**
 * GET /orchestrations/{id}.
 *
 * Deliberately built on OrchestrationCore, not on Orchestration: `step_count`,
 * `current_stage` and `agent_path` are computed in `list_orchestrations` and the detail
 * query does not compute them. Declaring them here would make `flowEdges(detail)`
 * typecheck and silently return nothing.
 */
export type OrchestrationDetail = OrchestrationCore & {
  runs: Run[];
  decisions: Decision[];
};

/**
 * The route the detail endpoint does not send. Its `runs` are the same data -- one row per
 * invocation, carrying the same three fields -- so the path is derivable client-side with
 * no second query. Sorted here rather than trusting the SQL, so the ordering guarantee is
 * local to the thing that depends on it.
 */
export const pathFromRuns = (runs: Run[]): PathStep[] =>
  [...runs]
    .sort((a, b) => a.step_no - b.step_no)
    .map((r) => ({ department: r.department, agent: r.agent, status: r.status }));

export type Costs = {
  by_agent: {
    department: string;
    agent: string;
    total_cost_usd: number;
    orchestrations: number;
    calls: number;
  }[];
  by_department: { department: string; total_cost_usd: number; calls: number }[];
  per_outcome: {
    outcome: string;
    count: number;
    total_cost_usd: number;
    avg_cost_usd: number;
  }[];
  trend: { day: string; orchestrations: number; total_cost_usd: number }[];
};

export type Departments = {
  departments: {
    id: string;
    name: string;
    endpoint: string;
    agents: { name: string; description: string }[];
  }[];
  unreachable: { id: string; name: string; endpoint: string; error: string }[];
};

export type SearchHit = Run & {
  orchestration_id: string;
  crm_reference_id: string;
  outcome: string | null;
};

const BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json() as Promise<T>;
}

export const api = {
  orchestrations: () => get<Orchestration[]>("/orchestrations"),
  orchestration: (id: string) => get<OrchestrationDetail>(`/orchestrations/${id}`),
  costs: () => get<Costs>("/costs"),
  departments: () => get<Departments>("/departments"),
  search: (q: string) => get<SearchHit[]>(`/search?q=${encodeURIComponent(q)}`),
};

/**
 * Spend here is sub-cent. Everything below a dollar carries four places so a column
 * of costs is comparable at a glance -- mixing $0.01 and $0.0056 in one column defeats
 * the point of tabular figures. Totals above a dollar drop back to two.
 */
export const usd = (n: number | null | undefined) =>
  n == null ? "—" : `$${n !== 0 && Math.abs(n) < 1 ? n.toFixed(4) : n.toFixed(2)}`;

export const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" }) : "—";

/** Clock time for today, date for anything older. A daily glance reads "14:02". */
export const shortWhen = (iso: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  const today = new Date().toDateString() === d.toDateString();
  return today
    ? d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

/** How long ago, for things still in flight. */
export const elapsed = (iso: string | null) => {
  if (!iso) return "—";
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return `${Math.floor(secs)}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  return `${Math.floor(secs / 86400)}d`;
};

export const isToday = (iso: string | null) =>
  iso != null && new Date(iso).toDateString() === new Date().toDateString();

/** "1 run" / "3 runs" -- the console says small numbers in words often enough to matter. */
export const plural = (n: number, one: string, many = `${one}s`) =>
  `${n} ${n === 1 ? one : many}`;
