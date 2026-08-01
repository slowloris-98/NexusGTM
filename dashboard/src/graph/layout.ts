/**
 * Radial layout for the agent map. Pure functions -- no React, no DOM, no fetching --
 * so the geometry can be reasoned about and checked on its own.
 *
 * Two rules drive everything here:
 *
 * 1. Positions are a function of the data alone, so the graph never reshuffles between
 *    polls. A constellation that jumps every 4 seconds is unreadable.
 * 2. Nothing is hardcoded per department. A fourth department entering departments.yaml
 *    must take its place in the ring, and get its colour, with no edit to this file.
 */

import type { Departments, Orchestration, PathStep } from "../api";

export const SIZE = 1000;
const CENTRE = SIZE / 2;

const R_DEPT = 210;
const R_AGENT = 312;
const R_LABEL = 392;

/**
 * Node radii, in viewBox units. The canvas typically renders around 640px against a 1000
 * unit viewBox, so everything here scales by roughly 0.64 on screen: an agent reads about
 * 19px across, a hub about 28px.
 *
 * These live here rather than in the component because a node's size is geometry, decided
 * alongside its position -- the agent radius depends on how much room its neighbours left.
 */
export const NODE = {
  core: 18,
  coreRing: 28,
  coreHalo: 120,
  hub: 22,
  hubRing: 32,
  agent: 15,
  agentPulse: 34,
  /** Clear space between an agent node's edge and the top of its label. */
  labelGap: 16,
} as const;

/**
 * Half-width of the arc a department's agents fan across.
 *
 * Capped against the department count: at a fixed 22°, neighbouring departments' fans
 * would start overlapping each other around eight departments. Unchanged at today's three.
 */
const fanFor = (departments: number) =>
  Math.min((22 * Math.PI) / 180, ((2 * Math.PI) / Math.max(1, departments)) * 0.35);

export type NodeKind = "orchestrator" | "department" | "agent";

export type GraphNode = {
  key: string;
  kind: NodeKind;
  label: string;
  x: number;
  y: number;
  /** Radius in viewBox units. Agent radii shrink when a department is crowded. */
  r: number;
  /** Label anchor point, departments only. */
  labelX: number;
  labelY: number;
  labelAnchor: "start" | "middle" | "end";
  hue: number | null;
  department: string | null;
  agent: string | null;
  description: string;
  /** False when the department's server did not answer the last discovery. */
  reachable: boolean;
  /**
   * True when we only know this agent existed because past runs used it -- its
   * department is unreachable right now, so the registry cannot confirm it.
   */
  fromHistory: boolean;
};

export type StructuralEdge = {
  id: string;
  /** trunk = orchestrator to department, branch = department to agent. They carry
      different visual weight, and parsing the id to tell them apart is not a contract. */
  kind: "trunk" | "branch";
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  hue: number | null;
  reachable: boolean;
};

export type FlowEdge = {
  id: string;
  from: string;
  to: string;
  /** SVG path: a straight line, sender to receiver. */
  d: string;
  /** The same two points, so a travelling dot does not have to re-parse `d`. */
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  /** Distance in viewBox units, so the dot can move at a constant speed. */
  length: number;
  hue: number;
  count: number;
  /** Handoffs whose target agent rejected the payload and forced a re-plan. */
  rejected: number;
  /** 0..1 against the busiest edge, for stroke weight and opacity. */
  weight: number;
  fromLabel: string;
  toLabel: string;
};

export type Graph = {
  nodes: GraphNode[];
  byKey: Map<string, GraphNode>;
  structural: StructuralEdge[];
  departments: GraphNode[];
};

export const agentKey = (department: string, agent: string) => `agent:${department}/${agent}`;
export const deptKey = (department: string) => `dept:${department}`;
export const ORCHESTRATOR = "orchestrator";

/**
 * Evenly spaced hues, assigned by position in the id-sorted department list.
 *
 * Deliberately not a hash of the id. A hash buys colour stability when a department is
 * inserted, but the ring redistributes on insert anyway -- every hub moves -- so there was
 * never stability to protect, and a hash can hand two departments near-identical hues.
 * Even spacing is guaranteed to separate, which is what actually matters on screen.
 */
export function departmentHues(ids: string[]): Map<string, number> {
  const sorted = [...ids].sort();
  const step = 360 / Math.max(1, sorted.length);
  const hues = new Map<string, number>();
  sorted.forEach((id, i) => hues.set(id, (210 + i * step) % 360));
  return hues;
}

