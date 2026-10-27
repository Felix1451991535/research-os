"""Deterministic mock provider for tests and offline demos. Never used unless explicitly injected
(tests / dev_mode); real providers are the only ones registered in production wiring."""
from __future__ import annotations

from collections.abc import Callable

from ....contracts.gateway import ChatRequest, ChatResponse, ProviderError

Responder = Callable[[ChatRequest], str | ProviderError]


class MockProvider:
    location = "local"  # nothing leaves the machine (egress gate, ADR-159)

    def __init__(self, name: str, responder: Responder) -> None:
        self.name = name
        self.responder = responder
        self.calls: list[ChatRequest] = []

    def complete(self, req: ChatRequest, api_key: str) -> ChatResponse:
        self.calls.append(req)
        out = self.responder(req)
        if isinstance(out, ProviderError):
            raise out
        prompt_chars = sum(len(m.content) for m in req.messages)
        return ChatResponse(text=out, input_tokens=max(1, prompt_chars // 3), output_tokens=max(1, len(out) // 3),
                            response_id=f"mock-{len(self.calls)}-{abs(hash(out)) % 10**8}", model_id=req.model_id,
                            raw_finish_reason="stop")
