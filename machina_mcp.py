#!/usr/bin/env python3
"""Machina MCP Bridge — connects MCP servers as Machina tools.

Reads mcp_servers.json, starts MCP server processes, discovers tools via
list_tools(), and exposes them through the AID dispatch system.

Each MCP tool becomes AID.MCP.{SERVER}.{TOOL_NAME}.v1
Example: AID.MCP.N8N.search_nodes.v1

Usage:
    from machina_mcp import mcp_manager
    await mcp_manager.start()           # connect all configured servers
    tools = mcp_manager.get_tool_list() # get discovered tools for INTENT_PROMPT
    result = await mcp_manager.call("server_name", "tool_name", {"arg": "val"})
    await mcp_manager.stop()            # graceful shutdown
"""

import asyncio
import json
import logging
import os
import time
from typing import Any

from machina_mcp_connection import (
    MCPServerConnection,
    _config_read_modify_write,
    _sanitize_name,
    make_mcp_aid,
    parse_mcp_aid,
    _AID_MCP_PREFIX,
    MACHINA_ROOT,
    MCP_CONFIG_PATH,
)

logger = logging.getLogger(__name__)


class MCPManager:
    """Manages all MCP server connections and provides unified tool access."""

    def __init__(self):
        self.servers: dict[str, MCPServerConnection] = {}
        self._started = False
        self._config: dict = {}
        self._loop = None  # reference to the event loop where sessions were created

    def load_config(self, path: str = None) -> dict:
        """Load MCP server configuration from JSON file."""
        config_path = path or MCP_CONFIG_PATH
        if not os.path.exists(config_path):
            logger.info(f"MCP config not found: {config_path}")
            return {}

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                self._config = json.load(f)
            servers = self._config.get("servers", self._config.get("mcpServers", {}))
            logger.info(f"MCP config loaded: {len(servers)} server(s)")
            return servers
        except Exception as e:
            logger.error(f"MCP config load error: {type(e).__name__}: {e}")
            return {}

    async def start(self, config_path: str = None):
        """Load config and connect to all MCP servers."""
        if self._started:
            return

        servers_config = self.load_config(config_path)
        if not servers_config:
            logger.info("No MCP servers configured")
            return

        # Store reference to current event loop for cross-thread access
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        for name, config in servers_config.items():
            if config.get("disabled"):
                logger.info(f"MCP [{name}]: disabled, skipping")
                continue
            conn = MCPServerConnection(name, config)
            self.servers[name] = conn

        # Connect all servers concurrently
        if self.servers:
            tasks = [conn.connect() for conn in self.servers.values()]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # Log individual failures
            for (name, conn), result in zip(list(self.servers.items()), results):
                if isinstance(result, Exception):
                    logger.error(f"MCP [{name}]: connect exception: {type(result).__name__}: {result}")
            # Remove servers that failed to connect
            failed = [n for n, c in self.servers.items() if not c._connected]
            for n in failed:
                del self.servers[n]

        self._started = True
        total_tools = sum(len(s.tools) for s in self.servers.values())
        logger.info(f"MCP started: {len(self.servers)} server(s), {total_tools} tool(s)")

    async def stop(self):
        """Disconnect from all MCP servers."""
        tasks = [conn.disconnect() for conn in self.servers.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.servers.clear()
        self._started = False
        logger.info("MCP manager stopped")

    async def call(self, server_name: str, tool_name: str, arguments: dict) -> str:
        """Call an MCP tool by server name and tool name."""
        # Case-insensitive server lookup
        conn = None
        for k, v in self.servers.items():
            if k.upper() == server_name.upper():
                conn = v
                break
        if not conn:
            return f"error: MCP server '{server_name}' not found"

        # Case-insensitive tool lookup
        actual_tool = None
        for t in conn.tools:
            if t.upper() == tool_name.upper():
                actual_tool = t
                break
        if not actual_tool:
            # Try with underscore variations
            for t in conn.tools:
                if _sanitize_name(t) == tool_name.upper():
                    actual_tool = t
                    break
        if not actual_tool:
            # Fuzzy: strip server-name prefixes LLM sometimes prepends
            clean = tool_name.upper()
            for pfx in [f"MCP_{_sanitize_name(server_name)}_", f"{_sanitize_name(server_name)}_", "MCP_"]:
                if clean.startswith(pfx):
                    clean = clean[len(pfx):]
                    break
            for t in conn.tools:
                if _sanitize_name(t) == clean:
                    actual_tool = t
                    break
        if not actual_tool:
            return f"error: tool '{tool_name}' not found on MCP server '{server_name}'"

        return await conn.call_tool(actual_tool, arguments)

    async def call_by_aid(self, aid: str, arguments: dict) -> str:
        """Call an MCP tool by its AID identifier."""
        server_key, tool_key = parse_mcp_aid(aid)
        if not server_key or not tool_key:
            return f"error: invalid MCP AID: {aid}"
        return await self.call(server_key, tool_key, arguments)

    def get_all_tools(self) -> dict:
        """Get all discovered MCP tools mapped to AID identifiers.

        Returns: {aid: {"server": name, "tool": tool_name, "description": str, "inputSchema": dict}}
        """
        result = {}
        for server_name, conn in self.servers.items():
            for tool_name, tool_info in conn.tools.items():
                aid = make_mcp_aid(server_name, tool_name)
                result[aid] = {
                    "server": server_name,
                    "tool": tool_name,
                    "description": tool_info["description"],
                    "inputSchema": tool_info["inputSchema"],
                }
        return result

    def get_tool_list_for_prompt(self, max_tools: int = 30) -> str:
        """Generate a concise tool list string for injection into INTENT_PROMPT."""
        lines = []
        for server_name, conn in self.servers.items():
            for tool_name, tool_info in conn.tools.items():
                desc = tool_info["description"]
                if len(desc) > 60:
                    desc = desc[:57] + "..."
                aid = make_mcp_aid(server_name, tool_name)
                lines.append(f"- {aid}: {desc} (server={server_name}, tool={tool_name})")
                if len(lines) >= max_tools:
                    break
            if len(lines) >= max_tools:
                break
        return "\n".join(lines)

    def get_intent_examples(self, max_examples: int = 5) -> str:
        """Generate intent JSON examples for MCP tools."""
        examples = []
        count = 0
        for server_name, conn in self.servers.items():
            for tool_name, tool_info in conn.tools.items():
                schema = tool_info.get("inputSchema", {})
                props = schema.get("properties", {})
                example_args = {}
                for prop_name, prop_info in list(props.items())[:3]:
                    ptype = prop_info.get("type", "string")
                    if ptype == "string":
                        example_args[prop_name] = f"예시_{prop_name}"
                    elif ptype in ("number", "integer"):
                        example_args[prop_name] = 1
                    elif ptype == "boolean":
                        example_args[prop_name] = True
                    elif ptype == "array":
                        example_args[prop_name] = []
                    elif ptype == "object":
                        example_args[prop_name] = {}

                short_name = f"mcp_{server_name}_{tool_name}"
                example = {
                    "type": "run",
                    "tool": "mcp",
                    "mcp_server": server_name,
                    "mcp_tool": tool_name,
                    "args": example_args,
                }
                desc_short = tool_info["description"][:40]
                examples.append(
                    f"{desc_short} -> {json.dumps(example, ensure_ascii=False)}"
                )
                count += 1
                if count >= max_examples:
                    break
            if count >= max_examples:
                break
        return "\n".join(examples)

    def get_aliases(self) -> dict:
        """Generate TOOL_ALIASES entries for MCP tools."""
        aliases = {}
        for server_name, conn in self.servers.items():
            for tool_name in conn.tools:
                alias = f"mcp_{server_name}_{tool_name}"
                aid = make_mcp_aid(server_name, tool_name)
                aliases[alias] = aid
                aliases[f"mcp_{tool_name}"] = aid
        return aliases

    def get_descriptions(self) -> dict:
        """Generate TOOL_DESCRIPTIONS entries for MCP tools."""
        descriptions = {}
        for server_name, conn in self.servers.items():
            for tool_name, tool_info in conn.tools.items():
                aid = make_mcp_aid(server_name, tool_name)
                desc = tool_info["description"][:80]
                descriptions[aid] = f"{desc} (MCP:{server_name})"
        return descriptions

    def get_permissions(self) -> dict:
        """Generate DEFAULT_PERMISSIONS entries for MCP tools."""
        from machina_permissions import ASK
        ALLOW = "allow"
        _SAFE_PREFIXES = (
            "websearch", "webreader", "web_search", "web_reader",
            "analyze_", "extract_text", "diagnose_error", "understand_",
            "ui_to_artifact", "ui_diff_check",
        )
        perms = {}
        for server_name, conn in self.servers.items():
            for tool_name in conn.tools:
                aid = make_mcp_aid(server_name, tool_name)
                tool_lower = tool_name.lower()
                if any(tool_lower.startswith(p) for p in _SAFE_PREFIXES):
                    perms[aid] = ALLOW
                else:
                    perms[aid] = ASK
        return perms

    async def reload(self):
        """Reload config and reconnect all servers."""
        logger.info("MCP reload: stopping existing connections...")
        await self.stop()
        self._started = False
        await self.start()
        return self.status()

    async def enable_server(self, server_name: str) -> str:
        """Enable a disabled server by name (modifies config + connects)."""
        config_path = MCP_CONFIG_PATH
        if not os.path.exists(config_path):
            return f"error: config file not found: {config_path}"

        found_key = None
        server_cfg = None

        def _modify(config):
            nonlocal found_key, server_cfg
            servers = config.get("servers", config.get("mcpServers", {}))
            for k in servers:
                if k.lower() == server_name.lower():
                    found_key = k
                    break
            if not found_key:
                return f"error: server '{server_name}' not found in config. Available: {', '.join(servers.keys())}"
            if not servers[found_key].get("disabled"):
                return f"'{found_key}' is already enabled"
            servers[found_key].pop("disabled", None)
            server_cfg = dict(servers[found_key])
            return None

        try:
            err = _config_read_modify_write(config_path, _modify)
        except Exception as e:
            return f"error: cannot update config: {e}"
        if err:
            return err

        conn = MCPServerConnection(found_key, server_cfg)
        self.servers[found_key] = conn
        await conn.connect()
        if conn._connected:
            return f"'{found_key}' enabled and connected ({len(conn.tools)} tools)"
        return f"'{found_key}' enabled but connection failed"

    async def disable_server(self, server_name: str) -> str:
        """Disable a server by name (disconnects + marks disabled in config)."""
        config_path = MCP_CONFIG_PATH
        if not os.path.exists(config_path):
            return f"error: config file not found: {config_path}"

        found_key = None

        def _modify(config):
            nonlocal found_key
            servers = config.get("servers", config.get("mcpServers", {}))
            for k in servers:
                if k.lower() == server_name.lower():
                    found_key = k
                    break
            if not found_key:
                return f"error: server '{server_name}' not found in config"
            servers[found_key]["disabled"] = True
            return None

        # Disconnect if running
        for k in list(self.servers):
            if k.lower() == server_name.lower():
                await self.servers[k].disconnect()
                del self.servers[k]
                found_key = k

        try:
            err = _config_read_modify_write(config_path, _modify)
        except Exception as e:
            return f"error: cannot update config: {e}"
        if err:
            return err

        return f"'{found_key}' disabled and disconnected"

    async def add_server(self, name: str, transport: str, url: str = "",
                         command: str = "", args: list = None,
                         headers: dict = None, env: dict = None) -> str:
        """Add a new MCP server to config and connect it."""
        config_path = MCP_CONFIG_PATH

        new_config = {"transport": transport}
        if transport == "stdio":
            if not command:
                return "error: stdio transport requires 'command'"
            new_config["command"] = command
            if args:
                new_config["args"] = args
            if env:
                new_config["env"] = env
        else:
            if not url:
                return f"error: {transport} transport requires 'url'"
            new_config["url"] = url
            if headers:
                new_config["headers"] = headers

        def _modify(config):
            servers = config.setdefault("servers", {})
            if name in servers:
                return f"error: server '{name}' already exists. Use enable/disable or edit manually."
            servers[name] = new_config
            return None

        if not os.path.exists(config_path):
            with open(config_path, "w") as f:
                json.dump({"servers": {}}, f)

        try:
            err = _config_read_modify_write(config_path, _modify)
        except Exception as e:
            return f"error: cannot update config: {e}"
        if err:
            return err

        conn = MCPServerConnection(name, new_config)
        self.servers[name] = conn
        await conn.connect()
        if conn._connected:
            return f"'{name}' added and connected ({len(conn.tools)} tools discovered)"
        return f"'{name}' added but connection failed (check config)"

    async def remove_server(self, server_name: str) -> str:
        """Remove a server from config and disconnect it."""
        config_path = MCP_CONFIG_PATH
        if not os.path.exists(config_path):
            return f"error: config file not found: {config_path}"

        found_key = None

        # Disconnect if running
        for k in list(self.servers):
            if k.lower() == server_name.lower():
                await self.servers[k].disconnect()
                del self.servers[k]
                found_key = k

        def _modify(config):
            nonlocal found_key
            servers = config.get("servers", config.get("mcpServers", {}))
            fk = None
            for k in servers:
                if k.lower() == server_name.lower():
                    fk = k
                    break
            if not fk:
                return f"error: server '{server_name}' not found"
            del servers[fk]
            if not found_key:
                found_key = fk
            return None

        try:
            err = _config_read_modify_write(config_path, _modify)
        except Exception as e:
            return f"error: cannot update config: {e}"
        if err:
            return err

        return f"'{found_key}' removed from config and disconnected"

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def tool_count(self) -> int:
        return sum(len(s.tools) for s in self.servers.values())

    def status(self) -> dict:
        """Return status summary."""
        return {
            "started": self._started,
            "servers": {
                name: {
                    "connected": conn._connected,
                    "tools": len(conn.tools),
                    "transport": conn.transport,
                }
                for name, conn in self.servers.items()
            },
            "total_tools": self.tool_count,
        }


# Singleton instance
mcp_manager = MCPManager()
