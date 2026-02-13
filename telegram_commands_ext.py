#!/usr/bin/env python3
"""Machina Telegram Command Handlers — Extended (MCP, Dev, Tools, Graph).

Extracted from telegram_commands.py for maintainability.
Contains: /mcp_status /mcp_reload /mcp_enable /mcp_disable /mcp_add /mcp_remove
          /dev_mode /tools /graph_status

All handlers are async (python-telegram-bot v20+).
"""

import logging
import os

from telegram import Update
from telegram.ext import ContextTypes

from machina_shared import get_brain_label

logger = logging.getLogger(__name__)


def _get_send_chunked():
    """Lazy import to avoid circular dependency."""
    from telegram_commands import _get_send_chunked as _parent_get
    return _parent_get()


def _get_available_tools():
    """Lazy import module-level AVAILABLE_TOOLS from parent."""
    from telegram_commands import AVAILABLE_TOOLS
    return AVAILABLE_TOOLS


def _check_chat_allowed(chat_id: int) -> bool:
    from telegram_commands import check_chat_allowed
    return check_chat_allowed(chat_id)


# ── MCP Commands ─────────────────────────────────────────────────────────────


async def mcp_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show MCP server status and discovered tools.

    Usage: /mcp_status
    """
    if not _check_chat_allowed(update.effective_chat.id):
        return

    try:
        from machina_mcp import mcp_manager
        status = mcp_manager.status()
        lines = ["MCP Bridge Status"]
        lines.append(f"Started: {'Yes' if status['started'] else 'No'}")
        lines.append(f"Total tools: {status['total_tools']}")
        lines.append("")

        for name, info in status.get("servers", {}).items():
            connected = "connected" if info["connected"] else "disconnected"
            lines.append(f"  {name}: {connected} ({info['tools']} tools, {info['transport']})")

        if status["total_tools"] > 0:
            lines.append("")
            lines.append("Discovered tools:")
            all_tools = mcp_manager.get_all_tools()
            for aid, tinfo in all_tools.items():
                desc = tinfo["description"][:50]
                lines.append(f"  {tinfo['server']}.{tinfo['tool']}: {desc}")

        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"MCP not available: {e}")


async def mcp_reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reload MCP servers (re-read config + reconnect all).

    Usage: /mcp_reload
    """
    if not _check_chat_allowed(update.effective_chat.id):
        return
    await update.message.reply_text("MCP 리로드 중... ⏳")
    try:
        from machina_mcp import mcp_manager
        from machina_dispatch import register_mcp_tools
        result = await mcp_manager.reload()
        # Re-register tools into dispatch (force to clear old entries)
        await register_mcp_tools(force=True)
        total = result.get("total_tools", 0)
        servers = result.get("servers", {})
        lines = [f"MCP 리로드 완료! {total} tools"]
        for name, info in servers.items():
            status = "connected" if info["connected"] else "disconnected"
            lines.append(f"  {name}: {status} ({info['tools']} tools)")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"MCP reload failed: {e}")


async def mcp_enable_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable a disabled MCP server.

    Usage: /mcp_enable <server_name>
    """
    if not _check_chat_allowed(update.effective_chat.id):
        return
    args = (update.message.text or "").split(None, 1)
    if len(args) < 2 or not args[1].strip():
        await update.message.reply_text("사용법: /mcp_enable <서버이름>\n예) /mcp_enable n8n")
        return
    server_name = args[1].strip()
    try:
        from machina_mcp import mcp_manager
        from machina_dispatch import register_mcp_tools
        result = await mcp_manager.enable_server(server_name)
        # Re-register to update dispatch table
        await register_mcp_tools(force=True)
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"MCP enable failed: {e}")


async def mcp_disable_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable an active MCP server.

    Usage: /mcp_disable <server_name>
    """
    if not _check_chat_allowed(update.effective_chat.id):
        return
    args = (update.message.text or "").split(None, 1)
    if len(args) < 2 or not args[1].strip():
        await update.message.reply_text("사용법: /mcp_disable <서버이름>\n예) /mcp_disable n8n")
        return
    server_name = args[1].strip()
    try:
        from machina_mcp import mcp_manager
        from machina_dispatch import register_mcp_tools
        result = await mcp_manager.disable_server(server_name)
        # Re-register to update dispatch table after disabling
        await register_mcp_tools(force=True)
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"MCP disable failed: {e}")


