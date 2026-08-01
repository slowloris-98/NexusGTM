---
name: NexusGTM Control Plane
description: A calibrated readout for agent orchestrations — quiet chassis, one accent, and numbers that carry all the weight.
colors:
  signal-blue: "#2470cc"
  series-orange: "#eb6834"
  series-green: "#178f64"
  status-good: "#0a7c0a"
  status-warning: "#96650b"
  status-serious: "#b4471a"
  status-critical: "#d03b3b"
  page: "#f9f9f7"
  surface: "#fcfcfb"
  text-primary: "#0b0b0b"
  text-secondary: "#52514e"
  text-muted: "#6e6c66"
  rule-grid: "#e1e0d9"
  rule-baseline: "#c3c2b7"
  border-hairline: "rgba(11, 11, 11, 0.1)"
  # Scoped to the system map pane only. See ## Scoped Exceptions.
  map-ground-lift: "oklch(19% 0.02 265)"
  map-ground: "oklch(15% 0.014 265)"
  map-ground-deep: "oklch(10% 0.012 265)"
  map-core: "oklch(96% 0.02 250)"
  map-core-ring: "oklch(88% 0.04 250)"
  map-label: "oklch(93% 0.008 250)"
  map-label-agent: "oklch(85% 0.008 250)"
  map-label-dim: "oklch(74% 0.008 250)"
  map-dormant-text: "oklch(66% 0.008 265)"
  map-dormant: "oklch(52% 0.008 265)"
  map-star: "oklch(99% 0 0)"
  map-star-alt: "oklch(88% 0.03 250)"
typography:
  display:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "27px"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.02em"
    fontFeature: "tabular-nums"
  headline:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "20px"
    fontWeight: 650
    lineHeight: 1.5
    letterSpacing: "-0.01em"
  answer:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "20px"
    fontWeight: 400
    lineHeight: 1.35
    letterSpacing: "-0.01em"
  title:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "0.045em"
  body:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  caption:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "0.05em"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
rounded:
  sm: "6px"
  md: "7px"
  full: "999px"
spacing:
  2xs: "4px"
  xs: "6px"
  sm: "8px"
  md: "12px"
  lg: "14px"
  xl: "18px"
  2xl: "22px"
  3xl: "26px"
  4xl: "72px"
components:
  rail-row:
    textColor: "{colors.text-secondary}"
    typography: "{typography.body}"
    padding: "8px 14px"
  rail-row-selected:
    backgroundColor: "{colors.signal-blue}"
    textColor: "{colors.text-primary}"
    typography: "{typography.body}"
    padding: "8px 14px"
  chip-good:
    textColor: "{colors.status-good}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "2px 8px"
  chip-attention:
    textColor: "{colors.status-serious}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "2px 8px"
  chip-fault:
    textColor: "{colors.status-critical}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "2px 8px"
  chip-neutral:
    textColor: "{colors.text-secondary}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "2px 8px"
  chip-live:
    textColor: "{colors.signal-blue}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "2px 8px"
  input-search:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text-primary}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "7px 10px"
  button-text:
    textColor: "{colors.signal-blue}"
    typography: "{typography.caption}"
    padding: "0"
  exception-row:
    textColor: "{colors.text-secondary}"
    typography: "{typography.body}"
    padding: "11px 8px 11px 0"
  payload-well:
    backgroundColor: "{colors.page}"
    textColor: "{colors.text-primary}"
    typography: "{typography.mono}"
    rounded: "{rounded.sm}"
    padding: "12px"
---

# Design System: NexusGTM Control Plane

## Overview

**Creative North Star: "The Instrument Panel"**

This is a calibrated readout, not a dashboard-as-brand-expression. The chassis is a muted
warm neutral that asks for nothing; every colored pixel on the screen is a measurement.
One accent and four status colors are the entire chromatic budget, and each one means a
specific thing. When a surface is grey, it is grey because nothing there needed reporting.

