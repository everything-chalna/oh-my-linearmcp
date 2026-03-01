from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import patch

from linear_mcp_fast.official_session import OfficialMcpSessionManager


URL = "https://mcp.linear.app/mcp"
URL_HASH = hashlib.md5(URL.encode()).hexdigest()  # noqa: S324


class TestFindTokenCacheDirs:
    def test_returns_dirs_when_exist(self, tmp_path: Path) -> None:
        mcp_auth = tmp_path / ".mcp-auth"
        d1 = mcp_auth / "mcp-remote-0.1.37"
        d1.mkdir(parents=True)
        with patch.object(Path, "home", return_value=tmp_path):
            result = OfficialMcpSessionManager._find_token_cache_dirs()
        assert len(result) == 1
        assert result[0] == d1

    def test_returns_empty_when_no_mcp_auth(self, tmp_path: Path) -> None:
        with patch.object(Path, "home", return_value=tmp_path):
            result = OfficialMcpSessionManager._find_token_cache_dirs()
        assert result == []

    def test_returns_multiple_versions_sorted(self, tmp_path: Path) -> None:
        mcp_auth = tmp_path / ".mcp-auth"
        (mcp_auth / "mcp-remote-0.1.36").mkdir(parents=True)
        (mcp_auth / "mcp-remote-0.1.37").mkdir(parents=True)
        (mcp_auth / "other-dir").mkdir(parents=True)  # should be excluded
        with patch.object(Path, "home", return_value=tmp_path):
            result = OfficialMcpSessionManager._find_token_cache_dirs()
        assert len(result) == 2
        assert "0.1.36" in str(result[0])
        assert "0.1.37" in str(result[1])


class TestClearTokenCache:
    def _make_manager(self) -> OfficialMcpSessionManager:
        return OfficialMcpSessionManager(url=URL, transport="stdio")

    def test_deletes_matching_token_files(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".mcp-auth" / "mcp-remote-0.1.37"
        cache_dir.mkdir(parents=True)
        for suffix in ("_tokens.json", "_client_info.json", "_code_verifier.txt"):
            (cache_dir / f"{URL_HASH}{suffix}").write_text("test")
        # Also add unrelated file that should NOT be deleted
        (cache_dir / "other_hash_tokens.json").write_text("keep")

        mgr = self._make_manager()
        with patch.object(Path, "home", return_value=tmp_path):
            result = mgr._clear_token_cache(full=True)

        assert result["deletedFiles"] == 3
        assert result["urlHash"] == URL_HASH
        assert not (cache_dir / f"{URL_HASH}_tokens.json").exists()
        assert (cache_dir / "other_hash_tokens.json").exists()  # untouched

    def test_no_error_when_no_files(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".mcp-auth" / "mcp-remote-0.1.37"
        cache_dir.mkdir(parents=True)
        mgr = self._make_manager()
        with patch.object(Path, "home", return_value=tmp_path):
            result = mgr._clear_token_cache()
        assert result["deletedFiles"] == 0

    def test_no_error_when_no_cache_dir(self, tmp_path: Path) -> None:
        mgr = self._make_manager()
        with patch.object(Path, "home", return_value=tmp_path):
            result = mgr._clear_token_cache()
        assert result["deletedFiles"] == 0
        assert result["searchedDirs"] == []


class TestReauth:
    def _make_manager(self) -> OfficialMcpSessionManager:
        return OfficialMcpSessionManager(url=URL, transport="stdio")

    def test_reauth_returns_status(self, tmp_path: Path) -> None:
        mgr = self._make_manager()
        with patch.object(Path, "home", return_value=tmp_path):
            result = mgr.reauth()
        assert result["status"] == "reauth_triggered"
        assert "deletedFiles" in result
        assert "urlHash" in result

    def test_reauth_disconnects_existing_session(self, tmp_path: Path) -> None:
        mgr = self._make_manager()
        # Simulate having a session
        disconnect_called = [False]
        original_submit = mgr._submit

        async def fake_disconnect() -> None:
            disconnect_called[0] = True
            mgr._session = None
            mgr._session_cm = None
            mgr._transport_cm = None

        mgr._session = "fake_session"  # type: ignore[assignment]
        mgr._ensure_loop()

        with patch.object(Path, "home", return_value=tmp_path):
            with patch.object(mgr, "_disconnect_async", side_effect=fake_disconnect):
                with patch.object(mgr, "_submit", side_effect=lambda coro: original_submit(coro)):
                    result = mgr.reauth()

        assert result["status"] == "reauth_triggered"
        assert mgr._session is None

    def test_reauth_clears_token_files(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".mcp-auth" / "mcp-remote-0.1.37"
        cache_dir.mkdir(parents=True)
        (cache_dir / f"{URL_HASH}_tokens.json").write_text("token")

        mgr = self._make_manager()
        with patch.object(Path, "home", return_value=tmp_path):
            result = mgr.reauth()

        assert result["deletedFiles"] == 1
        assert not (cache_dir / f"{URL_HASH}_tokens.json").exists()


    def test_reauth_handles_disconnect_failure(self, tmp_path: Path) -> None:
        """reauth succeeds even if disconnect raises."""
        mgr = self._make_manager()
        mgr._session = "fake"  # type: ignore[assignment]
        mgr._ensure_loop()

        async def exploding_disconnect() -> None:
            raise RuntimeError("disconnect boom")

        with patch.object(Path, "home", return_value=tmp_path):
            with patch.object(mgr, "_disconnect_async", side_effect=exploding_disconnect):
                result = mgr.reauth()

        assert result["status"] == "reauth_triggered"


class TestRouterReauth:
    def test_router_reauth_delegates(self) -> None:
        # This tests that ToolRouter.reauth_official() delegates to official.reauth()
        from linear_mcp_fast.router import ToolRouter

        class FakeReader:
            def is_degraded(self) -> bool:
                return False

            def refresh_cache(self, force: bool = True) -> None:
                pass

            def get_health(self) -> dict[str, Any]:
                return {}

        class FakeOfficial:
            def __init__(self) -> None:
                self.reauth_called = False

            def reauth(self) -> dict[str, Any]:
                self.reauth_called = True
                return {"status": "reauth_triggered", "deletedFiles": 0, "urlHash": "abc", "searchedDirs": []}

            def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
                return {}

            def list_tools(self) -> list[str]:
                return []

            def get_health(self) -> dict[str, Any]:
                return {}

        reader = FakeReader()
        official = FakeOfficial()
        router = ToolRouter(reader, official)  # type: ignore[arg-type]
        result = router.reauth_official()
        assert official.reauth_called
        assert result["status"] == "reauth_triggered"
