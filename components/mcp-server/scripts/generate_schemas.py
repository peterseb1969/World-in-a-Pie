#!/usr/bin/env python3
"""Generate MCP tool schemas from WIP OpenAPI specifications.

Fetches OpenAPI specs from running WIP services (or reads cached copies),
resolves $ref pointers, and produces src/wip_mcp/_generated_schemas.py
with resolved JSON schemas and composed tool descriptions.

Usage:
    python -m scripts.generate_schemas --fetch    # Fetch fresh specs + generate
    python -m scripts.generate_schemas            # Generate from cached specs
"""

import argparse
import json
import sys
import textwrap
from pathlib import Path

import httpx
import yaml

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
REPO_ROOT = PROJECT_ROOT.parent.parent
SHARED_SCHEMA_DIR = REPO_ROOT / "schemas"  # Shared with @wip/client
LOCAL_CACHE_DIR = PROJECT_ROOT / "openapi_cache"  # Fallback
TOOLS_YAML = PROJECT_ROOT / "tools.yaml"
OUTPUT_FILE = PROJECT_ROOT / "src" / "wip_mcp" / "_generated_schemas.py"

SERVICES = {
    "registry": {"port": 8001, "path": "/openapi.json"},
    "def-store": {"port": 8002, "path": "/openapi.json"},
    "template-store": {"port": 8003, "path": "/openapi.json"},
    "document-store": {"port": 8004, "path": "/openapi.json"},
    "reporting-sync": {"port": 8005, "path": "/openapi.json"},
}


# ---------------------------------------------------------------------------
# Fetch OpenAPI specs
# ---------------------------------------------------------------------------


def fetch_specs(base_url: str = "http://localhost") -> dict[str, dict]:
    """Fetch OpenAPI specs from running services into the shared schemas/ dir.

    Use scripts/update-schemas.sh for the canonical way to refresh specs.
    This --fetch flag is a convenience for MCP-server-only workflows.
    """
    specs = {}
    SHARED_SCHEMA_DIR.mkdir(exist_ok=True)

    for name, cfg in SERVICES.items():
        url = f"{base_url}:{cfg['port']}{cfg['path']}"
        print(f"  Fetching {name} from {url}...", end=" ")
        try:
            resp = httpx.get(url, timeout=10.0)
            resp.raise_for_status()
            spec = resp.json()
            specs[name] = spec
            # Write to shared schemas/ directory
            cache_file = SHARED_SCHEMA_DIR / f"{name}.json"
            cache_file.write_text(json.dumps(spec, indent=2))
            print(f"OK ({len(spec.get('paths', {}))} paths)")
        except Exception as e:
            print(f"FAILED: {e}")
            # Fall back to existing cached version
            for d in [SHARED_SCHEMA_DIR, LOCAL_CACHE_DIR]:
                cache_file = d / f"{name}.json"
                if cache_file.exists():
                    print(f"    Using cached version from {d.name}/")
                    specs[name] = json.loads(cache_file.read_text())
                    break
            else:
                print(f"    No cache available, skipping")

    return specs


def load_cached_specs() -> dict[str, dict]:
    """Load OpenAPI specs from shared schemas/ dir (or local fallback)."""
    specs = {}
    for name in SERVICES:
        for d in [SHARED_SCHEMA_DIR, LOCAL_CACHE_DIR]:
            cache_file = d / f"{name}.json"
            if cache_file.exists():
                specs[name] = json.loads(cache_file.read_text())
                break
        else:
            print(f"  Warning: no cached spec for {name}")
    return specs


# ---------------------------------------------------------------------------
# Schema resolution
# ---------------------------------------------------------------------------