async def mcp_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a new MCP server.

    Usage: /mcp_add <name> <transport> <url_or_command>
    Examples:
      /mcp_add my_server streamable_http https://example.com/mcp
      /mcp_add my_tool stdio npx my-mcp-tool
    """
    if not _check_chat_allowed(update.effective_chat.id):
        return
    parts = (update.message.text or "").split(None, 3)
    if len(parts) < 4:
        await update.message.reply_text(
            "사용법: /mcp_add <이름> <트랜스포트> <URL 또는 명령어>\n"
            "예) /mcp_add my_api streamable_http https://api.example.com/mcp\n"
            "예) /mcp_add my_tool stdio npx @my/mcp-server\n\n"
            "트랜스포트: stdio, sse, streamable_http"
        )
        return
    name = parts[1].strip()
    transport = parts[2].strip()
    target = parts[3].strip()

    try:
        from machina_mcp import mcp_manager
        from machina_dispatch import register_mcp_tools
        if transport == "stdio":
            # Parse command + args
            cmd_parts = target.split()
            result = await mcp_manager.add_server(
                name, transport, command=cmd_parts[0],
                args=cmd_parts[1:] if len(cmd_parts) > 1 else [])
        else:
            result = await mcp_manager.add_server(name, transport, url=target)
        # Re-register to update dispatch table
        await register_mcp_tools(force=True)
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"MCP add failed: {e}")


async def mcp_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove an MCP server from config.

    Usage: /mcp_remove <server_name>
    """
    if not _check_chat_allowed(update.effective_chat.id):
        return
    args = (update.message.text or "").split(None, 1)
    if len(args) < 2 or not args[1].strip():
        await update.message.reply_text("사용법: /mcp_remove <서버이름>\n예) /mcp_remove my_server")
        return
    server_name = args[1].strip()
    try:
        from machina_mcp import mcp_manager
        result = await mcp_manager.remove_server(server_name)
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"MCP remove failed: {e}")


# ── Dev / Tools / Graph Commands ─────────────────────────────────────────────


