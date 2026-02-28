from __future__ import annotations

import asyncio

import pytest

from linear_mcp_fast.official_session import (
    DEFAULT_OFFICIAL_MCP_URL,
    OfficialToolError,
    OfficialMcpSessionManager,
)


@pytest.fixture(autouse=True)
def _clear_official_env(monkeypatch: pytest.MonkeyPatch):
    keys = [
        "LINEAR_OFFICIAL_MCP_TRANSPORT",
        "LINEAR_OFFICIAL_MCP_COMMAND",
        "LINEAR_OFFICIAL_MCP_ARGS",
        "LINEAR_OFFICIAL_MCP_ENV",
        "LINEAR_OFFICIAL_MCP_CWD",
        "LINEAR_OFFICIAL_MCP_URL",
        "LINEAR_OFFICIAL_MCP_HEADERS",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_default_transport_uses_stdio(monkeypatch: pytest.MonkeyPatch):
    manager = OfficialMcpSessionManager()

    health = manager.get_health()
    assert health["transport"] == "stdio"
    assert health["command"] == "npx"
    assert health["args"] == ["-y", "mcp-remote", DEFAULT_OFFICIAL_MCP_URL]


def test_stdio_args_support_json_array(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "LINEAR_OFFICIAL_MCP_ARGS",
        '["-y", "mcp-remote", "https://example.com/mcp", "--foo", "bar"]',
    )
    manager = OfficialMcpSessionManager()

    health = manager.get_health()
    assert health["args"] == [
        "-y",
        "mcp-remote",
        "https://example.com/mcp",
        "--foo",
        "bar",
    ]


def test_stdio_args_support_shell_style_string(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "LINEAR_OFFICIAL_MCP_ARGS",
        "-y mcp-remote https://example.com/mcp --name 'My Client'",
    )
    manager = OfficialMcpSessionManager()

    health = manager.get_health()
    assert health["args"] == [
        "-y",
        "mcp-remote",
        "https://example.com/mcp",
        "--name",
        "My Client",
    ]


def test_stdio_args_invalid_shell_string_falls_back_to_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LINEAR_OFFICIAL_MCP_ARGS", "-y mcp-remote 'unterminated")
    manager = OfficialMcpSessionManager()

    health = manager.get_health()
    assert health["args"] == ["-y", "mcp-remote", DEFAULT_OFFICIAL_MCP_URL]


def test_http_transport_keeps_legacy_header_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LINEAR_OFFICIAL_MCP_TRANSPORT", "http")
    monkeypatch.setenv("LINEAR_OFFICIAL_MCP_HEADERS", '{"Authorization": "Bearer X"}')

    manager = OfficialMcpSessionManager()
    health = manager.get_health()

    assert health["transport"] == "http"
    assert health["url"] == DEFAULT_OFFICIAL_MCP_URL
    assert health["hasHeaders"] is True


def test_invalid_transport_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LINEAR_OFFICIAL_MCP_TRANSPORT", "invalid")
    with pytest.raises(ValueError):
        OfficialMcpSessionManager()


def test_module_alias_runs_main():
    from oh_my_linearmcp import main as alias_main
    from linear_mcp_fast import main as base_main

    assert alias_main is base_main


class _FakeText:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _FakeResult:
    def __init__(self, *, is_error: bool = False, text: str = "", tools: list[object] | None = None):
        self.isError = is_error
        self.content = [_FakeText(text)] if text else []
        self.tools = tools or []


class _FakeTool:
    def __init__(self, name: str):
        self.name = name


def _sync_submit(value):
    if asyncio.iscoroutine(value):
        return asyncio.run(value)
    return value


def test_call_tool_preserves_official_tool_error(monkeypatch: pytest.MonkeyPatch):
    manager = OfficialMcpSessionManager()

    class _FakeSession:
        def call_tool(self, name: str, arguments=None):
            return _FakeResult(is_error=True, text="official said no")

    manager._session = _FakeSession()
    monkeypatch.setattr(manager, "_ensure_connected", lambda: None)
    monkeypatch.setattr(manager, "_submit", _sync_submit)

    with pytest.raises(OfficialToolError) as exc_info:
        manager.call_tool("list_issues", {})

    assert exc_info.value.code == "official_tool_error"
    assert "official said no" in exc_info.value.message


def test_list_tools_retries_once(monkeypatch: pytest.MonkeyPatch):
    manager = OfficialMcpSessionManager()
    calls = {"count": 0}

    class _FakeSession:
        def list_tools(self):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("transient failure")
            return _FakeResult(tools=[_FakeTool("create_issue"), _FakeTool("list_issues")])

    manager._session = _FakeSession()
    monkeypatch.setattr(manager, "_ensure_connected", lambda: None)
    monkeypatch.setattr(manager, "_submit", _sync_submit)
    async def _noop_disconnect():
        return None
    monkeypatch.setattr(manager, "_disconnect_async", _noop_disconnect)

    tools = manager.list_tools()

    assert tools == ["create_issue", "list_issues"]
    assert calls["count"] == 2
