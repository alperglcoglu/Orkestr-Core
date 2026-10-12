"""Structured outputs that agents return (PHASE3_IMPLEMENTATION.md §6.2, §7.1).

Each model gives the JSON Schema passed to the executor and validates what comes back.
Domain rules (owners, dependencies, limits) are checked again when the core records the
result, so a schema-valid output can still be rejected.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..constants import AGENT_ROLES, OWNERS

Owner = Literal[OWNERS]  # type: ignore[valid-type]
Role = Literal[AGENT_ROLES]  # type: ignore[valid-type]
Severity = Literal["critical", "high", "medium", "low", "info"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanTask(Strict):
    title: str = Field(min_length=1)
    owner: Owner
    description: str = ""
    acceptance_criteria: list[str] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list, description='Existing task IDs or "#n" for the n-th task of this plan')
    scope: list[str] = Field(default_factory=list, description="Path patterns the task may change, relative to the project root")
    verify: list[str] = Field(default_factory=list, description="Exact test commands, runnable from the project root")
    requires_approval: bool = False


class PlanDecision(Strict):
    question: str = Field(min_length=1)
    options: list[str] = Field(min_length=2)
    blocks: list[str] = Field(min_length=1, description='"#n" for each task of this plan that must wait for the decision')
    context: str = ""


class PlanOutput(Strict):
    summary: str
    prd_markdown: str = Field(description="Product requirements document in Markdown")
    tasks: list[PlanTask] = Field(min_length=1)
    decisions_needed: list[PlanDecision] = Field(
        default_factory=list, description="Design choices the team must settle in a meeting before the blocked tasks start"
    )


class DecisionNeeded(Strict):
    question: str
    options: list[str] = Field(min_length=2)


class RestrictedAction(Strict):
    action: str
    category: Literal["destructive", "deploy", "publish", "git_push", "external_communication", "spending", "permission_change", "sensitive_data", "other"]
    reason: str


class ImplementOutput(Strict):
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list, description="Exact commands that verify this task, runnable from the project root")
    notes: list[str] = Field(default_factory=list)
    decision_needed: DecisionNeeded | None = None
    restricted_action: RestrictedAction | None = None


class Finding(Strict):
    title: str
    severity: Severity
    status: Literal["confirmed", "suspected"]
    component: str = ""
    evidence: str = Field(min_length=1)
    recommendation: str = ""


class ReviewOutput(Strict):
    verdict: Literal["PASS", "CHANGES_REQUESTED", "BLOCKED"]
    findings: list[Finding] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class ContributionOutput(Strict):
    position: str = Field(min_length=1)
    preferred_option: str | None = Field(default=None, description="The id of the option this participant prefers")
    key_points: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    high_severity_concern: bool = False


class Option(Strict):
    id: str
    name: str
    summary: str = ""


class ScopeChange(Strict):
    task_id: str
    add: list[str] = Field(min_length=1, description="Path patterns to add to the task's scope")


class SynthesisDecision(Strict):
    question: str
    selected_option: str | None = None
    rationale: str = ""
    tradeoffs: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    founder_required: bool = False
    scope_changes: list[ScopeChange] = Field(
        default_factory=list, description="Files a steered task must also change for this decision; applied when the decision is approved"
    )


class Dissent(Strict):
    role: str
    objection: str


class ActionItem(Strict):
    title: str
    owner: Owner
    description: str = ""
    acceptance_criteria: list[str] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    verify: list[str] = Field(default_factory=list)
    requires_approval: bool = False


class SynthesisOutput(Strict):
    options: list[Option]
    criteria: list[str] = Field(default_factory=list)
    evaluation: str = ""
    outcome: Literal["DECIDED", "ESCALATED", "NO_DECISION"]
    decision: SynthesisDecision | None = None
    dissent: list[Dissent] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)


class Lesson(Strict):
    lesson: str
    evidence: str


class RetroOutput(Strict):
    scope: str
    went_well: list[str] = Field(default_factory=list)
    went_badly: list[str] = Field(default_factory=list)
    incorrect_assumptions: list[str] = Field(default_factory=list)
    decomposition_assessment: str = ""
    meeting_assessment: str = ""
    lessons: list[Lesson] = Field(default_factory=list)
    proposed_changes: list[str] = Field(default_factory=list)


OUTPUTS: dict[str, type[BaseModel]] = {
    "plan": PlanOutput,
    "implement": ImplementOutput,
    "review": ReviewOutput,
    "contribution": ContributionOutput,
    "synthesis": SynthesisOutput,
    "retro": RetroOutput,
}


def schema_for(kind: str) -> dict:
    return OUTPUTS[kind].model_json_schema()
