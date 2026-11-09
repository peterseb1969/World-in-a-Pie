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


def test_write_tools_covers_every_write_verb_tool():
    """Drift guard: WRITE_TOOLS is a hand-maintained allowlist, and twice
    now a new write tool shipped without being added (api-key pair, then
    migrate_documents) — leaving readonly-mode servers exposing a genuine
    write. Derive the write-shaped tool names from the live registry by
    verb prefix and require every one to be in WRITE_TOOLS, so the next
    write tool fails THIS test instead of silently leaking past readonly.

    If this fails for a genuinely read-only tool whose name merely starts
    with a write verb, rename the tool or extend WRITE_TOOLS consciously —
    don't weaken the prefix list without checking what it guards.
    """
    write_verbs = (
        "create_", "update_", "delete_", "upsert_", "merge_", "add_",
        "remove_", "start_", "cancel_", "pause_", "resume_", "import_",
        "upload_", "archive_", "deprecate_", "activate_", "deactivate_",
        "reactivate_", "revoke_", "migrate_", "restore_", "hard_",
        "reconcile_",
    )
    result = _run_in_subprocess(
        "from wip_mcp.server import mcp, WRITE_TOOLS; "
        f"verbs = {write_verbs!r}; "
        "tools = {t.name for t in mcp._tool_manager.list_tools()}; "
        "write_shaped = {t for t in tools if t.startswith(verbs)}; "
        "missing = write_shaped - WRITE_TOOLS; "
        "assert not missing, f'Write-shaped tools missing from WRITE_TOOLS: {missing}'; "
        "stale = WRITE_TOOLS - tools; "
        "assert not stale, f'WRITE_TOOLS entries with no registered tool: {stale}'; "
        "print(f'OK: {len(write_shaped)} write-shaped tools all covered')",
        readonly=False,
    )
    assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    assert "OK:" in result.stdout


# Every registered tool that is NOT a write, named one by one. A name prefix
# cannot tell a write from a read (reconcile_terminology wrote terms under a
# verb the prefix list did not know), so every tool must be classified on
# purpose: in WRITE_TOOLS, or here. A new tool fails the test below until
# someone decides which side it is on.
READ_TOOLS = frozenset({
    "check_reporting_parity", "describe_data_model", "download_backup_archive",
    "export_report_csv", "export_table_csv", "export_terminology",
    "get_backup_job", "get_document", "get_document_relationships",
    "get_document_versions", "get_entry", "get_file_documents",
    "get_file_metadata", "get_namespace_stats", "get_replay_status",
    "get_sync_status", "get_table_view", "get_template",
    "get_template_by_value", "get_template_dependencies",
    "get_template_facets", "get_template_fields", "get_template_raw",
    "get_template_versions", "get_term", "get_term_hierarchy",
    "get_terminology", "get_terminology_by_value", "get_wip_status",
    "list_api_keys", "list_backup_jobs", "list_documents", "list_files",
    "list_grants", "list_namespaces", "list_report_tables",
    "list_reporting_views", "list_templates", "list_term_relations",
    "list_terminologies", "list_terms", "lookup_entry", "query_by_template",
    "query_documents", "read_reporting_view", "run_report_query", "search",
    "search_registry", "search_terms", "traverse_documents",
    "validate_document", "validate_documents", "validate_namespace",
    "validate_template", "validate_template_candidate", "validate_term_value",
})


def test_every_tool_is_classified_read_or_write():
    """WRITE_TOOLS and READ_TOOLS partition the registered tools exactly."""
    result = _run_in_subprocess(
        "from wip_mcp.server import mcp, WRITE_TOOLS; "
        f"reads = {sorted(READ_TOOLS)!r}; "
        "reads = set(reads); "
        "tools = {t.name for t in mcp._tool_manager.list_tools()}; "
        "both = reads & WRITE_TOOLS; "
        "assert not both, f'Tools listed as both read and write: {both}'; "
        "unclassified = tools - WRITE_TOOLS - reads; "
        "assert not unclassified, ("
        "f'Tools in neither WRITE_TOOLS nor READ_TOOLS: {unclassified} — '"
        "'decide whether each one writes'); "
        "stale = reads - tools; "
        "assert not stale, f'READ_TOOLS entries with no registered tool: {stale}'; "
        "print(f'OK: {len(tools)} tools classified')",
        readonly=False,
    )
    assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    assert "OK:" in result.stdout


def test_readonly_removes_reconcile_terminology():
    """reconcile_terminology writes terms; a read-only server must not offer it."""
    result = _run_in_subprocess(
        "from wip_mcp.server import mcp; "
        "names = {t.name for t in mcp._tool_manager.list_tools()}; "
        "assert 'reconcile_terminology' not in names; "
        "print('OK')",
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
