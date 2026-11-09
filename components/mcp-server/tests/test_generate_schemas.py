"""The schema generator fails loudly and its committed output is current."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("yaml")  # the generator's own dependency (the `generate` extra)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import generate_schemas as gen  # noqa: E402


def _committed_output() -> str:
    import yaml

    specs, provenance = gen.load_specs()
    tools = yaml.safe_load(gen.TOOLS_YAML.read_text())["tools"]
    resolved = gen.resolve_tool_schemas(tools, specs)
    docs = {n: d for n, tc in tools.items() if (d := gen.compose_field_docs(tc, resolved))}
    return gen.generate_output(resolved, docs, gen.tool_params(tools), provenance)


def test_committed_generated_module_is_current():
    """_generated_schemas.py is what the generator makes from schemas/ and
    tools.yaml now. When a spec in schemas/ changes, this fails until the
    module is regenerated."""
    assert gen.OUTPUT_FILE.read_text() == _committed_output(), (
        "run: cd components/mcp-server && python -m scripts.generate_schemas"
    )


@pytest.mark.parametrize(
    "ref",
    ["#/components/schemas/Missing", "#/components/schemas/Foo/enum/0", "other.json#/Foo"],
)
def test_an_unresolvable_ref_is_an_error(ref):
    spec = {"components": {"schemas": {"Foo": {"type": "string", "enum": ["a"]}}}}
    with pytest.raises(gen.GenerationError):
        gen.resolve_schema({"$ref": ref}, spec)


def test_an_unknown_model_is_an_error():
    with pytest.raises(gen.GenerationError, match="not found"):
        gen.extract_schema("def-store", "NoSuchModel", {"def-store": {"components": {"schemas": {}}}})


def test_allof_required_keeps_spec_order():
    spec = {"components": {"schemas": {
        "A": {"properties": {"x": {}, "y": {}}, "required": ["y", "x"]},
        "B": {"properties": {"z": {}}, "required": ["z", "y"]},
    }}}
    merged = gen.resolve_schema(
        {"allOf": [{"$ref": "#/components/schemas/A"}, {"$ref": "#/components/schemas/B"}]}, spec,
    )
    assert merged["required"] == ["y", "x", "z"]


def test_any_description_text_generates_a_module_that_compiles():
    tricky = 'ends with a quote" and """triple""" and a backslash \\ and \x00'
    out = gen.generate_output({}, {"t": tricky}, {}, {"x": "schemas/x.json"})
    namespace: dict = {}
    exec(compile(out, "gen", "exec"), namespace)
    assert namespace["TOOL_FIELD_DOCS"]["t"] == tricky
