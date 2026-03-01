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

    def ensure_fresh(self) -> None:
        pass

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


def test_stale_fallback_has_metadata_when_remote_down(monkeypatch: pytest.MonkeyPatch):
    """Degraded local + remote down -> result has _metadata.stale=True"""
    reader = FakeReader(degraded=True)
    official = FakeOfficial()
    official.exceptions["list_issues"] = OfficialToolError("official_down", "offline")

    def handler(_reader, **_kwargs):
        return {"source": "local-stale", "data": [1, 2, 3]}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    result = router.call_read("list_issues", {})

    assert result == {"source": "local-stale", "data": [1, 2, 3], "_metadata": {"stale": True}}
    assert result["_metadata"]["stale"] is True
    assert len(official.calls) == 1


def test_stale_fallback_has_metadata_in_remote_first_window(monkeypatch: pytest.MonkeyPatch):
    """Write -> remote fails during coherence -> stale local has _metadata.stale=True"""
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

    assert result == {"source": "local-stale", "_metadata": {"stale": True}}
    assert result["_metadata"]["stale"] is True
    assert len(official.calls) == 2
    assert official.calls[0][0] == "create_issue"
    assert official.calls[1][0] == "list_issues"


def test_fresh_local_read_has_no_metadata(monkeypatch: pytest.MonkeyPatch):
    """Healthy local -> no _metadata key"""
    reader = FakeReader(degraded=False)
    official = FakeOfficial()

    def handler(_reader, **_kwargs):
        return {"source": "local", "data": [1, 2, 3]}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    result = router.call_read("list_issues", {})

    assert result == {"source": "local", "data": [1, 2, 3]}
    assert "_metadata" not in result
    assert official.calls == []


def test_official_read_has_no_metadata(monkeypatch: pytest.MonkeyPatch):
    """Official fallback -> no _metadata key"""
    reader = FakeReader(degraded=False)
    official = FakeOfficial()
    official.responses["list_issues"] = {"source": "official", "data": [4, 5, 6]}

    def handler(_reader, **_kwargs):
        raise local_handlers.LocalFallbackRequested("unsupported_filter", "unsupported")

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    result = router.call_read("list_issues", {})

    assert result == {"source": "official", "data": [4, 5, 6]}
    assert "_metadata" not in result
    assert len(official.calls) == 1


def test_stale_metadata_with_list_response(monkeypatch: pytest.MonkeyPatch):
    """Handler returns list -> result is wrapped with _metadata"""
    reader = FakeReader(degraded=True)
    official = FakeOfficial()
    official.exceptions["list_issues"] = OfficialToolError("official_down", "offline")

    def handler(_reader, **_kwargs):
        return [{"id": "1"}, {"id": "2"}, {"id": "3"}]

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    result = router.call_read("list_issues", {})

    assert isinstance(result, dict)
    assert "results" in result
    assert result["results"] == [{"id": "1"}, {"id": "2"}, {"id": "3"}]
    assert result["_metadata"]["stale"] is True
    assert len(official.calls) == 1


def test_stale_metadata_injection_preserves_dict_structure(monkeypatch: pytest.MonkeyPatch):
    """Stale dict responses preserve all original fields"""
    reader = FakeReader(degraded=True)
    official = FakeOfficial()
    official.exceptions["list_issues"] = OfficialToolError("official_down", "offline")

    def handler(_reader, **_kwargs):
        return {
            "issues": [{"id": "ISS-1", "title": "Bug"}],
            "pageInfo": {"hasNextPage": False},
            "timestamp": 1234567890,
        }

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    result = router.call_read("list_issues", {})

    assert result["issues"] == [{"id": "ISS-1", "title": "Bug"}]
    assert result["pageInfo"] == {"hasNextPage": False}
    assert result["timestamp"] == 1234567890
    assert result["_metadata"]["stale"] is True


