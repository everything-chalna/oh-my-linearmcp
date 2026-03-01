from __future__ import annotations

from typing import Any

import pytest

from linear_mcp_fast import local_handlers
from linear_mcp_fast.router import ToolRouter


class FakeReader:
    def __init__(self):
        self.degraded = False
        self.ensure_fresh_calls = 0

    def is_degraded(self) -> bool:
        return self.degraded

    def ensure_fresh(self) -> None:
        self.ensure_fresh_calls += 1

    def refresh_cache(self, force: bool = True) -> None:
        pass

    def get_health(self) -> dict[str, Any]:
        return {"degraded": self.degraded}


class FakeOfficial:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, Any] = {}

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        args = arguments or {}
        self.calls.append((name, args))
        return self.responses.get(name, {"ok": True})

    def get_health(self) -> dict[str, Any]:
        return {"connected": True}


def _install_local_handler(monkeypatch: pytest.MonkeyPatch, fn):
    monkeypatch.setitem(local_handlers.LOCAL_READ_HANDLERS, "list_issues", fn)


def test_call_read_invokes_ensure_fresh(monkeypatch: pytest.MonkeyPatch):
    reader = FakeReader()
    official = FakeOfficial()

    def handler(_reader, **_kwargs):
        return {"source": "local"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)  # type: ignore[arg-type]
    router.call_read("list_issues", {})

    assert reader.ensure_fresh_calls == 1


def test_call_official_invokes_ensure_fresh(monkeypatch: pytest.MonkeyPatch):
    reader = FakeReader()
    official = FakeOfficial()

    router = ToolRouter(reader, official, coherence_window_seconds=30)  # type: ignore[arg-type]
    router.call_official("create_issue", {"title": "T"})

    assert reader.ensure_fresh_calls == 1


def test_write_then_read_calls_ensure_fresh_for_each_entry(monkeypatch: pytest.MonkeyPatch):
    """call_official(write) + call_read(read) = ensure_fresh at each router entry point.

    Note: call_read during remote-first window internally calls call_official,
    which adds an extra ensure_fresh call (3 total, not 2).
    """
    reader = FakeReader()
    official = FakeOfficial()
    official.responses["create_issue"] = {"id": "ISS-1"}
    official.responses["list_issues"] = {"source": "remote"}

    def handler(_reader, **_kwargs):
        return {"source": "local"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)  # type: ignore[arg-type]
    router.call_official("create_issue", {"title": "T"})
    router.call_read("list_issues", {})

    # 1: call_official(create_issue), 2: call_read(list_issues), 3: internal call_official(list_issues) via remote-first
    assert reader.ensure_fresh_calls == 3


def test_multiple_reads_call_ensure_fresh_each_time(monkeypatch: pytest.MonkeyPatch):
    reader = FakeReader()
    official = FakeOfficial()

    def handler(_reader, **_kwargs):
        return {"source": "local"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)  # type: ignore[arg-type]
    router.call_read("list_issues", {})
    router.call_read("list_issues", {})
    router.call_read("list_issues", {})

    assert reader.ensure_fresh_calls == 3
