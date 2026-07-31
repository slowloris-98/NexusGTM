"""The planner: picks the next agent at runtime from the registry.

The playbook is rendered into the prompt as guidance text, never parsed into a
graph -- parsing it into branch logic would make it the fixed sequence the design
rules forbid. Thresholds and negative signals reach the planner as context, not
as `if` statements in code.
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
- Set finished=true when the success criteria are met, or when the lead is \
disqualified and further work would be waste. Set outcome accordingly.
- rationale is read by leadership. State why this agent, now, in one or two \
plain sentences."""


def render_playbook() -> str:
    pb = playbook()
    lines = [f"GOAL: {pb.get('goal', '').strip()}", "", "SUCCESS CRITERIA:"]
    lines += [f"  - {c}" for c in pb.get("success_criteria", [])]

    lines += ["", "PATTERNS (guidance, not a fixed sequence):"]
    for pattern in pb.get("patterns", []):
        lines.append(f"  {pattern['name']} -- when {pattern['when']}")
        lines.append(f"    {pattern['guidance'].strip()}")

    thresholds = pb.get("thresholds", {})
    lines += ["", "THRESHOLDS:"]
    lines += [f"  {k}: {v}" for k, v in thresholds.items()]

    signals = pb.get("negative_signals", [])
    if signals:
        lines += ["", f"NEGATIVE SIGNALS: {', '.join(signals)}"]
    return "\n".join(lines)


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
) -> str:
    sections = [
        render_playbook(),
        "",
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