async def dev_mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle or set autonomic engine mode (DEV EXPLORE vs PRODUCTION).

    Usage: /dev_mode         -- toggle
           /dev_mode on      -- enable DEV EXPLORE
           /dev_mode off     -- switch to PRODUCTION
    """
    if not _check_chat_allowed(update.effective_chat.id):
        return
    args = (update.message.text or "").split(None, 1)
    sub = args[1].strip().lower() if len(args) > 1 else ""

    try:
        from telegram_bot import _autonomic_engine
        if not _autonomic_engine:
            await update.message.reply_text("자율 엔진이 비활성 상태야.")
            return

        if sub in ("on", "dev", "켜", "개발"):
            _autonomic_engine.set_mode(True)
            new_dev = True
        elif sub in ("off", "prod", "꺼", "운영"):
            _autonomic_engine.set_mode(False)
            new_dev = False
        else:
            # Toggle
            cur = _autonomic_engine._dev
            _autonomic_engine.set_mode(not cur)
            new_dev = not cur

        if new_dev:
            lines = [
                "🟢 DEV EXPLORE 모드 활성화",
                "",
                "변경된 타이밍:",
                "  반성: 1분 간격 (운영: 5분)",
                "  테스트: 2분 간격 (운영: 10분)",
                "  탐구: 20회/일 (운영: 10회)",
                "  버스트: 3분 유휴 후 (운영: 30분)",
                "",
                "자가 학습이 적극적으로 작동합니다.",
            ]
        else:
            lines = [
                "🔵 PRODUCTION 모드 활성화",
                "",
                "변경된 타이밍:",
                "  반성: 5분 간격",
                "  테스트: 10분 간격",
                "  탐구: 10회/일",
                "  버스트: 30분 유휴 후",
                "",
                "안정적인 운영 모드입니다.",
            ]
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"모드 전환 실패: {e}")


async def tools_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all available tools in categorized format.

    Usage: /tools
    """
    if not _check_chat_allowed(update.effective_chat.id):
        return

    available_tools = _get_available_tools()

    # Categorize built-in tools
    categories = {
        "시스템": [],
        "코드": [],
        "파일": [],
        "메모리": [],
        "웹": [],
        "유틸리티": [],
        "기타": [],
    }
    _cat_keywords = {
        "시스템": ("SHELL", "GPU", "PROC", "META", "RUNLOG"),
        "코드": ("CODE", "GENESIS"),
        "파일": ("FILE", "FS"),
        "메모리": ("MEM", "MEMORY", "VECTORDB", "EMBED"),
        "웹": ("HTTP", "WEB", "SEARCH"),
        "유틸리티": ("UTIL", "QUEUE", "REPORT", "ERROR_SCAN"),
    }

    for tool in available_tools:
        name = tool.get("name", "")
        placed = False
        for cat, keywords in _cat_keywords.items():
            if any(kw in name.upper() for kw in keywords):
                categories[cat].append(name)
                placed = True
                break
        if not placed:
            categories["기타"].append(name)

    lines = [f"🔧 사용 가능한 도구 ({len(available_tools)}개)", ""]

    for cat, tools in categories.items():
        if tools:
            lines.append(f"📂 {cat} ({len(tools)}개)")
            for t in sorted(tools):
                lines.append(f"  • {t}")
            lines.append("")

    # MCP tools
    try:
        from machina_mcp import mcp_manager
        if mcp_manager.is_started and mcp_manager.tool_count > 0:
            lines.append(f"🌐 MCP 외부 도구 ({mcp_manager.tool_count}개)")
            all_mcp = mcp_manager.get_all_tools()
            for aid, tinfo in all_mcp.items():
                desc = tinfo["description"][:40]
                lines.append(f"  • {tinfo['server']}.{tinfo['tool']}: {desc}")
            lines.append("")
    except Exception as e: logger.debug(f"MCP tools listing: {type(e).__name__}: {e}")

    lines.append("💡 자연어로 요청하면 자동으로 적합한 도구를 선택해.")

    await _get_send_chunked()(update, "\n".join(lines))


async def graph_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show Graph Memory statistics.

    Usage: /graph_status
    Optional: /graph_status <entity_name> -- show neighbors of an entity
    """
    if not _check_chat_allowed(update.effective_chat.id):
        return
    args = (update.message.text or "").split(None, 1)
    try:
        from machina_graph import graph_stats, graph_query_neighbors
        if len(args) >= 2 and args[1].strip():
            # Query specific entity neighbors
            name = args[1].strip()
            neighbors = graph_query_neighbors(name, limit=10)
            if not neighbors:
                await update.message.reply_text(f"'{name}' not found in graph memory")
                return
            lines = [f"Graph neighbors of '{name}':"]
            for n in neighbors:
                lines.append(f"  {n['predicate']} -> {n['entity']} "
                            f"({n['type']}, w={n['weight']}, x{n['mention_count']})")
            await update.message.reply_text("\n".join(lines))
        else:
            # Show overall stats
            stats = graph_stats()
            lines = [
                "Graph Memory Status:",
                f"  Entities: {stats.get('entities', 0)}",
                f"  Relations: {stats.get('relations', 0)}",
                f"  Avg degree: {stats.get('avg_degree', 0)}",
            ]
            types = stats.get("entity_types", {})
            if types:
                type_str = ", ".join(f"{t}:{c}" for t, c in list(types.items())[:5])
                lines.append(f"  Types: {type_str}")
            await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Graph status error: {e}")
