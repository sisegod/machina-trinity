#!/usr/bin/env python3
"""Machina MCP Connection — single MCP server connection management.

Extracted from machina_mcp.py for maintainability.
Handles stdio/sse/streamable_http transports and tool discovery.
"""

import asyncio
import fcntl
import json
import logging
import os
import re
from datetime import timedelta
from typing import Any

logger = logging.getLogger(__name__)

MACHINA_ROOT = os.environ.get(
    "MACHINA_ROOT",
    os.path.dirname(os.path.abspath(__file__)),
)
MCP_CONFIG_PATH = os.path.join(MACHINA_ROOT, "mcp_servers.json")

# AID pattern for MCP tools
_AID_MCP_PREFIX = "AID.MCP."
_ENV_REF_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _mcp_tool_timeout_sec() -> int:
    """Read MCP tool call timeout from env with sane bounds."""
    raw = os.getenv("MACHINA_MCP_TOOL_TIMEOUT_SEC", "45")
    try:
        v = int(raw)
    except Exception:
        return 45
    if v < 5:
        return 5
    if v > 300:
        return 300
    return v


def _config_read_modify_write(config_path: str, modify_fn):
    """Atomically read-modify-write MCP config with flock."""
    fd = os.open(config_path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(os.dup(fd), "r", encoding="utf-8") as rf:
            config = json.load(rf)
        result = modify_fn(config)
        data = json.dumps(config, indent=2, ensure_ascii=False).encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, data)
        os.fsync(fd)
        return result
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _sanitize_name(name: str) -> str:
    """Sanitize a name for use in AID identifiers (uppercase, alphanum+underscore)."""
    return re.sub(r"[^A-Z0-9_]", "_", name.upper())


def make_mcp_aid(server: str, tool: str) -> str:
    """Build AID identifier for an MCP tool."""
    return f"{_AID_MCP_PREFIX}{_sanitize_name(server)}.{_sanitize_name(tool)}.v1"


def parse_mcp_aid(aid: str) -> tuple:
    """Parse AID.MCP.SERVER.TOOL.v1 → (server_key, tool_name) or (None, None)."""
    if not aid.startswith(_AID_MCP_PREFIX):
        return None, None
    rest = aid[len(_AID_MCP_PREFIX):]
    # Format: SERVER.TOOL_NAME.v1
    parts = rest.rsplit(".v", 1)
    if len(parts) != 2:
        return None, None
    body = parts[0]  # SERVER.TOOL_NAME
    dot = body.find(".")
    if dot < 0:
        return None, None
    return body[:dot], body[dot + 1:]


def _resolve_env_refs(value):
    """Resolve ${ENV_VAR} placeholders in config values recursively."""
    if isinstance(value, str):
        def _sub(match):
            key = match.group(1)
            return os.getenv(key, "")
        return _ENV_REF_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _resolve_env_refs(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_refs(v) for v in value]
    return value


