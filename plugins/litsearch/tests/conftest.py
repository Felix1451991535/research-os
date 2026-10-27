"""Deny live networking for every plugin test."""

import socket
from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def deny(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Live networking forbidden in offline tests")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    yield
