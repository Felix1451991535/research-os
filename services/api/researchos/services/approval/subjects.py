"""Approval-subject registry (extensible, ADR-030). Core subjects are declared here; plugins add more."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ...contracts.approvals import ApprovalSubjectSpec

# Resolves the CURRENT content hash of a subject. Approval binds to this hash; any change voids the approval.
HashResolver = Callable[[sqlite3.Connection, str], str]

CORE_SUBJECTS: tuple[ApprovalSubjectSpec, ...] = (
    ApprovalSubjectSpec("task", "start a task whose kind requires approval"),
    ApprovalSubjectSpec("plan", "approve an immutable experiment plan revision"),
    ApprovalSubjectSpec("plan_revision_widening", "widen search space / change metric, data or code"),
    ApprovalSubjectSpec("final_eval", "one-shot evaluation on the sealed test set", reauth_when_remote=True),
    ApprovalSubjectSpec("effect_plan", "apply file/external side effects", reauth_when_remote=True, ttl_hours=2),
    ApprovalSubjectSpec("spend_over_budget", "model/compute spend beyond the per-task budget"),
    ApprovalSubjectSpec("premium_call", "premium model call while the premium switch is off"),
    ApprovalSubjectSpec("plugin_install", "install or enable a plugin", reauth_when_remote=True),
    ApprovalSubjectSpec("connector_enable", "enable an external connector / credential", reauth_when_remote=True),
    ApprovalSubjectSpec("external_publish", "publish or upload outside this computer", reauth_when_remote=True),
    ApprovalSubjectSpec("research_checkpoint", "human checkpoint inside a research pipeline"),
    ApprovalSubjectSpec("knowledge_import", "import this exact selection of records (and open-licensed PDFs)"),
)


@dataclass(frozen=True)
class RegisteredSubject:
    spec: ApprovalSubjectSpec
    resolver: HashResolver | None


class SubjectRegistry:
    def __init__(self, specs: Iterable[ApprovalSubjectSpec] = CORE_SUBJECTS) -> None:
        self._s: dict[str, RegisteredSubject] = {s.name: RegisteredSubject(s, None) for s in specs}
        self._frozen = False

    def add(self, spec: ApprovalSubjectSpec, resolver: HashResolver | None = None) -> None:
        if self._frozen:
            raise RuntimeError("registry is frozen after startup")
        if spec.name in self._s:
            raise ValueError(f"duplicate approval subject {spec.name}")
        self._s[spec.name] = RegisteredSubject(spec, resolver)

    def set_resolver(self, name: str, resolver: HashResolver) -> None:
        if self._frozen:
            raise RuntimeError("registry is frozen after startup")
        self._s[name] = RegisteredSubject(self._s[name].spec, resolver)

    def freeze(self) -> SubjectRegistry:
        self._frozen = True
        return self

    def get(self, name: str) -> RegisteredSubject | None:
        return self._s.get(name)

    def all(self) -> list[ApprovalSubjectSpec]:
        return [r.spec for r in self._s.values()]