class MCPServerConnection:
    """Manages a single MCP server connection."""

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = _resolve_env_refs(config)
        self.transport = config.get("transport", "stdio")
        self.tools: dict[str, dict] = {}  # tool_name → {description, inputSchema}
        self._session = None
        self._read = None
        self._write = None
        self._cm = None  # context manager for transport
        self._connected = False

    async def connect(self):
        """Connect to the MCP server and discover tools."""
        try:
            if self.transport == "stdio":
                await self._connect_stdio()
            elif self.transport == "sse":
                await self._connect_sse()
            elif self.transport == "streamable_http":
                await self._connect_streamable_http()
            else:
                logger.error(f"MCP [{self.name}]: unknown transport '{self.transport}'")
                return

            if self._session:
                await self._session.initialize()
                result = await self._session.list_tools()
                for tool in result.tools:
                    self.tools[tool.name] = {
                        "description": tool.description or "",
                        "inputSchema": tool.inputSchema or {},
                    }
                self._connected = True
                logger.info(
                    f"MCP [{self.name}]: connected, {len(self.tools)} tools discovered"
                )
        except Exception as e:
            logger.error(f"MCP [{self.name}]: connection failed: {type(e).__name__}: {e}")

    async def _connect_stdio(self):
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from mcp.client.session import ClientSession

        command = self.config.get("command", "")
        args = self.config.get("args", [])
        env_extra = self.config.get("env", {})
        cwd = self.config.get("cwd")

        if not command:
            logger.error(f"MCP [{self.name}]: no command specified for stdio transport")
            return

        # Merge env
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)

        params = StdioServerParameters(
            command=command,
            args=args,
            env=env,
            cwd=cwd,
        )

        # stdio_client is an async context manager — we need to keep it alive
        self._cm = stdio_client(params)
        self._read, self._write = await self._cm.__aenter__()
        try:
            self._session = ClientSession(self._read, self._write)
            await self._session.__aenter__()
        except Exception:
            await self._cm.__aexit__(None, None, None)
            self._cm = None
            raise

    async def _connect_sse(self):
        from mcp.client.sse import sse_client
        from mcp.client.session import ClientSession

        url = self.config.get("url", "")
        headers = self.config.get("headers", {})
        if not url:
            logger.error(f"MCP [{self.name}]: no url specified for SSE transport")
            return

        self._cm = sse_client(url=url, headers=headers)
        self._read, self._write = await self._cm.__aenter__()
        try:
            self._session = ClientSession(self._read, self._write)
            await self._session.__aenter__()
        except Exception:
            await self._cm.__aexit__(None, None, None)
            self._cm = None
            raise

    async def _connect_streamable_http(self):
        from mcp.client.streamable_http import streamablehttp_client
        from mcp.client.session import ClientSession

        url = self.config.get("url", "")
        headers = self.config.get("headers", {})
        if not url:
            logger.error(f"MCP [{self.name}]: no url for streamable_http transport")
            return

        self._cm = streamablehttp_client(url=url, headers=headers)
        self._read, self._write, _ = await self._cm.__aenter__()
        try:
            self._session = ClientSession(self._read, self._write)
            await self._session.__aenter__()
        except Exception:
            await self._cm.__aexit__(None, None, None)
            self._cm = None
            raise

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call an MCP tool and return the result as a string."""
        if not self._connected or not self._session:
            return f"error: MCP server '{self.name}' not connected"
        if tool_name not in self.tools:
            return f"error: tool '{tool_name}' not found on MCP server '{self.name}'"

        try:
            arguments = self._normalize_tool_arguments(tool_name, arguments)
            required = (self.tools.get(tool_name, {}).get("inputSchema", {}) or {}).get("required", []) or []
            missing = [k for k in required if not str((arguments or {}).get(k, "")).strip()]
            if missing:
                return (
                    f"error: MCP missing required args for {self.name}.{tool_name}: "
                    f"{', '.join(missing[:5])}"
                )
            timeout_sec = _mcp_tool_timeout_sec()
            result = await self._session.call_tool(
                tool_name,
                arguments,
                read_timeout_seconds=timedelta(seconds=timeout_sec),
            )
            # Extract text from result content
            parts = []
            for content in result.content:
                if hasattr(content, "text"):
                    parts.append(content.text)
                elif hasattr(content, "data"):
                    parts.append(f"[binary data: {len(content.data)} bytes]")
                else:
                    parts.append(str(content))

            output = "\n".join(parts)

            if result.isError:
                return f"MCP error: {output}"

            # Only truncate truly massive output (1MB+)
            if len(output) > 1_000_000:
                output = output[:1_000_000] + "\n...(MCP output truncated)"
            return output if output else "(no output from MCP tool)"

        except Exception as e:
            logger.error(
                f"MCP [{self.name}].{tool_name}: {type(e).__name__}: {e}"
            )
            return f"MCP call error [{self.name}.{tool_name}]: {type(e).__name__}: {e}"

    def _normalize_tool_arguments(self, tool_name: str, arguments: dict | None) -> dict:
        """Normalize common alias keys to actual schema keys for MCP tools."""
        args = dict(arguments or {})
        schema = (self.tools.get(tool_name, {}).get("inputSchema", {}) or {})
        props = schema.get("properties", {}) or {}

        # search_query <- query/q/keyword/text
        if "search_query" in props and not str(args.get("search_query", "")).strip():
            for alt in ("query", "q", "keyword", "text"):
                if str(args.get(alt, "")).strip():
                    args["search_query"] = args[alt]
                    break

        # url <- link/uri/href
        if "url" in props and not str(args.get("url", "")).strip():
            for alt in ("link", "uri", "href"):
                if str(args.get(alt, "")).strip():
                    args["url"] = args[alt]
                    break

        return args

    async def disconnect(self):
        """Gracefully disconnect from the MCP server."""
        self._connected = False
        try:
            if self._session:
                try:
                    await self._session.__aexit__(None, None, None)
                except Exception as e:
                    logger.debug(f"MCP [{self.name}]: session cleanup: {type(e).__name__}: {e}")
                    pass
                self._session = None
            if self._cm:
                try:
                    await self._cm.__aexit__(None, None, None)
                except Exception as e:
                    logger.debug(f"MCP [{self.name}]: cm cleanup: {type(e).__name__}: {e}")
                    pass
                self._cm = None
            logger.info(f"MCP [{self.name}]: disconnected")
        except Exception as e:
            logger.debug(f"MCP [{self.name}]: disconnect cleanup: {type(e).__name__}: {e}")