def test_stale_metadata_not_added_when_remote_succeeds_during_coherence(
    monkeypatch: pytest.MonkeyPatch,
):
    """Remote-first window with successful remote call -> no stale metadata"""
    reader = FakeReader(degraded=False)
    official = FakeOfficial()
    official.responses["create_issue"] = {"id": "ISS-1"}
    official.responses["list_issues"] = {"source": "remote", "data": [7, 8, 9]}

    def handler(_reader, **_kwargs):
        return {"source": "local"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    router.call_official("create_issue", {"title": "T"})
    result = router.call_read("list_issues", {})

    assert result == {"source": "remote", "data": [7, 8, 9]}
    assert "_metadata" not in result


def test_stale_metadata_not_added_when_local_healthy_and_remote_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    """Healthy local with remote unavailable -> local returned without stale metadata"""
    reader = FakeReader(degraded=False)
    official = FakeOfficial()
    official.exceptions["list_issues"] = OfficialToolError("official_down", "offline")

    def handler(_reader, **_kwargs):
        return {"source": "local-healthy"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    result = router.call_read("list_issues", {})

    assert result == {"source": "local-healthy"}
    assert "_metadata" not in result
    # When local is healthy, it succeeds immediately and official is never called
    assert len(official.calls) == 0


def test_stale_metadata_only_added_for_degraded_fallback(monkeypatch: pytest.MonkeyPatch):
    """Stale metadata only added when specifically returning degraded data as fallback"""
    reader = FakeReader(degraded=True)
    official = FakeOfficial()
    official.exceptions["list_issues"] = OfficialToolError("official_down", "offline")

    call_count = [0]

    def handler(_reader, **_kwargs):
        call_count[0] += 1
        return {"source": "degraded-fallback"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    result = router.call_read("list_issues", {})

    assert result == {"source": "degraded-fallback", "_metadata": {"stale": True}}
    # Handler called once with allow_degraded=True
    assert call_count[0] == 1


def test_stale_metadata_with_empty_list_response(monkeypatch: pytest.MonkeyPatch):
    """Handler returns empty list -> wrapped with _metadata"""
    reader = FakeReader(degraded=True)
    official = FakeOfficial()
    official.exceptions["list_issues"] = OfficialToolError("official_down", "offline")

    def handler(_reader, **_kwargs):
        return []

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    result = router.call_read("list_issues", {})

    assert isinstance(result, dict)
    assert result["results"] == []
    assert result["_metadata"]["stale"] is True


def test_stale_metadata_with_nested_structures(monkeypatch: pytest.MonkeyPatch):
    """Stale metadata preserves complex nested structures"""
    reader = FakeReader(degraded=True)
    official = FakeOfficial()
    official.exceptions["list_issues"] = OfficialToolError("official_down", "offline")

    def handler(_reader, **_kwargs):
        return {
            "nested": {
                "level1": {
                    "level2": {
                        "data": [1, 2, 3],
                        "info": "preserved",
                    }
                }
            }
        }

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    result = router.call_read("list_issues", {})

    assert result["nested"]["level1"]["level2"]["data"] == [1, 2, 3]
    assert result["nested"]["level1"]["level2"]["info"] == "preserved"
    assert result["_metadata"]["stale"] is True


def test_stale_metadata_multiple_sequential_reads(monkeypatch: pytest.MonkeyPatch):
    """Multiple degraded reads all get stale metadata"""
    reader = FakeReader(degraded=True)
    official = FakeOfficial()
    official.exceptions["list_issues"] = OfficialToolError("official_down", "offline")

    def handler(_reader, **_kwargs):
        return {"source": "local-stale"}

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)

    result1 = router.call_read("list_issues", {})
    result2 = router.call_read("list_issues", {})

    assert result1["_metadata"]["stale"] is True
    assert result2["_metadata"]["stale"] is True
    assert len(official.calls) == 2


def test_stale_metadata_does_not_mutate_original_dict(monkeypatch: pytest.MonkeyPatch):
    """_inject_stale_metadata must not mutate the dict returned by the handler"""
    reader = FakeReader(degraded=True)
    official = FakeOfficial()
    official.exceptions["list_issues"] = OfficialToolError("official_down", "offline")

    original = {"source": "local-stale", "data": [1, 2, 3]}

    def handler(_reader, **_kwargs):
        return original

    _install_local_handler(monkeypatch, handler)

    router = ToolRouter(reader, official, coherence_window_seconds=30)
    result = router.call_read("list_issues", {})

    assert result["_metadata"]["stale"] is True
    assert "_metadata" not in original
