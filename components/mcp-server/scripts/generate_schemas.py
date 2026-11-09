#!/usr/bin/env python3
"""Generate MCP tool schemas from WIP OpenAPI specifications.

Reads the committed specs in schemas/ (refresh them from a running install
with scripts/update-schemas.sh), resolves $ref pointers, and produces
src/wip_mcp/_generated_schemas.py with resolved JSON schemas, per-tool field
lists, the parameter mapping, and which spec versions it was built from.
A missing spec, an unresolved $ref or model, or output that does not compile
exits non-zero and writes nothing.

Usage:
    python -m scripts.generate_schemas
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
REPO_ROOT = PROJECT_ROOT.parent.parent
SHARED_SCHEMA_DIR = REPO_ROOT / "schemas"  # Shared with @wip/client
TOOLS_YAML = PROJECT_ROOT / "tools.yaml"
OUTPUT_FILE = PROJECT_ROOT / "src" / "wip_mcp" / "_generated_schemas.py"

SERVICES = ("registry", "def-store", "template-store", "document-store", "reporting-sync")


class GenerationError(Exception):
    """Input the generator cannot turn into a correct module; it exits non-zero."""


# ---------------------------------------------------------------------------
# Load OpenAPI specs
# ---------------------------------------------------------------------------


def load_specs() -> tuple[dict[str, dict], dict[str, str]]:
    """The committed specs in schemas/, and a provenance line for each.

    Refresh them from a running install with scripts/update-schemas.sh; the
    generator never fetches, so it cannot mix fresh and stale specs.
    """
    specs: dict[str, dict] = {}
    provenance: dict[str, str] = {}
    missing = []
    for name in SERVICES:
        spec_file = SHARED_SCHEMA_DIR / f"{name}.json"
        if not spec_file.exists():
            missing.append(str(spec_file))
            continue
        raw = spec_file.read_bytes()
        specs[name] = json.loads(raw)
        version = specs[name].get("info", {}).get("version", "?")
        provenance[name] = f"schemas/{name}.json  version {version}  sha256 {hashlib.sha256(raw).hexdigest()[:16]}"
    if missing:
        raise GenerationError(f"missing specs: {', '.join(missing)} — run scripts/update-schemas.sh")
    return specs, provenance


# ---------------------------------------------------------------------------
# Schema resolution
# ---------------------------------------------------------------------------


def resolve_ref(ref: str, spec: dict) -> dict:
    """Resolve a $ref pointer like '#/components/schemas/Foo'.

    A pointer that leaves the document or does not land on a schema object
    is an error: resolving it to {} would accept anything and silently drop
    the constraint it stood for.
    """
    if not ref.startswith("#/"):
        raise GenerationError(f"external $ref not supported: {ref}")
    node: object = spec
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            raise GenerationError(f"unresolved $ref: {ref}")
        node = node[part]
    if not isinstance(node, dict):
        raise GenerationError(f"$ref does not point at a schema object: {ref}")
    return node


def resolve_schema(schema: dict, spec: dict, seen: set | None = None) -> dict:
    """Recursively resolve $ref pointers in a JSON schema."""
    if seen is None:
        seen = set()

    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen:
            return {"type": "object", "description": "(circular reference)"}
        seen = seen | {ref}
        resolved = resolve_ref(ref, spec)
        return resolve_schema(resolved, spec, seen)

    result = {}
    for key, value in schema.items():
        if key == "title":
            continue  # Strip noise
        if key == "properties" and isinstance(value, dict):
            result[key] = {
                k: resolve_schema(v, spec, seen) for k, v in value.items()
            }
        elif key == "items" and isinstance(value, dict):
            result[key] = resolve_schema(value, spec, seen)
        elif key == "allOf" and isinstance(value, list):
            # Merge allOf schemas
            merged = {}
            for sub in value:
                resolved_sub = resolve_schema(sub, spec, seen)
                for sk, sv in resolved_sub.items():
                    if sk == "properties" and "properties" in merged:
                        merged["properties"].update(sv)
                    elif sk == "required" and "required" in merged:
                        # Keep spec order, without duplicates, so the
                        # output is the same on every run.
                        merged["required"] = list(
                            dict.fromkeys(merged["required"] + list(sv))
                        )
                    else:
                        merged[sk] = sv
            result.update(merged)
        elif key == "anyOf" and isinstance(value, list):
            # Simplify anyOf (common for Optional fields: [Type, null])
            non_null = [
                resolve_schema(v, spec, seen)
                for v in value
                if v != {"type": "null"} and v.get("type") != "null"
            ]
            if len(non_null) == 1:
                result.update(non_null[0])
            else:
                result["anyOf"] = non_null
        elif isinstance(value, dict) and "$ref" in value:
            result[key] = resolve_schema(value, spec, seen)
        else:
            result[key] = value

    return result


def extract_schema(service_name: str, model_name: str, specs: dict) -> dict:
    """Extract and resolve a schema by service name and model name."""
    spec = specs.get(service_name)
    if not spec:
        raise GenerationError(f"no spec for service '{service_name}'")
    schemas = spec.get("components", {}).get("schemas", {})
    if model_name not in schemas:
        raise GenerationError(f"schema '{model_name}' not found in {service_name}")
    return resolve_schema(schemas[model_name], spec)


# ---------------------------------------------------------------------------
# Description generation
# ---------------------------------------------------------------------------


def schema_to_field_docs(
    schema: dict, indent: int = 0, max_depth: int = 2
) -> str:
    """Convert a resolved JSON schema to a human-readable field reference."""
    if max_depth <= 0:
        return f"{'  ' * indent}(nested object — see full schema)"

    lines = []
    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))

    for name, prop in properties.items():
        prefix = "  " * indent
        type_str = _format_type(prop)
        req_str = ", REQUIRED" if name in required_fields else ""
        default_str = ""
        if "default" in prop:
            default_str = f", default: {json.dumps(prop['default'])}"

        desc = prop.get("description", "")
        enum_values = prop.get("enum")
        if enum_values:
            desc = f"One of: {', '.join(str(v) for v in enum_values)}. {desc}"

        line = f"{prefix}{name} ({type_str}{req_str}{default_str})"
        if desc:
            line += f": {desc}"
        lines.append(line)

        # Recurse into nested objects
        if prop.get("type") == "object" and "properties" in prop:
            lines.append(
                schema_to_field_docs(prop, indent + 1, max_depth - 1)
            )
        elif prop.get("type") == "array" and "items" in prop:
            items = prop["items"]
            if items.get("type") == "object" and "properties" in items:
                lines.append(f"{prefix}  Each item:")
                lines.append(
                    schema_to_field_docs(items, indent + 2, max_depth - 1)
                )

    return "\n".join(lines)


def _format_type(prop: dict) -> str:
    """Format a JSON schema type as a readable string."""
    if "enum" in prop:
        return "enum"
    t = prop.get("type", "object")
    if t == "array":
        items = prop.get("items", {})
        item_type = items.get("type", "object")
        if "enum" in items:
            return "array of enum"
        return f"array of {item_type}"
    if "anyOf" in prop:
        types = [_format_type(s) for s in prop["anyOf"]]
        return " | ".join(types)
    return t


def compose_field_docs(tool_config: dict, schemas: dict[str, dict]) -> str | None:
    """The generated field list a tool's docstring is extended with."""
    parts = []

    schema_ref = tool_config.get("openapi_schema")
    if schema_ref and "#" in schema_ref:
        schema = schemas.get(schema_ref)
        if schema:
            field_docs = schema_to_field_docs(schema, indent=0, max_depth=2)
            if field_docs:
                parts.append(f"Fields (from OpenAPI — these are the exact field names):\n{field_docs}")

    # Nested schemas (e.g., FieldDefinition for templates)
    for nested_ref in tool_config.get("nested_schemas", []):
        if nested_ref in schemas:
            nested_schema = schemas[nested_ref]
            nested_name = nested_ref.split("#", 1)[1] if "#" in nested_ref else nested_ref
            field_docs = schema_to_field_docs(nested_schema, indent=1, max_depth=1)
            if field_docs:
                parts.append(f"{nested_name} fields:\n{field_docs}")

    return "\n\n".join(parts) if parts else None


