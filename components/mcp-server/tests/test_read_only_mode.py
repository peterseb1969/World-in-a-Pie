"""Tests for WIP_MCP_MODE=readonly — verifies write tools are removed."""

import os
import subprocess
import sys
from pathlib import Path

MCP_SRC = str(Path(__file__).resolve().parent.parent / "src")
MCP_DIR = str(Path(__file__).resolve().parent.parent)


def _run_in_subprocess(code: str, readonly: bool = False) -> subprocess.CompletedProcess:
    """Run code in a fresh subprocess to avoid module caching."""
    env = os.environ.copy()
    env["PYTHONPATH"] = MCP_SRC
    if readonly:
        env["WIP_MCP_MODE"] = "readonly"
    else:
        env.pop("WIP_MCP_MODE", None)
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
    )


def test_readonly_mode_removes_write_tools():
    """In readonly mode, all write tools should be removed."""
    result = _run_in_subprocess(
        "from wip_mcp.server import mcp, WRITE_TOOLS; "
        "tools = {t.name for t in mcp._tool_manager.list_tools()}; "
        "write_present = tools & WRITE_TOOLS; "
        "assert len(write_present) == 0, f'Write tools still present: {write_present}'; "
        "assert len(tools) > 0, 'No tools registered'; "
        "print(f'OK: {len(tools)} read-only tools')",
        readonly=True,
    )
    assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    assert "OK:" in result.stdout


def test_normal_mode_keeps_all_tools():
    """Without WIP_MCP_MODE=readonly, all tools should remain."""
    result = _run_in_subprocess(
        "from wip_mcp.server import mcp, WRITE_TOOLS; "
        "tools = {t.name for t in mcp._tool_manager.list_tools()}; "
        "write_present = tools & WRITE_TOOLS; "
        "assert len(write_present) == len(WRITE_TOOLS), "
        "f'Expected {len(WRITE_TOOLS)} write tools, got {len(write_present)}'; "
        "print(f'OK: {len(tools)} total tools, {len(write_present)} write')",
        readonly=False,
    )
    assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    assert "OK:" in result.stdout


def test_readonly_mode_updates_instructions():
    """Read-only mode should update server instructions."""
    result = _run_in_subprocess(
        "from wip_mcp.server import mcp; "
        "assert 'READ-ONLY' in mcp.instructions, "
        "f'Instructions missing READ-ONLY: {mcp.instructions[:100]}'; "
        "assert 'CANNOT create' in mcp.instructions; "
        "print('OK: instructions updated')",
        readonly=True,
    )
    assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"


def test_readonly_preserves_key_read_tools():
    """Key query/discovery tools must remain in read-only mode."""
    expected = [
        "get_wip_status", "list_namespaces", "list_terminologies",
        "list_templates", "list_documents", "query_documents",
        "run_report_query", "search", "get_term_hierarchy",
        "list_files", "get_table_view", "export_table_csv",
    ]
    checks = "; ".join(f"assert '{t}' in tools, 'Missing {t}'" for t in expected)
    result = _run_in_subprocess(
        "from wip_mcp.server import mcp; "
        f"tools = {{t.name for t in mcp._tool_manager.list_tools()}}; "
        f"{checks}; "
        "print('OK: all key read tools present')",
        readonly=True,
    )
    assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