The register is **precise, unhurried, understated** — confidence through restraint. Type is
small and dense but never crowded, figures are tabular so columns of money line up on the
decimal, and hierarchy is built almost entirely from weight, case, and colour of text
rather than from size jumps.

The second register is **calm but alert**. The system's job is to leave the reader alone
until something needs them: a halted run, an unreachable department, a run that cost more
than it should have. Those states get colour that nothing else on the screen is allowed to
borrow. This is the operating logic behind the colour budget — an accent spent on
decoration is an accent that can no longer raise an alarm.

The system is realised as a **console**: a fixed chassis (system bar and rail) around a
single live readout (the pane). There are no tabs and no page navigation. The rail is the
bezel and never changes; the pane is what is being measured.

**Key Characteristics:**

- A warm-neutral chassis — off-white with a green-yellow cast, deliberately not blue-grey —
  with the rail one tonal step recessed from the pane.
- One accent (Signal Blue), which marks selection and data and nothing else.
- Depth from hairlines and tonal layering; the system is flat with a single overlay shadow.
- Text-led hierarchy: uppercase micro-labels, `600`/`650` weights, tabular figures.
- No web fonts, no icon set, no imagery. The system font stack is the whole typographic
  palette.
- Status is always a dot **and** a word — colour is never the sole carrier of meaning.
- Prose carries the answers; the load-bearing figure is set inline at display size.

## Colors

A muted warm-neutral field carrying a small, strictly-rationed set of saturated signals.

Every value below clears 4.5:1 against the ground it sits on, in both themes. Where a hue
needed to move to get there it moved in **lightness only** — hue, role, and name are
unchanged.

### Primary

- **Signal Blue** (`#2470cc`; dark `#3987e5`): the single interface accent. It marks *what
  is selected* (the rail row, at 12% tint), *what is actionable* (text buttons), *what is
  live* (the breathing dot on a running orchestration), and *what the data is* (bar fills,
  line strokes, decision-trail nodes). It appears nowhere else.

### Secondary

The chart series palette. Signal Blue is series 1; these two extend it when a chart needs
more than one dimension.

- **Ember Orange** (`#eb6834`; dark `#f2794a`): series 2.
- **Meter Green** (`#178f64`; dark `#2fcb92`): series 3.

### Tertiary

Status colours. Semantic only, and the only hues permitted to signal state.

- **Clear Green** (`#0a7c0a`; dark `#17bd17`): completed, qualified, an agent run that
  returned what was expected.
- **Hold Amber** (`#96650b`; dark `#fab219`): the planner found no agent that fit.
- **Halt Orange** (`#b4471a`; dark `#ec835a`): the guardrail colour — every `halted_*`
  status, a rejected payload, an unreachable department, and a terminal step in the trail.
- **Fault Red** (`#d03b3b`; dark `#ef6161`): an agent raised, or the run was abandoned. The
  loudest colour in the system and the rarest.

### Neutral

- **Paper** (`#f9f9f7`; dark `#0d0d0d`): the system bar and the rail — the chassis. Also
  the *recessed* fill for payload wells, which makes them read as cut into the pane.
- **Card** (`#fcfcfb`; dark `#1a1a19`): the pane, and input surfaces.
- **Ink** (`#0b0b0b`; dark `#ffffff`): primary text. Near-black, never pure black.
- **Graphite** (`#52514e`; dark `#c3c2b7`): secondary text — rail rows at rest, rationale
  copy, the neutral chip.
- **Ash** (`#6e6c66`; dark `#898781`): muted text — section labels, table headers,
  timestamps, placeholders, chart axis labels.
- **Grid Rule** (`#e1e0d9`; dark `#2c2c2a`): row dividers, section separators, chart
  gridlines, the trail's spine.
- **Baseline Rule** (`#c3c2b7`; dark `#383835`): chart axis lines and the interpunct
  between supporting facts — one step darker than Grid Rule.