def tool_params(tools_config: dict) -> dict[str, dict]:
    """Per tool: which parameter takes which OpenAPI schema, and its shape."""
    params = {}
    for tool_name, tc in sorted(tools_config.items()):
        schema_ref = tc.get("openapi_schema")
        param_name = tc.get("param_name")
        if schema_ref and param_name:
            params[tool_name] = {
                "param_name": param_name,
                "param_type": tc.get("param_type", "dict"),
                "schema_ref": schema_ref,
            }
    return params


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------


def generate_output(
    resolved_schemas: dict[str, dict],
    field_docs: dict[str, str],
    params: dict[str, dict],
    provenance: dict[str, str],
) -> str:
    """Generate the _generated_schemas.py file content."""
    lines = [
        '"""Auto-generated from WIP OpenAPI specs and tools.yaml. Do not edit manually.',
        "",
        "Regenerate with: python -m scripts.generate_schemas",
        "",
        "The server appends TOOL_FIELD_DOCS[tool] to that tool's docstring and",
        "replaces the parameter TOOL_PARAMS[tool] names with TOOL_SCHEMAS[ref].",
        "",
        "Generated from:",
        *(f"  {line}" for line in provenance.values()),
        '"""',
        "",
        "",
    ]

    lines.append("TOOL_SCHEMAS: dict[str, dict] = " + _format_dict(resolved_schemas))
    lines.append("")
    lines.append("")
    lines.append("TOOL_PARAMS: dict[str, dict] = " + _format_dict(params))
    lines.append("")
    lines.append("")

    # repr() escapes every quote, backslash and control character, so no
    # description text can break the generated module.
    lines.append("TOOL_FIELD_DOCS: dict[str, str] = {")
    for tool_name, doc in sorted(field_docs.items()):
        lines.append(f"    {tool_name!r}: {doc!r},")
    lines.append("}")
    lines.append("")

    return "\n".join(lines)


