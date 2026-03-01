from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

from linear_mcp_fast.official_session import (
    DEFAULT_NOTION_MCP_URL,
    OfficialMcpSessionManager,
)
from linear_mcp_fast.router import ToolRouter

LINEAR_URL = "https://mcp.linear.app/mcp"
LINEAR_HASH = hashlib.md5(LINEAR_URL.encode()).hexdigest()  # noqa: S324
NOTION_URL = DEFAULT_NOTION_MCP_URL
NOTION_HASH = hashlib.md5(NOTION_URL.encode()).hexdigest()  # noqa: S324


class TestClearTokenCacheForUrl:
    def test_deletes_only_target_url_files(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".mcp-auth" / "mcp-remote-0.1.37"
        cache_dir.mkdir(parents=True)
        # Create files for both URLs
        for suffix in ("_tokens.json", "_client_info.json", "_code_verifier.txt"):
            (cache_dir / f"{NOTION_HASH}{suffix}").write_text("notion")
            (cache_dir / f"{LINEAR_HASH}{suffix}").write_text("linear")

        with patch.object(Path, "home", return_value=tmp_path):
            result = OfficialMcpSessionManager.clear_token_cache_for_url(NOTION_URL)

        assert result["deletedFiles"] == 3
        assert result["urlHash"] == NOTION_HASH
        # Linear files untouched
        for suffix in ("_tokens.json", "_client_info.json", "_code_verifier.txt"):
            assert (cache_dir / f"{LINEAR_HASH}{suffix}").exists()
            assert not (cache_dir / f"{NOTION_HASH}{suffix}").exists()

    def test_empty_dir_no_error(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".mcp-auth" / "mcp-remote-0.1.37"
        cache_dir.mkdir(parents=True)

        with patch.object(Path, "home", return_value=tmp_path):
            result = OfficialMcpSessionManager.clear_token_cache_for_url(NOTION_URL)

        assert result["deletedFiles"] == 0
        assert result["urlHash"] == NOTION_HASH

    def test_no_cache_dir_no_error(self, tmp_path: Path) -> None:
        with patch.object(Path, "home", return_value=tmp_path):
            result = OfficialMcpSessionManager.clear_token_cache_for_url(NOTION_URL)

        assert result["deletedFiles"] == 0
        assert result["searchedDirs"] == []

    def test_searches_multiple_versions(self, tmp_path: Path) -> None:
        mcp_auth = tmp_path / ".mcp-auth"
        d1 = mcp_auth / "mcp-remote-0.1.36"
        d2 = mcp_auth / "mcp-remote-0.1.37"
        d1.mkdir(parents=True)
        d2.mkdir(parents=True)
        (d1 / f"{NOTION_HASH}_tokens.json").write_text("old")
        (d2 / f"{NOTION_HASH}_tokens.json").write_text("new")

        with patch.object(Path, "home", return_value=tmp_path):
            result = OfficialMcpSessionManager.clear_token_cache_for_url(NOTION_URL)

        assert result["deletedFiles"] == 2
        assert len(result["searchedDirs"]) == 2


class TestRefactoredClearTokenCache:
    def test_instance_method_delegates_to_static(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".mcp-auth" / "mcp-remote-0.1.37"
        cache_dir.mkdir(parents=True)
        (cache_dir / f"{LINEAR_HASH}_tokens.json").write_text("token")

        mgr = OfficialMcpSessionManager(url=LINEAR_URL, transport="stdio")
        with patch.object(Path, "home", return_value=tmp_path):
            result = mgr._clear_token_cache()

        assert result["deletedFiles"] == 1
        assert result["urlHash"] == LINEAR_HASH
        assert not (cache_dir / f"{LINEAR_HASH}_tokens.json").exists()

    def test_static_and_instance_same_result(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".mcp-auth" / "mcp-remote-0.1.37"
        cache_dir.mkdir(parents=True)

        mgr = OfficialMcpSessionManager(url=LINEAR_URL, transport="stdio")
        with patch.object(Path, "home", return_value=tmp_path):
            instance_result = mgr._clear_token_cache()
            static_result = OfficialMcpSessionManager.clear_token_cache_for_url(LINEAR_URL)

        assert instance_result["urlHash"] == static_result["urlHash"]


class FakeReader:
    def is_degraded(self) -> bool:
        return False

    def ensure_fresh(self) -> None:
        pass

    def refresh_cache(self, force: bool = True) -> None:
        pass

    def get_health(self) -> dict[str, Any]:
        return {}


class FakeOfficial:
    def __init__(self) -> None:
        self.reauth_called = False

    def reauth(self) -> dict[str, Any]:
        self.reauth_called = True
        return {
            "status": "reauth_triggered",
            "message": "OAuth tokens cleared.",
            "deletedFiles": 0,
            "urlHash": LINEAR_HASH,
            "searchedDirs": [],
        }

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        return {}

    def list_tools(self) -> list[str]:
        return []

    def get_health(self) -> dict[str, Any]:
        return {}


class TestRouterReauthNotion:
    def test_reauth_notion_returns_status(self, tmp_path: Path) -> None:
        router = ToolRouter(FakeReader(), FakeOfficial(), coherence_window_seconds=30)  # type: ignore[arg-type]
        with patch.object(Path, "home", return_value=tmp_path):
            result = router.reauth_notion()

        assert result["status"] == "reauth_triggered"
        assert result["service"] == "notion"
        assert "urlHash" in result
        assert result["urlHash"] == NOTION_HASH

    def test_reauth_notion_env_override(self, tmp_path: Path) -> None:
        custom_url = "https://custom-notion.example.com/mcp"
        custom_hash = hashlib.md5(custom_url.encode()).hexdigest()  # noqa: S324

        router = ToolRouter(FakeReader(), FakeOfficial(), coherence_window_seconds=30)  # type: ignore[arg-type]
        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch.dict(os.environ, {"NOTION_OFFICIAL_MCP_URL": custom_url}),
        ):
            result = router.reauth_notion()

        assert result["urlHash"] == custom_hash

    def test_reauth_all_includes_both_services(self, tmp_path: Path) -> None:
        official = FakeOfficial()
        router = ToolRouter(FakeReader(), official, coherence_window_seconds=30)  # type: ignore[arg-type]
        with patch.object(Path, "home", return_value=tmp_path):
            result = router.reauth_all()

        assert result["status"] == "reauth_triggered"
        assert result["services"] == ["linear", "notion"]
        assert "linear" in result
        assert "notion" in result
        assert official.reauth_called

    def test_reauth_all_deletes_both_token_files(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".mcp-auth" / "mcp-remote-0.1.37"
        cache_dir.mkdir(parents=True)
        (cache_dir / f"{LINEAR_HASH}_tokens.json").write_text("linear")
        (cache_dir / f"{NOTION_HASH}_tokens.json").write_text("notion")

        mgr = OfficialMcpSessionManager(url=LINEAR_URL, transport="stdio")
        # Use real official for this test
        reader = FakeReader()

        class RealishOfficial:
            def reauth(self) -> dict[str, Any]:
                return mgr.reauth()

            def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
                return {}

            def get_health(self) -> dict[str, Any]:
                return {}

        router = ToolRouter(reader, RealishOfficial(), coherence_window_seconds=30)  # type: ignore[arg-type]
        with patch.object(Path, "home", return_value=tmp_path):
            result = router.reauth_all()

        assert not (cache_dir / f"{LINEAR_HASH}_tokens.json").exists()
        assert not (cache_dir / f"{NOTION_HASH}_tokens.json").exists()
        assert result["linear"]["deletedFiles"] >= 1
        assert result["notion"]["deletedFiles"] >= 1

    def test_reauth_notion_clears_token_files(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".mcp-auth" / "mcp-remote-0.1.37"
        cache_dir.mkdir(parents=True)
        for suffix in ("_tokens.json", "_client_info.json", "_code_verifier.txt"):
            (cache_dir / f"{NOTION_HASH}{suffix}").write_text("data")

        router = ToolRouter(FakeReader(), FakeOfficial(), coherence_window_seconds=30)  # type: ignore[arg-type]
        with patch.object(Path, "home", return_value=tmp_path):
            result = router.reauth_notion()

        assert result["deletedFiles"] == 3
        for suffix in ("_tokens.json", "_client_info.json", "_code_verifier.txt"):
            assert not (cache_dir / f"{NOTION_HASH}{suffix}").exists()