- **Hairline** (`rgba(11, 11, 11, 0.1)`; dark `rgba(255, 255, 255, 0.1)`): every structural
  border. Alpha-based on purpose, so it darkens whatever it sits on.

### Named Rules

**The One Accent Rule.** Signal Blue is the only non-semantic colour in the interface. It
never becomes a large fill, a filled button, a header band, or a brand wash. Its
findability is the product — the selected rail row is locatable in peripheral vision
precisely because nothing else on the screen is blue. If a new surface wants a second
accent, the answer is weight or case, not another hue.

**The Reserved Signal Rule.** The four status colours are semantic-only. Nothing decorative
may use Clear Green, Hold Amber, Halt Orange, or Fault Red — not a chart series, not a
hover state. A reader must be able to assume that orange on this screen means a guardrail
fired.

**The Dark Theme Parity Rule.** Dark is a peer theme, not a derivative. Every colour token
carries both values, and adding one without the other is an incomplete change. Series
colours *lighten* in dark mode; they never darken.

## Typography

**Display Font:** none — the system UI stack is the entire typographic palette
(`system-ui, -apple-system, "Segoe UI", sans-serif`).
**Body Font:** the same stack.
**Label/Mono Font:** `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace` for
identifiers and payloads.

**Character:** neutral and native by choice. There is no web font and no font loading — the
interface inherits whatever the operating system considers correct, which keeps it feeling
like a tool the reader's machine came with rather than a site they visited. All the
personality comes from how the stack is *used*: tight negative tracking on figures, wide
positive tracking on uppercase micro-labels, and only two weights above regular.

### Hierarchy

- **Display** (600, 27px, 1.15, `-0.02em`, tabular): never a standalone element. It exists
  only as the load-bearing figure *inside* an Answer sentence.
- **Headline** (650, 20px, `-0.01em`): the pane title, once per pane. In the orchestration
  pane the title is the account reference and takes the mono family at the same size.
- **Answer** (400, 20px, 1.35, `-0.01em`, max 46ch, balanced): the briefing's answer
  sentences. Regular weight at headline size — a statement, not a heading.
- **Title** (600, 13px, `0.045em`, uppercase): the wordmark. The only place this step
  appears now that panels are gone.
- **Body** (400, 13px, 1.5): the working size and the document base — rail rows, tables,
  rationale text, form controls.
- **Caption** (400, 12px): supporting facts, timestamps, costs in the rail, endpoints,
  text buttons.
- **Label** (600, 11px, `0.05em`, uppercase): rail section headings, block headings, and
  table column headers, all in Ash. Also the chip size, at 11px/600 but *not* uppercased —
  status words are shown as written.
- **Mono** (400, 12px): CRM reference identifiers, MCP endpoints, JSON payloads, and raw
  status values. Mono is a semantic signal, not a style: it marks a value that came from a
  machine and that a reader may need to copy exactly.

### Named Rules

**The Quiet Heading Rule.** Headings are smaller and lighter than the content they
introduce. An 11px Ash uppercase label sits above a 20px answer sentence. The reader is
looking for the answer, not for the word "spend".

**The Figure-In-Sentence Rule.** Numbers are not presented as stat tiles. The load-bearing
figure is set at display size *inside* the sentence that explains it, so the number and its
meaning are read in one movement and a figure can never appear without its unit of meaning.

**The Tabular Figures Rule.** Every number that appears in a column is
`font-variant-numeric: tabular-nums` and, in tables, right-aligned. One column uses one
precision: costs below a dollar all carry four decimal places, because a column mixing
`$0.01` and `$0.0056` cannot be compared by eye.

## Layout

A two-column console filling the viewport, with a fixed bar across the top.

- **System bar:** 46px, full width, Paper ground, hairline underneath.
- **Rail:** fixed 340px, Paper ground, hairline on the right. It owns its own scroll; the
  filter field is pinned above it and never scrolls away.
- **Pane:** fluid, Card ground, its own scroll, content held to a 860px measure with
  `26px 28px 72px` of padding.