def _format_dict(d: dict) -> str:
    """Format a dict as valid Python source."""
    import pprint
    return pprint.pformat(d, width=100, sort_dicts=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def resolve_tool_schemas(tools: dict, specs: dict) -> dict[str, dict]:
    """Every model tools.yaml names, resolved; any miss is an error."""
    refs: set[str] = set()
    for tool_name, tc in tools.items():
        for ref in [tc.get("openapi_schema"), *tc.get("nested_schemas", [])]:
            if ref is None:
                continue
            if "#" not in ref:
                raise GenerationError(f"{tool_name}: '{ref}' is not 'service#Model'")
            refs.add(ref)
    resolved = {}
    for ref in sorted(refs):
        service, model = ref.split("#", 1)
        resolved[ref] = extract_schema(service, model, specs)
        print(f"  {ref}: {len(resolved[ref].get('properties', {}))} properties")
    return resolved


def main():
    parser = argparse.ArgumentParser(
        description="Generate MCP tool schemas from the committed OpenAPI specs in schemas/ "
        "(refresh them with scripts/update-schemas.sh)",
    )
    parser.parse_args()

    print("WIP MCP Schema Generator")
    print("=" * 40)
    try:
        specs, provenance = load_specs()
        for line in provenance.values():
            print(f"  {line}")

        tools = yaml.safe_load(TOOLS_YAML.read_text()).get("tools", {})
        print(f"\n{len(tools)} tools in {TOOLS_YAML.name}; resolving schemas...")
        resolved_schemas = resolve_tool_schemas(tools, specs)
    except GenerationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    field_docs: dict[str, str] = {}
    for tool_name, tc in tools.items():
        doc = compose_field_docs(tc, resolved_schemas)
        if doc:
            field_docs[tool_name] = doc
    params = tool_params(tools)

    # Refuse to write a module that does not compile
    output = generate_output(resolved_schemas, field_docs, params, provenance)
    compile(output, str(OUTPUT_FILE), "exec")
    OUTPUT_FILE.write_text(output)
    print(
        f"\nWrote {OUTPUT_FILE}: {len(resolved_schemas)} schemas, "
        f"{len(field_docs)} field lists, {len(params)} parameter mappings."
    )


if __name__ == "__main__":
    main()
