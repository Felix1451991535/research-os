"""K2 Orchestration contract: the single Task/Run state machine and task-kind specification."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class TaskState(StrEnum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    BLOCKED = "blocked"


TERMINAL: frozenset[TaskState] = frozenset(
    {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED, TaskState.INTERRUPTED})

# Allowed transitions. Enforced server-side; clients can never request SUCCEEDED.
TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.DRAFT: frozenset({TaskState.AWAITING_APPROVAL, TaskState.QUEUED, TaskState.CANCELLED}),
    TaskState.AWAITING_APPROVAL: frozenset({TaskState.QUEUED, TaskState.CANCELLED, TaskState.BLOCKED}),
    TaskState.QUEUED: frozenset({TaskState.RUNNING, TaskState.CANCELLED, TaskState.BLOCKED}),
    TaskState.RUNNING: frozenset({TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLING,
                                  TaskState.INTERRUPTED, TaskState.QUEUED, TaskState.BLOCKED}),
    TaskState.CANCELLING: frozenset({TaskState.CANCELLED, TaskState.FAILED, TaskState.SUCCEEDED,
                                     TaskState.INTERRUPTED}),
    TaskState.BLOCKED: frozenset({TaskState.QUEUED, TaskState.CANCELLED, TaskState.FAILED}),
    TaskState.SUCCEEDED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
    TaskState.INTERRUPTED: frozenset({TaskState.QUEUED}),  # explicit user retry only
}


def can_transition(src: TaskState, dst: TaskState) -> bool:
    return dst in TRANSITIONS[src]


class ResourceClass(StrEnum):
    CPU = "cpu"
    GPU = "gpu"          # serial: at most one running
    LLM = "llm"          # one paid call in flight
    NETWORK = "network"
    LIGHT = "light"      # bookkeeping, no limit


class RetryPolicy(StrEnum):
    NONE = "none"              # lease loss -> interrupted, never auto-rerun
    IDEMPOTENT = "idempotent"  # lease loss -> requeue with backoff while attempts remain


class TaskContext(Protocol):
    """Handed to a task handler by the worker."""

    task_id: str
    run_id: str            # additive (core 1.1): id of the current run row, for run-scoped artifacts
    payload: dict[str, Any]

    def progress(self, stage: str, data: dict[str, Any] | None = None) -> None: ...

    def cancel_requested(self) -> bool: ...

    def check_lease(self) -> bool:
        """False if the lease was lost (fenced); the handler must stop without writing results."""
        ...


@dataclass(frozen=True)
class TaskOutcome:
    state: TaskState  # SUCCEEDED | FAILED | CANCELLED
    result: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None


TaskHandler = Callable[[TaskContext], TaskOutcome]


@dataclass(frozen=True)
class TaskKindSpec:
    name: str
    description: str
    resource_class: ResourceClass = ResourceClass.LIGHT
    retry: RetryPolicy = RetryPolicy.NONE
    max_attempts: int = 1
    approval_subject: str | None = None   # if set, task waits in AWAITING_APPROVAL
    payload_schema: dict[str, Any] = field(default_factory=dict)  # JSON Schema (validated on create)
    handler: TaskHandler | None = None     # executed by the worker
