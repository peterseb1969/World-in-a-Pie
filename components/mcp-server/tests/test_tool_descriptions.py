"""What the server serves as a tool's description and parameter schema.

A tool's prose is its docstring. For the tools tools.yaml maps to an OpenAPI
model, the generated field list is appended to the docstring and the mapped
parameter takes the OpenAPI schema. All of it comes from
_generated_schemas.py inside the package, so a container (which has no
tools.yaml and no PyYAML) serves exactly what a source checkout serves.
"""

import inspect
import os
import subprocess
import sys

import pytest

from wip_mcp import server
from wip_mcp._generated_schemas import TOOL_FIELD_DOCS, TOOL_PARAMS, TOOL_SCHEMAS

MCP_SRC = os.path.join(os.path.dirname(__file__), "..", "src")


def _tools() -> dict:
    return {t.name: t for t in server.mcp._tool_manager.list_tools()}


@pytest.mark.parametrize("tool_name", sorted(TOOL_FIELD_DOCS))
def test_served_description_is_docstring_plus_field_list(tool_name):
    tool = _tools()[tool_name]
    docstring = inspect.getdoc(getattr(server, tool_name))
    assert tool.description.startswith(docstring.split("\n", 1)[0])
    assert tool.description.endswith(TOOL_FIELD_DOCS[tool_name])
    # Every line of the docstring survives (FastMCP keeps its own spacing).
    for line in docstring.splitlines():
        assert line.strip() in tool.description, line


@pytest.mark.parametrize("tool_name", sorted(TOOL_PARAMS))
def test_mapped_parameter_takes_the_openapi_schema(tool_name):
    mapping = TOOL_PARAMS[tool_name]
    prop = _tools()[tool_name].parameters["properties"][mapping["param_name"]]
    schema = TOOL_SCHEMAS[mapping["schema_ref"]]
    if mapping["param_type"] == "list":
        assert prop["type"] == "array"
        assert prop["items"] == schema
    else:
        assert prop["properties"] == schema["properties"]


def test_the_server_serves_the_same_without_pyyaml_or_tools_yaml():
    """The container has neither; the patch must not depend on them."""
    code = (
        "import sys; sys.modules['yaml'] = None\n"
        "from wip_mcp.server import mcp\n"
        "from wip_mcp._generated_schemas import TOOL_FIELD_DOCS\n"
        "tools = {t.name: t for t in mcp._tool_manager.list_tools()}\n"
        "for name, doc in TOOL_FIELD_DOCS.items():\n"
        "    assert tools[name].description.endswith(doc), name\n"
        "print('OK', len(TOOL_FIELD_DOCS))\n"
    )
    env = dict(os.environ, PYTHONPATH=MCP_SRC)
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True,
        cwd="/",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("OK 8")