def resolve_ref(ref: str, spec: dict) -> dict:
    """Resolve a $ref pointer like '#/components/schemas/Foo'."""
    if not ref.startswith("#/"):
        return {"type": "object", "description": f"Unresolved ref: {ref}"}
    parts = ref.lstrip("#/").split("/")
    node = spec
    for part in parts:
        node = node.get(part, {})
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
                        merged["required"] = list(
                            set(merged["required"]) | set(sv)
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


def extract_schema(service_name: str, model_name: str, specs: dict) -> dict | None:
    """Extract and resolve a schema by service name and model name."""
    spec = specs.get(service_name)
    if not spec:
        print(f"  Warning: no spec for service '{service_name}'")
        return None

    schemas = spec.get("components", {}).get("schemas", {})
    if model_name not in schemas:
        print(f"  Warning: schema '{model_name}' not found in {service_name}")
        # Try case-insensitive match
        for name in schemas:
            if name.lower() == model_name.lower():
                print(f"    Found case-insensitive match: {name}")
                model_name = name
                break
        else:
            return None

    raw = schemas[model_name]
    return resolve_schema(raw, spec)


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
            return f"array of enum"
        return f"array of {item_type}"
    if "anyOf" in prop:
        types = [_format_type(s) for s in prop["anyOf"]]
        return " | ".join(types)
    return t


def compose_description(
    tool_config: dict,
    schemas: dict[str, dict],
    specs: dict,
) -> str | None:
    """Compose a tool description from hand-written prose + generated schema."""
    parts = []

    # Hand-written description
    desc = tool_config.get("description", "")
    if desc:
        parts.append(desc.strip())

    # Hand-written notes (gotchas, conventions)
    notes = tool_config.get("notes", "")
    if notes:
        parts.append(notes.strip())

    # Generated field reference from OpenAPI
    schema_ref = tool_config.get("openapi_schema")
    if schema_ref and "#" in schema_ref:
        service, model = schema_ref.split("#", 1)
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


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------


def generate_output(
    tools_config: dict,
    resolved_schemas: dict[str, dict],
    descriptions: dict[str, str],
) -> str:
    """Generate the _generated_schemas.py file content."""
    lines = [
        '"""Auto-generated from WIP OpenAPI specs. Do not edit manually.',
        "",
        "Regenerate with: python -m scripts.generate_schemas [--fetch]",
        '"""',
        "",
        "",
    ]

    # TOOL_SCHEMAS dict
    lines.append("TOOL_SCHEMAS: dict[str, dict] = " + _format_dict(resolved_schemas))
    lines.append("")
    lines.append("")

    # TOOL_DESCRIPTIONS dict
    lines.append("TOOL_DESCRIPTIONS: dict[str, str] = {")
    for tool_name, desc in sorted(descriptions.items()):
        escaped = desc.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        lines.append(f'    {tool_name!r}: """{escaped}""",')
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


def main():
    parser = argparse.ArgumentParser(description="Generate MCP tool schemas from OpenAPI")
    parser.add_argument(
        "--fetch", action="store_true", help="Fetch fresh specs from running services"
    )
    parser.add_argument(
        "--base-url", default="http://localhost", help="Base URL for services"
    )
    args = parser.parse_args()

    print("WIP MCP Schema Generator")
    print("=" * 40)

    # Load or fetch specs
    if args.fetch:
        print("\nFetching OpenAPI specs...")
        specs = fetch_specs(args.base_url)
    else:
        print("\nLoading cached OpenAPI specs...")
        specs = load_cached_specs()

    if not specs:
        print("ERROR: No OpenAPI specs available. Run with --fetch first.")
        sys.exit(1)

    print(f"\nLoaded specs for: {', '.join(specs.keys())}")

    # Load tools config
    print(f"\nLoading tools config from {TOOLS_YAML}...")
    config = yaml.safe_load(TOOLS_YAML.read_text())
    tools = config.get("tools", {})
    print(f"  Found {len(tools)} tool definitions")

    # Extract and resolve all referenced schemas
    print("\nResolving schemas...")
    resolved_schemas: dict[str, dict] = {}
    schema_refs: set[str] = set()

    for tool_name, tc in tools.items():
        ref = tc.get("openapi_schema")
        if ref:
            schema_refs.add(ref)
        for nested in tc.get("nested_schemas", []):
            schema_refs.add(nested)

    for ref in sorted(schema_refs):
        if "#" not in ref:
            continue
        service, model = ref.split("#", 1)
        schema = extract_schema(service, model, specs)
        if schema:
            resolved_schemas[ref] = schema
            prop_count = len(schema.get("properties", {}))
            print(f"  {ref}: {prop_count} properties")
        else:
            print(f"  {ref}: NOT FOUND")

    # Compose descriptions
    print("\nComposing tool descriptions...")
    descriptions: dict[str, str] = {}
    for tool_name, tc in tools.items():
        desc = compose_description(tc, resolved_schemas, specs)
        if desc:
            descriptions[tool_name] = desc
            print(f"  {tool_name}: {len(desc)} chars")

    # Generate output
    print(f"\nWriting {OUTPUT_FILE}...")
    output = generate_output(tools, resolved_schemas, descriptions)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(output)
    print(f"  Written {len(output)} bytes")

    # Summary
    print(f"\nDone. {len(resolved_schemas)} schemas resolved, {len(descriptions)} descriptions composed.")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
