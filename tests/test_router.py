from __future__ import annotations

from typing import Any

import pytest

from linear_mcp_fast import local_handlers
from linear_mcp_fast.official_session import OfficialToolError
from linear_mcp_fast.router import ToolRouter


class FakeReader:
    def __init__(self, degraded: bool = False):
        self.degraded = degraded
        self.refresh_count = 0

    def is_degraded(self) -> bool:
        return self.degraded

    def refresh_cache(self, force: bool = True) -> None:
        self.refresh_count += 1

    def get_health(self) -> dict[str, Any]:
        return {"degraded": self.degraded, "refreshCount": self.refresh_count}


class FakeOfficial:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, Any] = {}
        self.exceptions: dict[str, Exception] = {}

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        args = arguments or {}
        self.calls.append((name, args))
        if name in self.exceptions:
            raise self.exceptions[name]
        return self.responses.get(name, {"ok": True, "tool": name, "args": args})

    def list_tools(self) -> list[str]:
        return ["create_issue", "list_issues"]

    def get_health(self) -> dict[str, Any]:
        return {"connected": True}


def _install_local_handler(monkeypatch: pytest.MonkeyPatch, fn):
    monkeypatch.setitem(local_handlers.LOCAL_READ_HANDLERS, "list_issues", fn)


def test_read_local_success_without_official_call(monkeypatch: pytest.MonkeyPatch):
    reader = FakeReader(degraded=False)
    official = FakeOfficial()

    def handler(_reader, **_kwargs):
        return {"source": "local"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    result = router.call_read("list_issues", {})

    assert result == {"source": "local"}
    assert official.calls == []


def test_read_local_unsupported_falls_back_to_official(monkeypatch: pytest.MonkeyPatch):
    reader = FakeReader(degraded=False)
    official = FakeOfficial()

    def handler(_reader, **_kwargs):
        raise local_handlers.LocalFallbackRequested("unsupported_filter", "unsupported")

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    result = router.call_read("list_issues", {"query": "hello"})

    assert result["tool"] == "list_issues"
    assert official.calls == [("list_issues", {"query": "hello"})]


def test_degraded_local_uses_official_when_available(monkeypatch: pytest.MonkeyPatch):
    reader = FakeReader(degraded=True)
    official = FakeOfficial()

    def handler(_reader, **_kwargs):
        return {"source": "local"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    result = router.call_read("list_issues", {})

    assert result["tool"] == "list_issues"
    assert official.calls == [("list_issues", {})]


def test_degraded_local_returns_stale_local_if_remote_down(monkeypatch: pytest.MonkeyPatch):
    reader = FakeReader(degraded=True)
    official = FakeOfficial()
    official.exceptions["list_issues"] = OfficialToolError("official_down", "offline")

    def handler(_reader, **_kwargs):
        return {"source": "local-stale"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    result = router.call_read("list_issues", {})

    assert result == {"source": "local-stale"}
    assert len(official.calls) == 1


def test_unexpected_local_error_falls_back_to_official(monkeypatch: pytest.MonkeyPatch):
    reader = FakeReader(degraded=False)
    official = FakeOfficial()

    def handler(_reader, **_kwargs):
        raise RuntimeError("boom")

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    result = router.call_read("list_issues", {})

    assert result["tool"] == "list_issues"
    assert len(official.calls) == 1


def test_write_marks_coherence_and_uses_remote_first(monkeypatch: pytest.MonkeyPatch):
    reader = FakeReader(degraded=False)
    official = FakeOfficial()
    official.responses["create_issue"] = {"id": "ISS-1"}
    official.responses["list_issues"] = {"source": "remote"}

    def handler(_reader, **_kwargs):
        return {"source": "local"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    router.call_official("create_issue", {"title": "T"})
    result = router.call_read("list_issues", {})

    assert result == {"source": "remote"}
    assert official.calls[0][0] == "create_issue"
    assert official.calls[1][0] == "list_issues"


def test_remote_first_falls_back_to_local_when_remote_fails(monkeypatch: pytest.MonkeyPatch):
    reader = FakeReader(degraded=False)
    official = FakeOfficial()
    official.responses["create_issue"] = {"id": "ISS-1"}
    official.exceptions["list_issues"] = OfficialToolError("official_down", "offline")

    def handler(_reader, **_kwargs):
        return {"source": "local"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    router.call_official("create_issue", {"title": "T"})
    result = router.call_read("list_issues", {})

    assert result == {"source": "local"}
    assert len(official.calls) == 2


def test_remote_first_tool_error_does_not_fallback_to_local(monkeypatch: pytest.MonkeyPatch):
    reader = FakeReader(degraded=False)
    official = FakeOfficial()
    official.responses["create_issue"] = {"id": "ISS-1"}
    official.exceptions["list_issues"] = OfficialToolError("official_tool_error", "bad args")

    def handler(_reader, **_kwargs):
        return {"source": "local"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    router.call_official("create_issue", {"title": "T"})
    with pytest.raises(OfficialToolError) as exc_info:
        router.call_read("list_issues", {})

    assert exc_info.value.code == "official_tool_error"
    assert len(official.calls) == 2


def test_remote_first_degraded_local_does_not_retry_official_twice(monkeypatch: pytest.MonkeyPatch):
    reader = FakeReader(degraded=True)
    official = FakeOfficial()
    official.responses["create_issue"] = {"id": "ISS-1"}
    official.exceptions["list_issues"] = OfficialToolError("official_unavailable", "offline")

    def handler(_reader, **_kwargs):
        return {"source": "local-stale"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    router.call_official("create_issue", {"title": "T"})
    result = router.call_read("list_issues", {})

    assert result == {"source": "local-stale"}
    assert len(official.calls) == 2


def test_non_write_official_call_does_not_force_remote_first(monkeypatch: pytest.MonkeyPatch):
    reader = FakeReader(degraded=False)
    official = FakeOfficial()

    def handler(_reader, **_kwargs):
        return {"source": "local"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    router.call_official("list_teams", {})
    result = router.call_read("list_issues", {})

    assert result == {"source": "local"}


def test_refresh_local_cache_returns_local_health(monkeypatch: pytest.MonkeyPatch):
    reader = FakeReader(degraded=False)
    official = FakeOfficial()

    def handler(_reader, **_kwargs):
        return {"source": "local"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    health = router.refresh_local_cache()

    assert health["refreshCount"] == 1


def test_router_health_includes_local_and_official(monkeypatch: pytest.MonkeyPatch):
    reader = FakeReader(degraded=False)
    official = FakeOfficial()

    def handler(_reader, **_kwargs):
        return {"source": "local"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    health = router.get_health()

    assert "local" in health
    assert "official" in health
    assert health["coherenceWindowSeconds"] == 30
