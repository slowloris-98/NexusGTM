"""The planner: picks the next agent at runtime from the registry.

The playbook is rendered into the prompt as guidance text, never parsed into a
graph -- parsing it into branch logic would make it the fixed sequence the design
rules forbid. Thresholds and negative signals reach the planner as context, not
as `if` statements in code.

The same holds for flows. A flow names a scenario the system knows how to run,
and the planner is told which one is in force -- but the action space is every
agent under every flow, and the flow the planner declares is a label on its
judgment, not a gate on its choices.
"""

from __future__ import annotations

import json
from typing import Any

from core import llm as llm_module
from core.config import playbook
from core.llm import LLMResult, strict_schema
from core.registry import AgentSpec

DECISION_SCHEMA = strict_schema(
    "routing_decision",
    {
        "chosen_agent": {
            "type": ["string", "null"],
            "description": "Agent name to run next, or null to finish/stop.",
        },
        "rationale": {
            "type": "string",
            "description": "Why this agent next, given the goal and current state.",
        },
        "candidates_considered": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Agents weighed before choosing.",
        },
        "input_payload": {
            "type": "string",
            "description": (
                "JSON object string for the chosen agent's payload, assembled from "
                "the blackboard. Empty string when finishing."
            ),
        },
        "finished": {
            "type": "boolean",
            "description": "True when the goal is met or the lead is disqualified.",
        },
        "outcome": {
            "type": ["string", "null"],
            "description": "When finished: qualified, disqualified, or null.",
        },
        # strict_schema puts every property in `required`, so the model restates
        # the flow every step. That is the point: a switch becomes something the
        # planner said, recorded against the step it happened on, rather than
        # something the orchestrator has to infer from the agent it picked.
        "flow": {
            "type": ["string", "null"],
            "description": (
                "The flow id you are working under. Repeat the current one unless "
                "the state now clearly fits another declared flow better. null "
                "means keep the current one."
            ),
        },
    },
)

INSTRUCTIONS = """You are the GTM orchestration planner. At each step you choose \
the single next agent to run, or declare the run finished.

Rules:
- Choose only from the listed available agents. Their names are exact.
- Assemble input_payload from the blackboard so it satisfies the chosen agent's \
input schema. Copy values from the blackboard; never invent data an agent has \
not produced.
- Do not re-run an agent that already produced its output in the history.
- The playbook is guidance for the judgment, not a script. Follow the pattern \
that matches the current state.
- You are working under one flow at a time. Restate its id in `flow` every step.
- A flow is guidance, not a gate. Every listed agent is available under every \
flow; choose the one the current state actually calls for.
- Switch flows only when the state has genuinely changed shape -- a churn \
assessment that turns out to be an expansion signal, a sourced account that \
arrives already qualified. Name a declared flow id; an invented one is ignored.
- Set finished=true when the success criteria are met, or when the lead is \
disqualified and further work would be waste. Set outcome accordingly.
- rationale is read by leadership. State why this agent, now, in one or two \
plain sentences."""


# These accessors degrade rather than raise, deliberately unlike the policy
# renderers in the department servers. A playbook with no `flows` is the playbook
# this file shipped with before flows existed, and it still has to render; a
# playbook with no `icp` is a broken deployment and should fail loudly.
def flows() -> list[dict]:
    return playbook().get("flows", []) or []


def flow_by_id(flow_id: str | None) -> dict | None:
    if not flow_id:
        return None
    return next((f for f in flows() if f.get("id") == flow_id), None)


def default_flow_id() -> str | None:
    """The flow marked `default:`, else the first declared, else none at all."""
    declared = flows()
    if not declared:
        return None
    marked = next((f for f in declared if f.get("default")), None)
    return (marked or declared[0]).get("id")


def _render_patterns(patterns: list[dict]) -> list[str]:
    lines = []
    for pattern in patterns:
        lines.append(f"  {pattern['name']} -- when {pattern['when']}")
        lines.append(f"    {pattern['guidance'].strip()}")
    return lines


def render_flow_index(current: str | None = None) -> str:
    """One line per flow, rendered every step.

    This is the whole switching mechanism: the planner cannot choose a flow it has
    never been shown. Only one line each, because showing five flows in full would
    be five goals and fifteen patterns of prompt on every step, against a budget
    measured in cents.
    """
    declared = flows()
    if not declared:
        return ""
    lines = ["FLOWS (the named orchestrations this system runs):"]
    for flow in declared:
        mark = "*" if flow.get("id") == current else "-"
        lines.append(
            f"  {mark} {flow['id']} ({flow.get('label', flow['id'])}) "
            f"-- when {flow.get('when', '')}"
        )
    return "\n".join(lines)


