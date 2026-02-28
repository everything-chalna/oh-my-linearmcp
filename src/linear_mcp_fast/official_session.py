"""
Official Linear MCP client session manager.

Maintains a long-lived MCP client session with reconnect and single-retry
semantics for transient failures.

Default transport uses the official `mcp-remote` stdio bridge so existing OAuth
flows (as used by Claude MCP config) are reused without custom token plumbing.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import shlex
import threading
import time
from datetime import timedelta
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)

DEFAULT_OFFICIAL_MCP_URL = "https://mcp.linear.app/mcp"
DEFAULT_TRANSPORT = "stdio"
DEFAULT_STDIO_COMMAND = "npx"
DEFAULT_STDIO_ARGS_PREFIX = ["-y", "mcp-remote"]


class OfficialToolError(RuntimeError):
    """Raised when the official MCP call fails."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class OfficialMcpSessionManager:
    """Thread-safe synchronous wrapper around async MCP client session."""

    def __init__(
        self,
        transport: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 30.0,
        sse_read_timeout_seconds: float = 300.0,
        read_timeout_seconds: float = 30.0,
    ):
        self._transport = (transport or os.getenv("LINEAR_OFFICIAL_MCP_TRANSPORT", DEFAULT_TRANSPORT)).lower()
        self._url = url or os.getenv("LINEAR_OFFICIAL_MCP_URL", DEFAULT_OFFICIAL_MCP_URL)
        self._headers = headers or self._parse_headers_from_env()
        self._command = command or os.getenv("LINEAR_OFFICIAL_MCP_COMMAND", DEFAULT_STDIO_COMMAND)
        self._args = args or self._parse_stdio_args_from_env(default_url=self._url)
        self._env = env or self._parse_stdio_env_from_env()
        self._cwd = cwd or os.getenv("LINEAR_OFFICIAL_MCP_CWD")
        self._timeout_seconds = timeout_seconds
        self._sse_read_timeout_seconds = sse_read_timeout_seconds
        self._read_timeout_seconds = read_timeout_seconds

        if self._transport not in {"stdio", "http"}:
            raise ValueError("LINEAR_OFFICIAL_MCP_TRANSPORT must be one of: stdio, http")

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

        self._transport_cm: Any = None
        self._session_cm: Any = None
        self._session: ClientSession | None = None

        self._failure_count = 0
        self._last_error: str | None = None
        self._last_failure_at: float | None = None
        self._last_connected_at: float | None = None

    @staticmethod
    def _parse_headers_from_env() -> dict[str, str] | None:
        raw = os.getenv("LINEAR_OFFICIAL_MCP_HEADERS")
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except Exception:
            logger.warning("Ignoring invalid LINEAR_OFFICIAL_MCP_HEADERS value")
        return None

    @staticmethod
    def _parse_stdio_env_from_env() -> dict[str, str] | None:
        raw = os.getenv("LINEAR_OFFICIAL_MCP_ENV")
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except Exception:
            logger.warning("Ignoring invalid LINEAR_OFFICIAL_MCP_ENV value")
        return None

    @staticmethod
    def _parse_stdio_args_from_env(default_url: str) -> list[str]:
        raw = os.getenv("LINEAR_OFFICIAL_MCP_ARGS")
        if not raw:
            return [*DEFAULT_STDIO_ARGS_PREFIX, default_url]

        # Prefer JSON array for exact argument boundaries.
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except Exception:
            pass

        # Fallback to shell-like parsing.
        try:
            return shlex.split(raw)
        except ValueError:
            logger.warning("Ignoring invalid LINEAR_OFFICIAL_MCP_ARGS value; using default args")
            return [*DEFAULT_STDIO_ARGS_PREFIX, default_url]

    def _ensure_loop(self) -> None:
        if self._loop and self._thread and self._thread.is_alive():
            return

        loop = asyncio.new_event_loop()

        def _run_loop() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(target=_run_loop, daemon=True, name="linear-official-mcp")
        thread.start()

        self._loop = loop
        self._thread = thread

    def _submit(self, coro: Any) -> Any:
        if not self._loop:
            raise RuntimeError("event loop not initialized")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=self._read_timeout_seconds + 10)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise

    async def _connect_async(self) -> None:
        if self._session is not None:
            return

        if self._transport == "stdio":
            params = StdioServerParameters(
                command=self._command,
                args=self._args,
                env=self._env,
                cwd=self._cwd,
            )
            self._transport_cm = stdio_client(params)
        else:
            self._transport_cm = streamablehttp_client(
                self._url,
                headers=self._headers,
                timeout=self._timeout_seconds,
                sse_read_timeout=self._sse_read_timeout_seconds,
                terminate_on_close=False,
            )
        transport_streams = await self._transport_cm.__aenter__()
        if len(transport_streams) == 3:
            read_stream, write_stream, _ = transport_streams
        elif len(transport_streams) == 2:
            read_stream, write_stream = transport_streams
        else:
            raise RuntimeError("official MCP transport returned unexpected stream tuple")

        self._session_cm = ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=self._read_timeout_seconds),
        )
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()
        self._last_connected_at = time.time()

    async def _disconnect_async(self) -> None:
        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception as exc:
                self._log_cleanup_exception("Official MCP session cleanup failed", exc)
        if self._transport_cm is not None:
            try:
                await self._transport_cm.__aexit__(None, None, None)
            except Exception as exc:
                self._log_cleanup_exception("Official MCP transport cleanup failed", exc)

        self._session = None
        self._session_cm = None
        self._transport_cm = None

    def _ensure_connected(self) -> None:
        self._ensure_loop()
        self._submit(self._connect_async())

    @staticmethod
    def _log_cleanup_exception(prefix: str, exc: Exception) -> None:
        message = str(exc)
        if "Attempted to exit cancel scope in a different task" in message:
            logger.debug("%s: %s", prefix, exc)
            return
        logger.warning("%s: %s", prefix, exc)

    def _normalize_result(self, result: Any) -> Any:
        if getattr(result, "isError", False):
            text = self._extract_text(result)
            raise OfficialToolError(
                "official_tool_error", text or "official MCP returned an error"
            )

        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return structured

        text = self._extract_text(result)
        if text:
            try:
                return json.loads(text)
            except Exception:
                return {"text": text}

        if hasattr(result, "model_dump"):
            return result.model_dump()
        return result

    @staticmethod
    def _extract_text(result: Any) -> str:
        content = getattr(result, "content", None) or []
        texts: list[str] = []
        for block in content:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", "")
                if text:
                    texts.append(text)
        return "\n".join(texts).strip()

    def _record_failure(self, exc: Exception) -> None:
        self._failure_count += 1
        self._last_failure_at = time.time()
        self._last_error = f"{exc.__class__.__name__}: {exc}"
        logger.warning("Official MCP call failed (%s): %s", exc.__class__.__name__, exc)

    def _record_success(self) -> None:
        self._failure_count = 0
        self._last_error = None

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        args = arguments or {}
        with self._lock:
            for attempt in range(2):
                try:
                    self._ensure_connected()
                    if self._session is None:
                        raise RuntimeError("official MCP session unavailable")
                    result = self._submit(self._session.call_tool(name, arguments=args))
                    normalized = self._normalize_result(result)
                    self._record_success()
                    return normalized
                except OfficialToolError as exc:
                    if exc.code == "official_tool_error":
                        # Do not degrade semantic tool errors into transport failures.
                        raise
                    self._record_failure(exc)
                    try:
                        self._submit(self._disconnect_async())
                    except Exception as cleanup_exc:
                        self._log_cleanup_exception("Official MCP disconnect failed", cleanup_exc)
                    if attempt == 1:
                        raise
                except Exception as exc:
                    self._record_failure(exc)
                    try:
                        self._submit(self._disconnect_async())
                    except Exception as cleanup_exc:
                        self._log_cleanup_exception("Official MCP disconnect failed", cleanup_exc)
                    if attempt == 1:
                        raise OfficialToolError(
                            "official_unavailable",
                            f"official MCP call failed for tool '{name}': {exc}",
                        ) from exc

        raise OfficialToolError("official_unavailable", "official MCP unavailable")

    def list_tools(self) -> list[str]:
        with self._lock:
            for attempt in range(2):
                try:
                    self._ensure_connected()
                    if self._session is None:
                        return []
                    result = self._submit(self._session.list_tools())
                    tools = getattr(result, "tools", []) or []
                    self._record_success()
                    return [t.name for t in tools if getattr(t, "name", None)]
                except Exception as exc:
                    self._record_failure(exc)
                    try:
                        self._submit(self._disconnect_async())
                    except Exception as cleanup_exc:
                        self._log_cleanup_exception("Official MCP disconnect failed", cleanup_exc)
                    if attempt == 1:
                        raise OfficialToolError(
                            "official_unavailable",
                            f"official MCP list_tools failed: {exc}",
                        ) from exc
            return []

    def get_health(self) -> dict[str, Any]:
        with self._lock:
            health: dict[str, Any] = {
                "transport": self._transport,
                "url": self._url,
                "connected": self._session is not None,
                "failureCount": self._failure_count,
                "lastError": self._last_error,
                "lastFailureAt": self._last_failure_at,
                "lastConnectedAt": self._last_connected_at,
            }
            if self._transport == "stdio":
                health["command"] = self._command
                health["args"] = self._args
            else:
                health["hasHeaders"] = self._headers is not None
            return health

    def close(self) -> None:
        with self._lock:
            loop = self._loop
            thread = self._thread

            if loop:
                try:
                    self._submit(self._disconnect_async())
                except Exception as exc:
                    self._log_cleanup_exception("Official MCP disconnect during close failed", exc)

                loop.call_soon_threadsafe(loop.stop)

            if thread and thread.is_alive():
                thread.join(timeout=1.0)

            if loop and (thread is None or not thread.is_alive()):
                loop.close()
            elif thread and thread.is_alive():
                logger.warning("Official MCP loop thread did not stop within timeout")

            self._session = None
            self._session_cm = None
            self._transport_cm = None
            self._loop = None
            self._thread = None
