"""The offline validator keeps pace with the live one on shapes and caps.

Provenance: CASE-1275. Live refuses a rule template-store refuses, honours a
mutual_exclusion's conditions, applies a declared enum to date, datetime and
boolean as well as string and number, reads an empty enum as admitting nothing,
and caps a file list at ``max_files`` on both config slots. The archive port
must agree: a pre-upload gate that disagrees with the server is worse than none.
"""

from wip_archive.definitions import DefinitionIndex
from wip_archive.validation import OfflineValidator

NS = "demo"
TPL = "0190c000-0000-7000-0000-000000001275"
FIELDS = [
    {"name": "n", "type": "integer"}, {"name": "s", "type": "string"},
    {"name": "kind", "type": "string"}, {"name": "d", "type": "date"},
    {"name": "t", "type": "datetime"}, {"name": "flag", "type": "boolean"},
    {"name": "files", "type": "array", "array_item_type": "file"},
    {"name": "many", "type": "file", "file_config": {"multiple": True, "max_files": 2}},
    {"name": "untyped", "type": "array"},
]


def _check(data, rules=None, fields=None):
    template = {"template_id": TPL, "version": 1, "value": "ITEM", "namespace": NS,
                "identity_fields": [], "fields": fields or FIELDS, "rules": rules or []}
    return OfflineValidator(DefinitionIndex()).validate(template, data, NS)


def _codes(outcome):
    return [e["code"] for e in outcome.errors]


def test_a_rule_shape_the_platform_refuses_is_refused_not_skipped():
    bad = [
        {"type": "range", "target_field": "n"},
        {"type": "conditional_value", "target_field": "s"},
        {"type": "mutual_exclusion", "target_fields": ["s"]},
        {"type": "dependency", "target_field": "s"},
    ]
    assert _codes(_check({"n": 1, "s": "x"}, bad)) == ["invalid_rule"] * 4


def test_a_rule_that_is_not_an_object_is_refused():
    assert _codes(_check({"n": 1}, ["nope"])) == ["invalid_rule"]


def test_mutual_exclusion_applies_only_when_its_conditions_hold():
    rule = {"type": "mutual_exclusion", "target_fields": ["s", "kind"],
            "conditions": [{"field": "n", "operator": "equals", "value": 1}]}
    assert _codes(_check({"n": 1, "s": "a", "kind": "b"}, [rule])) == ["mutual_exclusion"]
    assert _check({"n": 2, "s": "a", "kind": "b"}, [rule]).valid


def test_enum_holds_for_every_scalar_type():
    fields = [
        {"name": "d", "type": "date", "validation": {"enum": ["2024-01-01"]}},
        {"name": "t", "type": "datetime", "validation": {"enum": ["2024-01-01T10:00:00Z"]}},
        {"name": "flag", "type": "boolean", "validation": {"enum": [True]}},
        {"name": "n", "type": "integer", "validation": {"enum": [1, 2]}},
    ]
    assert _check({"d": "2024-01-01", "t": "2024-01-01T10:00:00Z",
                  "flag": True, "n": 2}, fields=fields).valid
    assert _codes(_check({"d": "2024-06-06", "t": "2024-06-06T10:00:00Z",
                          "flag": False, "n": 9}, fields=fields)) == ["invalid_enum"] * 4


def test_a_declared_empty_enum_admits_nothing():
    fields = [{"name": "s", "type": "string", "validation": {"enum": []}}]
    assert _codes(_check({"s": "x"}, fields=fields)) == ["invalid_enum"]


def test_max_files_caps_both_file_slots():
    assert _codes(_check({"many": ["a", "b", "c"]})) == ["max_files"]
    array_field = [{"name": "files", "type": "array", "array_item_type": "file",
                    "array_file_config": {"max_files": 2}}]
    assert _codes(_check({"files": ["a", "b", "c"]}, fields=array_field)) == ["max_files"]
    assert _check({"files": ["a", "b"]}, fields=array_field).valid


def test_an_array_without_an_item_type_has_untyped_items():
    assert _check({"untyped": [1, "x", True]}, fields=[{"name": "untyped", "type": "array"}]).valid