/** Agents each department has been seen using, even if its server is down right now. */
function historicalAgents(orchestrations: Orchestration[]): Map<string, Set<string>> {
  const seen = new Map<string, Set<string>>();
  for (const o of orchestrations) {
    for (const step of o.agent_path ?? []) {
      if (!seen.has(step.department)) seen.set(step.department, new Set());
      seen.get(step.department)!.add(step.agent);
    }
  }
  return seen;
}

export function buildGraph(
  depts: Departments | null,
  orchestrations: Orchestration[],
): Graph {
  const live = depts?.departments ?? [];
  const down = depts?.unreachable ?? [];
  const history = historicalAgents(orchestrations);

  // An unreachable department reports no agents at all -- discovery could not ask it.
  // Rather than let its whole branch vanish, fall back to the agents past runs used, so
  // stopping a server severs a branch instead of deleting it.
  const entries = [
    ...live.map((d) => ({
      id: d.id,
      name: d.name,
      reachable: true,
      agents: d.agents.map((a) => ({ name: a.name, description: a.description, fromHistory: false })),
    })),
    ...down.map((d) => ({
      id: d.id,
      name: d.name,
      reachable: false,
      agents: [...(history.get(d.id) ?? [])].sort().map((name) => ({
        name,
        description: "This department's server did not answer. Last known from earlier runs.",
        fromHistory: true,
      })),
    })),
  ].sort((a, b) => a.id.localeCompare(b.id));

  const hues = departmentHues(entries.map((e) => e.id));

  const nodes: GraphNode[] = [
    {
      key: ORCHESTRATOR,
      kind: "orchestrator",
      label: "Orchestrator",
      x: CENTRE,
      y: CENTRE,
      r: NODE.core,
      labelX: CENTRE,
      labelY: CENTRE,
      labelAnchor: "middle",
      hue: null,
      department: null,
      agent: null,
      description:
        "Plans every step and picks the next agent at runtime. Metered as orchestrator/planner.",
      reachable: true,
      fromHistory: false,
    },
  ];
  const structural: StructuralEdge[] = [];
  const departmentNodes: GraphNode[] = [];

  const N = entries.length;
  const FAN = fanFor(N);
  entries.forEach((dept, i) => {
    const theta = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(1, N);
    const hue = hues.get(dept.id) ?? 210;

    const dx = CENTRE + R_DEPT * Math.cos(theta);
    const dy = CENTRE + R_DEPT * Math.sin(theta);
    const lx = CENTRE + R_LABEL * Math.cos(theta);
    const ly = CENTRE + R_LABEL * Math.sin(theta);

    // Flip the anchor by hemisphere so a label never runs back across the graph.
    const cos = Math.cos(theta);
    const anchor: GraphNode["labelAnchor"] =
      Math.abs(cos) < 0.25 ? "middle" : cos > 0 ? "start" : "end";

    const node: GraphNode = {
      key: deptKey(dept.id),
      kind: "department",
      label: dept.name,
      x: dx,
      y: dy,
      r: NODE.hub,
      labelX: lx,
      labelY: ly,
      labelAnchor: anchor,
      hue,
      department: dept.id,
      agent: null,
      description: dept.reachable
        ? `${dept.agents.length} agent${dept.agents.length === 1 ? "" : "s"} registered.`
        : "Not answering. The planner is running without it.",
      reachable: dept.reachable,
      fromHistory: false,
    };
    nodes.push(node);
    departmentNodes.push(node);

    structural.push({
      id: `s:${ORCHESTRATOR}->${node.key}`,
      kind: "trunk",
      x1: CENTRE,
      y1: CENTRE,
      x2: dx,
      y2: dy,
      hue,
      reachable: dept.reachable,
    });

    const M = dept.agents.length;

    // Cap the agent radius against the room its neighbours leave, so a crowded department
    // degrades gracefully instead of overlapping. Not reached at one or two agents.
    const spacing = M > 1 ? 2 * R_AGENT * Math.sin(FAN / (M - 1)) : Infinity;
    const agentR = Math.min(NODE.agent, spacing * 0.33);

    dept.agents.forEach((agent, j) => {
      const offset = M <= 1 ? 0 : -FAN + (2 * FAN * j) / (M - 1);
      const at = theta + offset;
      const ax = CENTRE + R_AGENT * Math.cos(at);
      const ay = CENTRE + R_AGENT * Math.sin(at);

      const agentNode: GraphNode = {
        key: agentKey(dept.id, agent.name),
        kind: "agent",
        label: agent.name.replace(/[_-]+/g, " "),
        x: ax,
        y: ay,
        r: agentR,
        labelX: ax,
        labelY: ay,
        labelAnchor: "middle",
        hue,
        department: dept.id,
        agent: agent.name,
        description: agent.description,
        reachable: dept.reachable,
        fromHistory: agent.fromHistory,
      };
      nodes.push(agentNode);

      structural.push({
        id: `s:${node.key}->${agentNode.key}`,
        kind: "branch",
        x1: dx,
        y1: dy,
        x2: ax,
        y2: ay,
        hue,
        reachable: dept.reachable,
      });
    });
  });

  return {
    nodes,
    byKey: new Map(nodes.map((n) => [n.key, n])),
    structural,
    departments: departmentNodes,
  };
}

