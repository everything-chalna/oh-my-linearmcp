"""
Tool routing policy for unified local-fast + official Linear MCP access.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from . import local_handlers
from .official_session import OfficialMcpSessionManager, OfficialToolError
from .reader import LinearLocalReader

logger = logging.getLogger(__name__)

WRITE_TOOL_PREFIXES = (
    "create_",
    "update_",
    "delete_",
    "archive_",
    "unarchive_",
    "set_",
    "add_",
    "remove_",
    "move_",
)


class ToolRouter:
    """Routes tool calls between local cache handlers and official MCP."""

    def __init__(
        self,
        reader: LinearLocalReader,
        official: OfficialMcpSessionManager,
        coherence_window_seconds: int | None = None,
    ):
        self._reader = reader
        self._official = official
        self._coherence_window_seconds = coherence_window_seconds or int(
            os.getenv("LINEAR_FAST_COHERENCE_WINDOW_SECONDS", "30")
        )
        self._remote_reads_until = 0.0
        self._state_lock = threading.RLock()

    def _mark_recent_write(self) -> None:
        with self._state_lock:
            self._remote_reads_until = time.time() + self._coherence_window_seconds

    def _read_remote_first(self) -> bool:
        with self._state_lock:
            return time.time() < self._remote_reads_until

    def _is_probable_write_tool(self, tool_name: str) -> bool:
        if tool_name in local_handlers.LOCAL_READ_HANDLERS:
            return False
        return tool_name.startswith(WRITE_TOOL_PREFIXES)

    def _call_local(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        allow_degraded: bool = False,
    ) -> Any:
        handler = local_handlers.LOCAL_READ_HANDLERS.get(tool_name)
        if handler is None:
            raise local_handlers.LocalFallbackRequested(
                "unsupported_tool", f"tool '{tool_name}' not implemented in local cache"
            )

        if self._reader.is_degraded() and not allow_degraded:
            raise local_handlers.LocalFallbackRequested(
                "degraded_local", "local cache is degraded"
            )

        return handler(self._reader, **arguments)

    def call_official(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = self._official.call_tool(tool_name, arguments or {})
        if self._is_probable_write_tool(tool_name):
            self._mark_recent_write()
        return result

    def call_read(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        args = arguments or {}
        remote_error: OfficialToolError | None = None

        if self._read_remote_first():
            try:
                return self.call_official(tool_name, args)
            except OfficialToolError as exc:
                if exc.code == "official_tool_error":
                    raise
                logger.warning("Remote-first read failed for %s, falling back to local", tool_name)
                remote_error = exc

        try:
            return self._call_local(tool_name, args)
        except local_handlers.LocalFallbackRequested as local_exc:
            if remote_error is not None:
                if local_exc.code == "degraded_local":
                    logger.warning(
                        "Returning stale local for %s because remote failed during remote-first window",
                        tool_name,
                    )
                    return self._call_local(tool_name, args, allow_degraded=True)
                raise remote_error

            try:
                return self.call_official(tool_name, args)
            except OfficialToolError as official_exc:
                if official_exc.code == "official_tool_error":
                    raise
                if local_exc.code == "degraded_local":
                    # When remote is unavailable, stale local read is better than hard failure.
                    logger.warning(
                        "Returning stale local for %s because remote is unavailable and local is degraded",
                        tool_name,
                    )
                    return self._call_local(tool_name, args, allow_degraded=True)
                raise
        except Exception:
            logger.exception("Unexpected local error for %s", tool_name)
            return self.call_official(tool_name, args)

    def refresh_local_cache(self) -> dict[str, Any]:
        self._reader.refresh_cache(force=True)
        return self._reader.get_health()

    def get_health(self) -> dict[str, Any]:
        return {
            "local": self._reader.get_health(),
            "official": self._official.get_health(),
            "remoteReadUntil": self._remote_reads_until,
            "coherenceWindowSeconds": self._coherence_window_seconds,
        }
