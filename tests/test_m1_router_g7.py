"""G7: deterministic router decision cases (MODEL_ROUTING_SPEC §4 + ADR-024)."""

from __future__ import annotations

import pytest

from researchos.contracts.gateway import TaskType
from researchos.services.gateway.router import Policy, Signals, candidates, composite_confidence, decide, tier_allowed

POL = Policy()
CASES = [
    # (signals, attempt, failures, budget_ok, at_max, expected action)
    (Signals(1.0, 1.0, 0.95), 1, 0, True, False, "accept"),
    (Signals(1.0, 1.0, 0.0), 1, 0, True, False, "accept"),  # low self-report alone: no escalation
    (Signals(0.0, 0.0, 1.0), 1, 1, True, False, "retry_same"),  # first validation failure: retry
    (Signals(0.0, 0.0, 1.0), 2, 2, True, False, "escalate"),  # high self-report cannot bypass validators
    (Signals(0.0, 0.0, 1.0), 2, 2, False, False, "fail"),  # budget vetoes escalation
    (Signals(0.0, 0.0, 1.0), 2, 2, True, True, "fail"),  # task type caps the level
    (Signals(1.0, 0.5, None), 1, 0, True, False, "retry_same"),  # composite in [0.60, 0.85)
    (Signals(1.0, 0.5, None), 2, 0, True, False, "accept"),  # validators pass after retry (labelled)
    (Signals(1.0, 0.0, 0.0), 1, 0, True, False, "escalate"),  # no evidence coverage: 0.5/0.9 = 0.56 < 0.60
]


@pytest.mark.parametrize("s,attempt,fails,budget,at_max,expected", CASES)
def test_decisions(s: Signals, attempt: int, fails: int, budget: bool, at_max: bool, expected: str) -> None:
    d = decide(
        s, attempt_on_model=attempt, validation_failures=fails, budget_ok=budget, at_max_level=at_max, policy=POL
    )
    assert d.action == expected, (d.reason, d.confidence)


def test_composite_weights() -> None:
    assert composite_confidence(Signals(1.0, 1.0)) == 1.0
    assert composite_confidence(Signals(0.0, 1.0, 1.0)) < 0.6  # failed validators dominate any self-report


def test_tiers_and_force_top() -> None:
    assert not tier_allowed("premium", Policy(premium_enabled=False), False)
    assert tier_allowed("premium", Policy(premium_enabled=False), True)  # manual force-top
    assert not tier_allowed("top", Policy(top_enabled=False), True)  # very-top disabled by default
    assert candidates(TaskType.ROUTINE_TEXT, False)[0] == "ROUTINE_TEXT"
    assert candidates(TaskType.ROUTINE_TEXT, True)[0] == "LONG_DOCUMENT_REASONING"