Inside the pane, content is a vertical stack of blocks separated by a Grid Rule with `22px`
of clearance on each side. There is no card, no panel, and no nested container — a block is
a label, its content, and a rule.

**Density** is compact but not cramped: `8px 14px` rail rows, `10px 14px` table cells, a
constant 1.5 line height.

Spacing steps in use: `4 · 6 · 8 · 12 · 14 · 18 · 22 · 26 · 72`.

### Named Rules

**The Fixed Chassis Rule.** The bar and the rail never change with selection. Only the pane
swaps. A reader who has found a row keeps their place in the list no matter what they open.

**The Structural Breakpoint Rule.** There is exactly one media query (`900px`), and it
exists to change *structure*, not size: below it the rail becomes the whole screen and the
pane slides in over it as a detail view with its own back control. Everything else
responsive is intrinsic — `minmax(0, 1fr)` tracks, `flex-wrap`, `overflow-x` containers.
Reach for intrinsic sizing before adding a second query.

**The Table Scroll Rule.** Every table is wrapped in an `overflow-x: auto` container. A wide
table scrolls inside its own block; the page body never scrolls sideways.

## Elevation & Depth

**Depth remains an open decision.** The system is flat by observation, not by doctrine.
Treat flatness as the default and the starting point; a future surface may establish an
elevation vocabulary without arguing past a rule.

Separation comes from three things, in order: the 1px alpha hairline, a tonal step between
the chassis (Paper) and the readout (Card), and the Grid Rule that divides blocks within a
pane. Recession is expressed by inverting the tonal relationship — the payload well fills
with the *chassis* colour inside the pane, so it reads as cut in rather than raised up.

### Shadow Vocabulary

- **Floating overlay** (`box-shadow: 0 4px 14px rgba(0, 0, 0, 0.1)`): the only shadow in the
  system, on the chart tooltip. It marks something genuinely detached from document flow
  and following the cursor.

## Shapes

Rectangles with softened corners. There is no distinctive silhouette, no clipping, no
angle, and no decorative geometry — the form language is rectilinear and corner radius is
the only shape variable in play.

The ladder is now `6px` (payload wells, focus rings) → `7px` (inputs) → `999px` (chips).
The previous `8px` and `10px` steps disappeared with the cards and panels that used them;
the console has no floating container to round. The result is the three-step scale the
earlier crowded ladder should have been, arrived at by removing containers rather than by
renumbering.

The pill chip is the only shape that breaks the family, and it earns it — a fully rounded
status chip is legible as *a state* at a glance.

### Named Rules

**The Hairline Rule.** Every structural boundary is exactly `1px` of alpha-black, never a
solid colour and never thicker. The one exception is the decision trail's 2px spine, which
is a drawn line rather than a boundary.

**The No-Container Rule.** Content is separated by rules and space, not by boxes. Do not
reintroduce a card, panel, or tile to group things — a label plus a rule does the same work
without adding a border, a radius, a shadow, and an inset to every group on the screen.

## Components

The component character is **quiet frames around loud data**. Chrome recedes to almost
nothing — one hairline, no fill contrast — so the figure or the status is the only thing on
screen with weight.

### Buttons

The system has no filled button. Every button is either a rail row or a text button.

- **Text button:** Signal Blue, 12px, zero padding, no border or background. Underlines on
  hover.
- **Focus:** a 2px Signal Blue ring at 2px offset with a 6px radius, applied globally to
  `:focus-visible`. Mouse focus is suppressed; keyboard focus is never suppressed.

### Rail rows

- **Orchestration row:** two lines. The account reference in mono with its cost right-
  aligned, then a status dot, the status word, and a timestamp pushed to the right edge.
- **Pinned row:** single line, used for the two standing destinations (Today, Spend), with
  an optional right-aligned trailing figure.
- **Selected:** a 12% Signal Blue background tint plus Ink text and a semibold reference,
  carried by `aria-current`. Never a coloured left border.
