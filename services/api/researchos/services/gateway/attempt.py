"""One paid model attempt: single in-flight slot, request preflight, reservation, provider call with the retry
policy split (HARDENING #9, #16), settlement. Every attempt leaves a model_calls row and a ledger entry."""
from __future__ import annotations

import random
import sqlite3
import time
from dataclasses import dataclass, replace
from typing import Any

from ...contracts.errors import ApiError
from ...contracts.gateway import ChatRequest, ChatResponse, Message, ModelProvider, ProviderError
from ...kernel.db import jdump, new_id, tx, utcnow
from ...kernel.events import emit
from . import ledger
from .catalog import AliasStatus

SLOT_WAIT_S = 30.0
MAX_RETRIES = 1  # baseline §8: max 1 retry


class GatewayError(ApiError):
    pass


@dataclass(frozen=True)
class AttemptCtx:
    task_type: str
    prompt_name: str
    prompt_hash: str
    project_id: str | None
    task_id: str | None
    trace_id: str | None
    routing_before: dict[str, Any]


def estimate_tokens(messages: tuple[Message, ...]) -> int:
    """Upper bound for the reservation (review #75): byte-level tokenizers emit at most one token per UTF-8 byte,
    plus a per-message allowance for role / special tokens. An ESTIMATE, never reported as usage."""
    return sum(len(m.content.encode("utf-8")) for m in messages) + 16 * len(messages)


def _record(conn: sqlite3.Connection, call_id: str, st: AliasStatus, ctx: AttemptCtx, status: str) -> None:
    with tx(conn):
        conn.execute("INSERT INTO model_calls(id,task_type,alias,provider,model_id,prompt_name,prompt_hash,status,"
                     "routing_before,project_id,task_id,trace_id,created_at,transport) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (call_id, ctx.task_type, st.alias, st.provider, st.model_id, ctx.prompt_name, ctx.prompt_hash,
                      status, jdump(ctx.routing_before), ctx.project_id, ctx.task_id, ctx.trace_id, utcnow(),
                      st.transport))


def _finish(conn: sqlite3.Connection, call_id: str, status: str, resp: ChatResponse | None, err: str | None,
            after: dict[str, Any] | None = None) -> None:
    with tx(conn):
        conn.execute("UPDATE model_calls SET status=?, input_tokens=?, output_tokens=?, error_code=?, finished_at=?, "
                     "routing_after=COALESCE(?, routing_after) WHERE id=?",
                     (status, resp.input_tokens if resp else None, resp.output_tokens if resp else None, err, utcnow(),
                      jdump(after) if after else None, call_id))


def _acquire(conn: sqlite3.Connection, holder: str, timeout_s: float) -> None:
    end = time.monotonic() + SLOT_WAIT_S
    while not ledger.acquire_slot(conn, holder, timeout_s + 30):
        if time.monotonic() > end:
            raise GatewayError(503, "ai_busy", "another AI call is in progress; try again shortly", retryable=True)
        time.sleep(0.2)


def run_attempt(conn: sqlite3.Connection, provider: ModelProvider, api_key: str, st: AliasStatus, req: ChatRequest,
                ctx: AttemptCtx, budget: ledger.Budget) -> tuple[str, ChatResponse]:
    call_id = new_id("call")
    # the limit the provider will actually send (e.g. the relay's minimum of 16): reserve and check that (review #77)
    floor = int(getattr(provider, "min_output_tokens", 1))
    if req.max_output_tokens < floor:
        req = replace(req, max_output_tokens=floor)
    est_in = estimate_tokens(req.messages)
    if st.context_window and est_in + req.max_output_tokens > st.context_window:
        _record(conn, call_id, st, ctx, "rejected")
        _finish(conn, call_id, "rejected", None, "request_too_large")
        raise GatewayError(413, "request_too_large", f"estimated {est_in} input tokens exceed the model context "
                           f"({st.context_window}); not sent (estimate, not measured usage)")
    assert st.price is not None and st.fx is not None
    ceiling = ledger.estimate_cny(st.price, st.fx, est_in, req.max_output_tokens)
    _record(conn, call_id, st, ctx, "routed")
    snapshot = {"price": {k: st.price[k] for k in ("currency", "input_per_mtok", "output_per_mtok", "effective_date",
                                                   "source")}, "fx_cny": st.fx, "est_input_tokens": est_in,
                "max_output_tokens": req.max_output_tokens, "estimate": True}
    _acquire(conn, call_id, req.timeout_s)  # slot first, so a busy wait never holds a reservation
    try:
        try:
            entry = ledger.reserve(conn, call_id=call_id, amount=ceiling, budget=budget, snapshot=snapshot,
                                   project_id=ctx.project_id, task_id=ctx.task_id)
        except ApiError as e:
            _finish(conn, call_id, "rejected", None, e.code)
            raise
        resp = _call_with_retry(conn, provider, api_key, req, st, call_id, entry)
        cost = ledger.actual_cny(st.price, st.fx, resp.input_tokens, resp.output_tokens)
        if cost > ceiling:  # billed beyond the reservation (usage above the bound): recorded loudly (review #75)
            with tx(conn):
                emit(conn, "budget.overshoot", entity_type="ledger", entity_id=entry, project_id=ctx.project_id,
                     data={"reserved_cny": ceiling, "actual_cny": cost, "call_id": call_id,
                           "input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens})
        return call_id, resp
    finally:
        ledger.release_slot(conn, call_id)


def _call_with_retry(conn: sqlite3.Connection, provider: ModelProvider, api_key: str, req: ChatRequest,
                     st: AliasStatus, call_id: str, entry: str) -> ChatResponse:
    with tx(conn):
        conn.execute("UPDATE model_calls SET status='in_flight' WHERE id=?", (call_id,))
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = provider.complete(req, api_key)
        except ProviderError as e:
            if e.maybe_billed:  # outcome unknown: keep the reservation, never retry automatically
                ledger.mark_unknown(conn, entry)
                _finish(conn, call_id, "unknown", None, e.kind)
                raise GatewayError(504, "ai_outcome_unknown", "the AI call timed out and may have been billed; "
                                   "cost kept as 'unknown' until reconciled") from e
            if e.kind in ("rate_limited", "server", "network") and attempt < MAX_RETRIES:
                time.sleep(min(e.retry_after_s or 1.0, 10.0) * (1 + random.random() * 0.25))  # backoff + jitter
                continue
            ledger.release(conn, entry)  # rejected before processing: not billed
            _finish(conn, call_id, "error", None, e.kind)
            raise GatewayError(502, f"ai_{e.kind}", str(e), retryable=e.kind in ("rate_limited", "server", "network")
                               ) from e
        assert st.price is not None and st.fx is not None
        ledger.settle(conn, entry, ledger.actual_cny(st.price, st.fx, resp.input_tokens, resp.output_tokens),
                      resp.response_id)
        _finish(conn, call_id, "ok", resp, None)
        return resp
    raise AssertionError("unreachable")
