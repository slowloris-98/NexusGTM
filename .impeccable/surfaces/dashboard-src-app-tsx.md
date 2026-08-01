---
version: 1
slug: "dashboard-src-app-tsx"
primary_target: "dashboard/src/App.tsx"
related_targets: ["dashboard/src/styles.css","dashboard/src/components.tsx"]
---

# Surface: NexusGTM control plane (single console screen)

**Scope.** The whole dashboard: `dashboard/src/**`. One screen replaces the five-tab
structure. Frontend only — the five existing API endpoints are used exactly as they are.

**Visitor mode.** Operate.

**Audience and job.** GTM leadership (see PRODUCT.md). They do not operate the system.
The confirmed usage scene is a **standing daily glance**: roughly thirty seconds, once a
day, answering three questions — is the pipeline healthy, did anything break, what did it
cost. Sustained investigation, weekly cost review, and curiosity reading were explicitly
not selected as scenes; they are supported but never optimized for.

**Task.** Land on the page and have all three questions answered before touching anything.
Click one row to read why the planner chose what it did.

**Content and constraints.**
- Endpoints: `/orchestrations`, `/orchestrations/{id}`, `/costs`, `/departments`,
  `/search?q=`. No API changes.
- Poll cadences are fixed by the existing implementation: orchestrations 4s, costs 10s,
  registry 15s.
- Taxonomy comes from `/departments`; the three current departments must never be
  hardcoded into layout or color.
- Honest at three seeded rows and at three hundred real ones.
- No fabricated figures. Every number traces to the store.

**Chosen direction.** The Split Console — persistent left rail of every orchestration,
right pane holding whatever is selected. Assigned by the surface roll (seed `3d223551`),
seventh of seven grounded structures ordered by resonance.

**The memorable moment.** The briefing zero-state. Master-detail normally lands on an
empty pane; here the unselected pane *is* the daily answer, so the structure built for
investigation also serves a thirty-second glance. If that zero-state is weak, the whole
structure reverts to a console for a job nobody does — it is the load-bearing element.

**Surface-local decisions.**
- Search has no tab: typing filters the rail locally; Enter runs the deep `/search` across
  run inputs and outputs and puts the hits in the rail as a distinct mode.
- Cost analytics have no tab: a pinned "Spend" row in the rail opens them in the pane.
- The decision trail has no tab: it is the first section of an orchestration's detail,
  ahead of the runs table, because a decision without its rationale is incomplete.
- Engineering vocabulary is translated at the surface (halt codes and agent identifiers
  become outcome language) with the raw value preserved in the detail pane, never dropped.

**Unresolved.**
- Elevation remains open per DESIGN.md; this build stays flat with hairlines and a tonal
  rail, and does not establish a shadow vocabulary.
- The `6 / 7 / 8 / 10` radius ladder stays as documented. Consolidating it is a durable
  system change and was not authorized here.
