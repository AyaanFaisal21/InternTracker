"""The rubric model and the placeholder scorer.

A rubric is only useful if it is enforced. Every test here is about the
report being rejected when it is not gradeable: a missing dimension, a
duplicate, a level outside the scale. The API enforces the schema; this
enforces the meaning.
"""

import pytest
from pydantic import ValidationError

from interview.problems import get_problem
from interview.rubric import (
    ANCHORS,
    DIMENSIONS,
    LEVELS,
    DimensionScore,
    Report,
    rubric_text,
)
from interview.schema import Speaker, Transcript
from interview.scorer import SYSTEM, StubScorer


def score(dimension: str, level: int = 3) -> DimensionScore:
    return DimensionScore(
        dimension=dimension,
        level=level,
        evidence="the student said the map holds value to index",
        improvement="State the input bound before choosing the structure.",
    )


def full(levels: dict[str, int] | None = None) -> Report:
    levels = levels or {}
    return Report(
        scores=[score(d, levels.get(d, 3)) for d in DIMENSIONS],
        summary="Framed it well, never derived the bound.",
        next_steps=["Name the dominating operation out loud."],
    )


# -- shape ----------------------------------------------------------------


def test_the_rubric_has_six_dimensions_and_four_levels():
    assert len(DIMENSIONS) == 6
    assert LEVELS == (1, 2, 3, 4)
    assert set(ANCHORS) == set(DIMENSIONS)
    for dimension in DIMENSIONS:
        assert set(ANCHORS[dimension]) == set(LEVELS)
        for text in ANCHORS[dimension].values():
            assert text.strip() and text.endswith(".")


def test_the_scoring_prompt_is_built_from_the_anchors():
    """One rubric, not two. A prompt that restates the anchors in prose is a
    second rubric that drifts."""
    prompt = SYSTEM
    for dimension in DIMENSIONS:
        assert dimension in prompt
        for level in LEVELS:
            assert ANCHORS[dimension][level] in prompt
    assert rubric_text() in prompt


def test_a_complete_report_validates():
    report = full()
    assert report.total == 18
    assert report.level("complexity_analysis") == 3
    assert len(report.scores) == 6


def test_levels_are_independent_not_averaged():
    report = full({"complexity_analysis": 4, "problem_framing": 1})
    assert report.level("complexity_analysis") == 4
    assert report.level("problem_framing") == 1
    assert report.total == 4 + 1 + 3 * 4


# -- rejection ------------------------------------------------------------


def test_a_missing_dimension_is_rejected():
    with pytest.raises(ValidationError, match="missing dimensions"):
        Report(
            scores=[score(d) for d in DIMENSIONS[:-1]],
            summary="s",
            next_steps=[],
        )


def test_a_duplicated_dimension_is_rejected():
    with pytest.raises(ValidationError, match="more than once"):
        Report(
            scores=[score(d) for d in DIMENSIONS] + [score(DIMENSIONS[0])],
            summary="s",
            next_steps=[],
        )


def test_an_unknown_dimension_is_rejected():
    with pytest.raises(ValidationError):
        DimensionScore(
            dimension="vibes", level=3, evidence="e", improvement="i"
        )


@pytest.mark.parametrize("level", [0, 5, -1, 3.5])
def test_a_level_off_the_scale_is_rejected(level):
    with pytest.raises(ValidationError):
        DimensionScore(
            dimension="edge_cases", level=level, evidence="e", improvement="i"
        )


def test_every_field_is_required_so_the_api_schema_can_demand_them_all():
    with pytest.raises(ValidationError):
        DimensionScore(dimension="edge_cases", level=3)
    with pytest.raises(ValidationError):
        Report(scores=[score(d) for d in DIMENSIONS])


# -- the placeholder scorer -----------------------------------------------


def test_the_stub_scorer_returns_a_valid_report_that_admits_it_is_fake():
    transcript = Transcript()
    transcript.add(Speaker.INTERVIEWER, "why a hash map")
    transcript.add(Speaker.CANDIDATE, "constant lookup")

    report = StubScorer().score(get_problem("two-sum"), transcript)
    assert isinstance(report, Report)
    assert {s.dimension for s in report.scores} == set(DIMENSIONS)
    # Obviously fake rather than plausibly wrong: a misconfigured deploy must
    # not put a believable score in front of a student.
    assert "placeholder" in report.summary.lower()
    assert all("no model was called" in s.evidence for s in report.scores)