/**
 * The handoffs the planner actually decided, tallied across the orchestrations the API
 * returned. Agents never call each other, so there is no declared topology to read -- an
 * edge exists because that transition happened.
 */
export function flowEdges(graph: Graph, orchestrations: Orchestration[]): FlowEdge[] {
  const tally = new Map<string, { from: string; to: string; count: number; rejected: number }>();

  for (const o of orchestrations) {
    const path: PathStep[] = o.agent_path ?? [];
    for (let i = 0; i < path.length - 1; i++) {
      const a = path[i];
      const b = path[i + 1];
      const from = agentKey(a.department, a.agent);
      const to = agentKey(b.department, b.agent);
      if (from === to) continue;
      const id = `${from}->${to}`;
      const entry = tally.get(id) ?? { from, to, count: 0, rejected: 0 };
      entry.count += 1;
      // The planner picked this agent and its schema refused, forcing a re-plan.
      if (b.status === "invalid_input") entry.rejected += 1;
      tally.set(id, entry);
    }
  }

  const busiest = Math.max(1, ...[...tally.values()].map((e) => e.count));

  const edges: FlowEdge[] = [];
  for (const [id, entry] of tally) {
    const a = graph.byKey.get(entry.from);
    const b = graph.byKey.get(entry.to);
    // An agent that no longer exists anywhere in the registry or in any reachable
    // department has no node to attach to. Nothing to draw.
    if (!a || !b) continue;

    // Straight, centre to centre. A handoff between two diametrically opposite agents
    // therefore runs through the middle -- which is fine, because the flow group paints
    // before the core group and the orchestrator's halo covers it.
    edges.push({
      id,
      from: entry.from,
      to: entry.to,
      d: `M ${a.x.toFixed(1)} ${a.y.toFixed(1)} L ${b.x.toFixed(1)} ${b.y.toFixed(1)}`,
      x1: a.x,
      y1: a.y,
      x2: b.x,
      y2: b.y,
      length: Math.hypot(b.x - a.x, b.y - a.y),
      hue: a.hue ?? 210,
      count: entry.count,
      rejected: entry.rejected,
      weight: entry.count / busiest,
      fromLabel: a.label,
      toLabel: b.label,
    });
  }

  return edges.sort((x, y) => x.count - y.count);
}

/**
 * Which nodes are working right now.
 *
 * The last step of a running orchestration's path is its most recent run, and it carries
 * its department, so it maps to exactly one node -- no ambiguity even if two departments
 * expose the same bare agent name. An empty path means no agent has been invoked yet and
 * the planner is still choosing, which is the centre's own live state.
 */
export function liveKeys(orchestrations: Orchestration[]): {
  nodes: Set<string>;
  planning: boolean;
} {
  const nodes = new Set<string>();
  let planning = false;

  for (const o of orchestrations) {
    if (o.status !== "running") continue;
    const path = o.agent_path ?? [];
    if (path.length === 0) {
      planning = true;
      continue;
    }
    const last = path[path.length - 1];
    nodes.add(agentKey(last.department, last.agent));
  }

  return { nodes, planning };
}

/** Deterministic starfield, seeded so it never reshuffles on re-render. */
export function starfield(count: number, seed = 9): { x: number; y: number; r: number; o: number }[] {
  let s = seed;
  const rand = () => {
    s = (s * 1664525 + 1013904223) % 4294967296;
    return s / 4294967296;
  };
  return Array.from({ length: count }, () => ({
    x: rand() * SIZE,
    y: rand() * SIZE,
    r: 0.6 + rand() * 1.1,
    o: 0.15 + rand() * 0.5,
  }));
}