def render_flow(flow: dict) -> str:
    """The goal, criteria and patterns of one flow -- the one in force."""
    lines = [f"CURRENT FLOW: {flow['id']}", "", f"GOAL: {flow.get('goal', '').strip()}"]

    criteria = flow.get("success_criteria", [])
    if criteria:
        lines += ["", "SUCCESS CRITERIA:"] + [f"  - {c}" for c in criteria]

    patterns = flow.get("patterns", [])
    if patterns:
        lines += ["", "PATTERNS FOR THIS FLOW (guidance, not a fixed sequence):"]
        lines += _render_patterns(patterns)
    return "\n".join(lines)


def render_common() -> str:
    """What holds whichever flow is in force."""
    pb = playbook()
    lines: list[str] = []

    patterns = (pb.get("common") or {}).get("patterns", [])
    if patterns:
        lines += ["PATTERNS FOR EVERY FLOW:"] + _render_patterns(patterns)

    thresholds = pb.get("thresholds", {})
    if thresholds:
        lines += ["", "THRESHOLDS:"] + [f"  {k}: {v}" for k, v in thresholds.items()]

    signals = pb.get("negative_signals", [])
    if signals:
        lines += ["", f"NEGATIVE SIGNALS: {', '.join(signals)}"]
    return "\n".join(lines)


def render_playbook(flow_id: str | None = None) -> str:
    """Index of every flow, detail of the current one, then the common rules."""
    flow = flow_by_id(flow_id) or flow_by_id(default_flow_id())
    sections = [render_flow_index(flow.get("id") if flow else None)]
    if flow:
        sections.append(render_flow(flow))
    sections.append(render_common())
    return "\n\n".join(s for s in sections if s)


def render_agents(agents: list[AgentSpec]) -> str:
    if not agents:
        return "(none -- no departments reachable)"
    blocks = []
    for spec in agents:
        blocks.append(
            f"- {spec.agent} [{spec.department_name}]\n"
            f"    {spec.description}\n"
            f"    input schema: {json.dumps(spec.input_schema)}"
        )
    return "\n".join(blocks)


def render_history(history: list[dict]) -> str:
    if not history:
        return "(nothing run yet)"
    lines = []
    for entry in history:
        lines.append(
            f"  step {entry['step_no']}: {entry['agent']} -> {entry['status']}"
            + (f" ({entry['note']})" if entry.get("note") else "")
        )
    return "\n".join(lines)


def build_prompt(
    *,
    crm_reference_id: str,
    blackboard: dict,
    agents: list[AgentSpec],
    history: list[dict],
    step_no: int,
    total_cost_usd: float,
    budget_usd: float,
    observation: str | None = None,
    flow: str | None = None,
) -> str:
    sections = [
        render_playbook(flow),
        "",
        # Never filtered by flow. Narrowing the action space to the current flow's
        # usual agents would turn the flow into the branch logic this design
        # exists to avoid, and would make a mid-run switch unreachable.
        "AVAILABLE AGENTS (your entire action space):",
        render_agents(agents),
        "",
        f"ACCOUNT: {crm_reference_id}",
        f"STEP: {step_no}   SPENT: ${total_cost_usd:.4f} of ${budget_usd:.2f} budget",
        "",
        "BLACKBOARD (everything produced so far):",
        json.dumps(blackboard, indent=2, default=str),
        "",
        "HISTORY:",
        render_history(history),
    ]
    if observation:
        sections += ["", f"OBSERVATION FROM LAST ATTEMPT: {observation}"]
    sections += ["", "Choose the next agent, or finish."]
    return "\n".join(sections)


def decide(prompt: str) -> tuple[dict[str, Any], LLMResult]:
    """Ask for the next routing decision. Returns (decision, llm result)."""
    # Module-qualified so tests can patch core.llm.llm_call in one place.
    result = llm_module.llm_call(prompt, instructions=INSTRUCTIONS, schema=DECISION_SCHEMA)
    decision = dict(result.parsed or {})

    # input_payload travels as a JSON string: strict structured-output mode
    # cannot express a free-form object, since it requires every property of
    # every object to be declared up front.
    raw = decision.get("input_payload") or ""
    if isinstance(raw, str):
        try:
            decision["input_payload"] = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            decision["input_payload"] = {}
    return decision, result