- **Hover:** a 4% Ink tint. Focus rings inset by 2px so they read inside the rail.

### Chips

- **Style:** fully rounded, `2px 8px`, hairline border, 11px `600` text in the status
  colour, preceded by a 6px `currentColor` dot.
- **Tones:** good, attention, fault, neutral, live.
- **Live:** the dot breathes on a 2.4s cycle. This is state, not decoration — it means the
  console is polling and the run is still open.
- **Distinctive behaviour:** the word is always rendered, and its `title` carries a plain
  sentence explaining what the state means.

### Blocks

A block is an 11px Ash uppercase label, its content, and a Grid Rule above it. The first
block in a pane drops its rule and its top padding. This is the only grouping construct.

### Answer + support

The briefing's unit. An Answer sentence carries the finding with its figure inline at
display size; a `support` line beneath it carries qualifying facts at 12px Ash, separated by
Baseline Rule interpuncts. The alert variant switches the whole sentence to Halt Orange at
`600`.

### Exception rows

A borderless button row — mono reference, plain-language explanation, right-aligned
timestamp — divided by Grid Rules, tinting Signal Blue at 6% on hover. Used only in the
briefing's attention list.

### Inputs

- **Style:** Card background, hairline border, `7px` radius, `7px 10px` padding.
- **Focus:** the global ring.
- **Label:** visually hidden; the placeholder states both behaviours (filter, and Enter to
  search).

### Decision trail

The signature component. An ordered list rendered as a vertical timeline: each step is a
2px Grid Rule left border with a 10px Signal Blue node punched over it, ringed in 2px of
the pane colour so the node appears to float above the line. The final step's border goes
transparent so the timeline terminates. A step where the planner chose to stop is marked
terminal and its node switches to Halt Orange.

Each step stacks three registers: the agent name (13px `600` Ink) with its department in
Ash, the planner's rationale (13px Graphite, max 68ch), and the alternatives considered
plus timestamp (11px Ash). That descending scale *is* the argument — the decision, then the
reasoning, then what else was on the table.

### Tables

Uppercase Ash column headers over a hairline, Grid Rule row dividers, no zebra striping, no
row borders on the outside. Numeric columns are right-aligned and tabular. Tables carry no
hover state — rows in a table are not clickable anywhere in this system; the rail owns
selection.

### Charts

Recharts, styled entirely from tokens. Bars are Signal Blue with a 16px bar size and a
`[0, 4, 4, 0]` radius; lines are 2px Signal Blue with `r=4` dots ringed in the pane colour —
the same floating-node treatment as the decision trail. Gridlines are Grid Rule on one axis
only; axis lines are Baseline Rule; tick labels are 11px Ash. Tooltips are the one elevated
surface in the system. Values are labelled directly on bars rather than relying on the axis.

### System map

A radial constellation of the running system: the orchestrator at the centre, a hub per
department on an evenly spaced ring, and each agent fanned outward across a ±22° arc. It
runs the scoped dark world described below.

Every edge is a straight line, in three tiers of decreasing weight. The tree is what gives
the constellation its shape, so it leads; the handoffs are what happened inside it, so they
recede.

| tier | edge | width | opacity |
|---|---|---|---|
| trunk | orchestrator → department | 2.5 | 0.55 |
| branch | department → agent | 1.75 | 0.42 |
| flow | agent → agent | 0.75–1.75 | 0.20–0.34 |

Flow edges join agents the planner actually handed off between, and their width and opacity
scale with how often that handoff occurred. A flow edge whose every observation was rejected
by the target's schema draws dashed and dimmer still — below a successful handoff on purpose,
because a route the schema refused should never read as more substantial than one that went
through. Live and hovered edges jump to near-full opacity; the quiet resting field is what
lets them carry.

A handoff between two diametrically opposite agents runs through the middle. That is fine:
the flow layer paints before the orchestrator, whose core and halo cover it.

