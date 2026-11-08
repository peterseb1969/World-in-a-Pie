"""The generated definition defaults match the models they are generated from.

services/_definition_defaults.py is generated from the def-store and
template-store models (scripts/generate_definition_defaults.py) because
document-store cannot import them at runtime. A model default changed
without regenerating would let the merge comparison judge definitions by
stale defaults. Provenance: CASE-1146 — the generated file named this test
before it existed.
"""

import importlib.util
import sys
from pathlib import Path

COMPONENTS = Path(__file__).resolve().parents[2]
GENERATOR = COMPONENTS / "document-store" / "scripts" / "generate_definition_defaults.py"


def test_definition_defaults_contract():
    for component in ("def-store", "template-store"):
        src = str(COMPONENTS / component / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
    spec = importlib.util.spec_from_file_location("generate_definition_defaults", GENERATOR)
    assert spec and spec.loader
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)

    expected = generator.render(generator.build())
    current = generator.OUT.read_text()
    assert current == expected, (
        f"{generator.OUT} is out of date — run "
        "components/document-store/scripts/generate_definition_defaults.py"
    )
