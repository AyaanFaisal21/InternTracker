"""The scoring rubric.

Six dimensions, four levels each, with an anchor sentence for every cell.
The anchors are the rubric: they are what the scorer is shown, what the
student is shown, and what a human grader checks against when we measure
whether the scores are reproducible. Changing an anchor changes the scores,
so the anchors live here in one place rather than inside a prompt string.

Shape rules come from the structured-output API. Every field is required and
`additionalProperties` is false, so `Literal` enums do the range checking the
API cannot express with numeric bounds.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, model_validator

Dimension = Literal[
    "problem_framing",
    "approach_justification",
    "complexity_analysis",
    "edge_cases",
    "communication",
    "response_to_challenge",
]
DIMENSIONS: tuple[str, ...] = get_args(Dimension)

Level = Literal[1, 2, 3, 4]
LEVELS: tuple[int, ...] = get_args(Level)
LEVEL_NAMES = {1: "absent", 2: "developing", 3: "solid", 4: "strong"}

# Level 4 is deliberately hard. It means a real interviewer would have no
# follow-up left to ask on that dimension, which is rare in a 20 minute
# session and should stay rare, or the top of the scale stops carrying
# information.
ANCHORS: dict[str, dict[int, str]] = {
    "problem_framing": {
        1: "Started solving without restating the problem or its constraints.",
        2: "Restated the problem but not the input bounds or the output contract.",
        3: "Restated the problem, named the input size and the output, and "
           "checked at least one assumption.",
        4: "Also named an assumption the statement left open and said what "
           "they would ask the interviewer about it.",
    },
    "approach_justification": {
        1: "Named an approach and gave no reason for it.",
        2: "Gave a reason that only restates the approach, such as choosing a "
           "hash map because it does lookups.",
        3: "Tied the choice to a property the problem actually needs and "
           "named one approach they rejected and why.",
        4: "Also said what would change the choice: a different input size, a "
           "different key distribution, or a memory limit.",
    },
    "complexity_analysis": {
        1: "Gave no bound, or gave a bound with no derivation.",
        2: "Gave a bound but could not say which step produces it.",
        3: "Gave time and space, and named the dominating operation for each.",
        4: "Also handled amortized or worst versus average cases where the "
           "problem has them, and said which one they were quoting.",
    },
    "edge_cases": {
        1: "Raised no edge cases.",
        2: "Named a category of edge case without tracing a single concrete "
           "input through the approach.",
        3: "Traced at least two concrete inputs through the approach, "
           "including one degenerate case.",
        4: "Also found a case the interviewer had not raised and said what "
           "the approach returns on it.",
    },
    "communication": {
        1: "A listener cannot follow the order of the reasoning.",
        2: "Followable, but backtracks without signalling, or leaves the "
           "interviewer to infer the plan.",
        3: "States the plan before the detail, signals moves between steps, "
           "and finishes sentences.",
        4: "Also adapts to the interviewer: shortens where they are satisfied "
           "and expands where they are not.",
    },
    "response_to_challenge": {
        1: "Repeated the original answer, or agreed instantly with no reasoning.",
        2: "Changed position under pressure without saying what changed their "
           "mind.",
        3: "Engaged the specific challenge, said whether it held, and revised "
           "or defended with a reason.",
        4: "Also caught the flaw before the interviewer finished pointing at "
           "it, or correctly defended against a challenge that was wrong.",
    },
}


def rubric_text() -> str:
    """The anchors rendered for a prompt. Built from ANCHORS so the prompt
    and the stored rubric can never drift apart."""
    blocks = []
    for dimension in DIMENSIONS:
        lines = [dimension]
        for level in LEVELS:
            lines.append(f"  {level} {LEVEL_NAMES[level]}: {ANCHORS[dimension][level]}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


class DimensionScore(BaseModel):
    """One graded dimension. `evidence` is what makes a score checkable: a
    level with no citation is a level nobody can argue with, which is the
    failure mode of every soft rubric."""

    dimension: Dimension
    level: Level
    evidence: str      # what the student actually said, quoted or paraphrased
    improvement: str   # one concrete thing to do differently next time


class Report(BaseModel):
    """The structured output of the scoring pass, and the row the session
    stores. Six scores, one per dimension, no more and no fewer."""

    scores: list[DimensionScore]
    summary: str
    next_steps: list[str]

    @model_validator(mode="after")
    def _exactly_one_score_per_dimension(self) -> "Report":
        seen = [s.dimension for s in self.scores]
        missing = [d for d in DIMENSIONS if d not in seen]
        if missing:
            raise ValueError(f"report is missing dimensions: {', '.join(missing)}")
        if len(seen) != len(set(seen)):
            raise ValueError("report scores the same dimension more than once")
        return self

    @property
    def total(self) -> int:
        """Sum across dimensions, 6 to 24. Shown as a trend line across a
        student's sessions, never as a single verdict on one session."""
        return sum(s.level for s in self.scores)

    def level(self, dimension: str) -> int:
        """The level for one dimension. The validator guarantees it exists."""
        return next(s.level for s in self.scores if s.dimension == dimension)