Node sizes are geometry, not style: they live beside the positions in
[layout.ts](dashboard/src/graph/layout.ts) and each node carries its own radius, because an
agent's size depends on how much room its neighbours leave. A department hub renders about
28px across and an agent about 19px, holding a 1.5:1 ratio. Two clamps keep that safe as the
system grows — the agent radius is capped against the spacing between adjacent agents, and
the fan width is capped against the department count. Neither engages at three departments;
at seven agents in one department the radius eases down rather than letting nodes collide.

Nothing about the composition is per-department: hues are generated from the id-sorted
department list, positions from the count. A department added to `departments.yaml` takes
its place with no edit here. There are deliberately **no department icons** — an icon
cannot be authored for a department that does not exist yet, and a fallback glyph for new
ones would make the extensibility second-class.

Labels are HTML positioned over the SVG rather than `<text>` inside it, so they stay on the
type ramp at every pane width instead of scaling down with the viewBox. Department names are
always visible; **agent names appear only while their node is hovered**, fading in rather
than popping — seven nodes do not need four permanent captions. The labels layer is
`aria-hidden`, so the SVG `<desc>` is the accessible source and names every department with
its full agent roster, including agents that appear in no handoff.

An unreachable department keeps its agents, recovered from the paths of past runs and drawn
dormant, so stopping a server severs a branch instead of deleting it.

Hover is carried by a **locked-height readout strip** beneath the canvas: one nowrap status
row and one detail row clamped to two lines, in a box of fixed height. This is structural,
not stylistic. The canvas is sized from leftover space, so a strip that grew with its text
would resize the constellation on every hover — and it would, because the registry
descriptions it prints run to 189 characters against a ~78-character resting line. Every
state renders the same two rows, so no text can change the height.

Flow edges carry their own hover through a transparent 16px hit stroke layered beneath the
nodes; a chord's visible stroke is 1–3.4px and no pointer can reliably find it. A hovered
edge brightens but does **not** dim other branches — department hover owns dimming, and two
gestures competing for it makes the map twitch when the pointer crosses from a chord to a hub.

### Skeletons

Loading is a shape at the size of the content it replaces, sweeping a Grid Rule gradient on
a 1.4s linear loop. Never a spinner dropped into the middle of content.

## Motion

Two durations and one easing curve: `140ms` for state feedback (hover, selection),
`220ms` for structural movement (the mobile pane sliding in), both on
`cubic-bezier(0.2, 0, 0.15, 1)`.

Four things animate. Three report state: the live dot breathing while a run is open, the
skeleton sweep while data is loading, and the detail pane sliding over the rail on narrow
screens. The fourth — the map's travelling handoff dots — does not, and is a recorded
exception rather than a quiet breach; see Scoped Exceptions. Everything else changes
instantly.

`prefers-reduced-motion: reduce` collapses every duration to `0.01ms`. Note that collapsing a
duration is not the same as removing an animation: anything whose *resting* frame would be
wrong when frozen has to be hidden outright, which is why the map's dots are `display: none`
under that query rather than merely stopped.

### Named Rules

**The Motion-Means-State Rule.** If an animation is not reporting a state the reader would
otherwise have to infer, it does not belong. There are no entrance animations, no scroll
choreography, and no page-load sequences — the console loads into a task. Exactly one
exception exists, named and scoped under Scoped Exceptions; a second one is not free to
appear by pointing at the first.

## Do's and Don'ts

### Do:

- **Do** spend Signal Blue on selection and data only. Its scarcity is the mechanism.
- **Do** give every new colour token both a light and a dark value in the same change, and
  check it clears 4.5:1 against its own ground in both.
- **Do** render status as a dot *and* a word, with a plain-language sentence in `title`.
- **Do** set the load-bearing figure inside the sentence that explains it.
- **Do** use one precision per column — four decimal places below a dollar.
- **Do** separate content with a label and a rule rather than a box.
- **Do** translate halt codes and agent identifiers into outcome language, and keep the raw
  value reachable in the detail pane's Raw values block.
