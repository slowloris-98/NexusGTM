import type { Departments } from "./api";
import type { Selection } from "./Rail";

export type Tab = {
  key: string;
  label: string;
  selection: Selection;
  /** A department whose server is not answering. It keeps its tab; see below. */
  down?: boolean;
};

/**
 * The system bar's sections. Home and the map are fixed; everything after them is
 * registry-driven, exactly as the map's ring is — a department added to
 * departments.yaml gets a tab with no edit to this file.
 *
 * Unreachable departments are merged in rather than dropped. `/departments` puts a
 * downed server in `unreachable`, not `departments`, so filtering to the reachable
 * list would make a tab vanish the moment a process stopped. Severing beats
 * deleting: the same rule the map already follows for a downed branch.
 */
export function tabsFor(depts: Departments | null): Tab[] {
  const live = (depts?.departments ?? []).map((d) => ({ id: d.id, name: d.name, down: false }));
  const down = (depts?.unreachable ?? []).map((d) => ({ id: d.id, name: d.name, down: true }));

  const all = [...live, ...down].sort((a, b) => a.id.localeCompare(b.id));

  return [
    { key: "home", label: "Home", selection: { kind: "briefing" } },
    { key: "map", label: "Live Map", selection: { kind: "map" } },
    ...all.map((d) => ({
      key: `dept:${d.id}`,
      label: d.name,
      selection: { kind: "department", id: d.id } as Selection,
      down: d.down,
    })),
  ];
}

/**
 * Which tab lights up for a given selection.
 *
 * Spend and an open orchestration both live under Home — they are reached from
 * Home's rail, and unlighting the tab the reader is standing in to light nothing
 * at all would read as having navigated away.
 */
export function activeTabKey(selection: Selection): string {
  if (selection.kind === "map") return "map";
  if (selection.kind === "department") return `dept:${selection.id}`;
  return "home";
}