- **Do** state a below-threshold condition honestly — refuse to draw a chart that would
  mislead, and say why.
- **Do** keep the chassis fixed when the pane changes.

### Don't:

- **Don't** use Clear Green, Hold Amber, Halt Orange, or Fault Red for anything that isn't
  status. They are reserved signals.
- **Don't** introduce a second interface accent. If something needs to stand apart, change
  its weight, case, or size.
- **Don't** reintroduce cards, panels, or tiles. See The No-Container Rule.
- **Don't** add a fourth corner radius. `6 / 7 / 999` covers everything the console has.
- **Don't** mark selection with a coloured left border; use the background tint and
  `aria-current`.
- **Don't** thicken the hairline past `1px` or make it a solid colour.
- **Don't** add a web font. The native system stack is a deliberate choice.
- **Don't** animate anything that isn't reporting state.
- **Don't** hardcode the three current departments or their agent names. The taxonomy comes
  from the registry and must absorb a fourth department with no visual change.
- **Don't** read the current flatness as a ban on depth — but if you introduce elevation,
  introduce a vocabulary, not a one-off shadow.
- **Don't** widen the map's exception past `.map-stage`. See below.
- **Don't** let the map's readout strip size itself from its content. Its height is locked
  and its rows are clamped precisely because the canvas above it takes the leftover space —
  a growing strip resizes the constellation on every hover.

## Scoped Exceptions

One surface departs from this system on purpose. Recording it here keeps the rest of the
system honest — an undocumented departure is drift, and the next person cannot tell which
of the two is the mistake.

### The system map's dark world

**Scope.** `.map-stage` in [dashboard/src/styles.css](dashboard/src/styles.css) and nothing
else. The pane heading, the legend chips beneath it, the rail, and the system bar are all
ordinary console chrome on the console's own surfaces.

**Why.** The user pinned this aesthetic with a reference image. The map is a diagram of a
running machine rather than a reading surface, and a constellation of coloured department
branches on a dark field is what makes its structure legible at a glance.

**What it changes.**

- A committed dark ground (`map-ground` family) in **both** themes. The map does not invert
  with the console around it; it is an instrument display, not a document.
- A **generated categorical palette** — one hue per department, evenly spaced from the
  id-sorted list, rendered `oklch(72% 0.16 <hue>)`. This is the one place a second, third,
  and fourth accent exist. They are a data encoding, not decoration: the hue identifies
  which department a node belongs to and carries no other meaning.
- A static starfield, the only purely decorative *static* element in the entire system.
- **Continuous travelling dots on every handoff edge**, running sender to receiver. These
  animate at rest, so by the Motion-Means-State Rule's own definition they are decoration.
  They earn their place by making direction legible — a straight line between two agents
  otherwise cannot say which way the handoff went — and they stay partly informational
  because a wholly-rejected handoff gets no dot: nothing flowed, so nothing travels. Every
  dot moves at one speed (duration derives from edge length), staggered deterministically so
  they neither fire in lockstep nor reshuffle between polls, and capped at 24 edges.

**What it does not change.**

- The four status hues — Clear Green, Hold Amber, Halt Orange, Fault Red — do **not** appear
  in the map at all. The Reserved Signal Rule holds without qualification.
- Signal Blue is not used as an accent inside the map; the department hues replace it there.
- The type ramp holds. Labels are HTML at documented steps, not scaled SVG text.
- The node pulse still means an agent is working and the core pulse still means the planner
  is choosing; the travelling dots above are the *only* motion here that reports nothing.
- The starfield stays deliberately **static** — a continuously animating 260-circle field
  would repaint forever to report nothing, and one motion exception is the budget.
- `prefers-reduced-motion` still removes every animation, dots included.

**The boundary is the rule.** A future surface wanting a dark ground or a second accent does
not inherit this exception by pointing at it. It argues its own case and gets its own entry
here, or it uses the system.
