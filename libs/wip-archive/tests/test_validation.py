"""The offline validator on hand-built definitions.

Agreement with the live validator is proven by the toolkit's integration
comparison suite (`WIP-Toolkit/tests/integration/test_offline_validation_parity.py`).
These tests cover what that suite cannot reach without a platform: what the
archive cannot answer, and the resolver's corners.
"""

from wip_archive.definitions import DefinitionIndex
from wip_archive.validation import OfflineValidator

NS = "demo"
LOV = "0190a000-0000-7000-0000-0000000000c1"
TPL = "0190c000-0000-7000-0000-0000000000t1"
NESTED = "0190c000-0000-7000-0000-0000000000n1"


def _terminology(tid=LOV, value="COLOUR", ns=NS, case_sensitive=False):
    return {"terminology_id": tid, "value": value, "namespace": ns,
            "case_sensitive": case_sensitive, "status": "active"}


def _term(term_id, value, *, names=(), status="active", created="2026-01-01", tid=LOV, ns=NS):
    return {"term_id": term_id, "terminology_id": tid, "namespace": ns, "value": value,
            "status": status, "created_at": created,
            "names": [{"value": n} for n in names]}


def _index(terminologies=(), terms=(), templates=()):
    index = DefinitionIndex()
    for row in terminologies:
        index.add_terminology(row)
    for row in terms:
        index.add_term(row)
    for row in templates:
        index.add_template(row)
    return index


def _template(fields, *, identity=(), rules=(), template_id=TPL, value="ITEM"):
    return {"template_id": template_id, "version": 1, "value": value, "namespace": NS,
            "identity_fields": list(identity), "fields": fields, "rules": list(rules)}


def _validate(index, template, data):
    return OfflineValidator(index).validate(template, data, NS)


def _codes(outcome):
    return {(e["field"], e["code"]) for e in outcome.errors}


COLOUR_FIELD = {"name": "colour", "type": "term", "terminology_ref": "COLOUR"}


class TestTermResolution:
    def test_a_value_wins_over_another_terms_name(self):
        index = _index([_terminology()], [
            _term("t-red", "red", names=["blue"]),
            _term("t-blue", "blue", created="2026-02-01"),
        ])
        outcome = _validate(index, _template([COLOUR_FIELD]), {"colour": "blue"})
        assert outcome.valid
        assert outcome.term_references[0]["term_id"] == "t-blue"
        assert outcome.term_references[0]["matched_via"] == "value"

    def test_case_follows_the_terminology(self):
        sensitive = _index([_terminology(case_sensitive=True)], [_term("t-red", "red")])
        assert _codes(_validate(sensitive, _template([COLOUR_FIELD]), {"colour": "RED"})) == {
            ("colour", "invalid_term"),
        }
        insensitive = _index([_terminology()], [_term("t-red", "red")])
        assert _validate(insensitive, _template([COLOUR_FIELD]), {"colour": "RED"}).valid

    def test_ambiguity_fails_an_identity_field_and_warns_elsewhere(self):
        index = _index([_terminology()], [
            _term("t-red", "red", names=["crimson"]),
            _term("t-maroon", "maroon", names=["crimson"], created="2026-02-01"),
        ])
        identity = _validate(index, _template([COLOUR_FIELD], identity=["colour"]), {"colour": "crimson"})
        assert _codes(identity) == {("colour", "ambiguous_term")}
        plain = _validate(index, _template([COLOUR_FIELD]), {"colour": "crimson"})
        assert plain.valid and any("ambiguous" in w for w in plain.warnings)
        # The first-created bearer is recorded.
        assert plain.term_references[0]["term_id"] == "t-red"

    def test_a_miss_suggests_a_prefix_match(self):
        index = _index([_terminology()], [_term("t-red", "redwood")])
        outcome = _validate(index, _template([COLOUR_FIELD]), {"colour": "red"})
        assert outcome.errors[0]["details"]["suggestion"] == {"term_id": "t-red", "value": "redwood"}

    def test_retired_terms_do_not_match_new_values(self):
        index = _index([_terminology()], [_term("t-grey", "grey", status="deprecated")])
        assert _codes(_validate(index, _template([COLOUR_FIELD]), {"colour": "grey"})) == {
            ("colour", "invalid_term"),
        }


class TestWhatTheArchiveCannotAnswer:
    def test_a_terminology_in_an_uncarried_namespace_is_unverifiable(self):
        index = _index(templates=[_template([])])
        field = {"name": "unit", "type": "term", "terminology_ref": "wip:UNITS"}
        outcome = _validate(index, _template([field]), {"unit": "kg"})
        assert outcome.valid
        assert outcome.unverifiable == [{"kind": "terminology", "ref": "wip:UNITS", "field": "unit"}]

    def test_a_terminology_id_the_archive_lacks_is_unverifiable(self):
        index = _index(templates=[_template([])])
        field = {"name": "unit", "type": "term",
                 "terminology_ref": "0190a000-0000-7000-0000-0000000000ff"}
        outcome = _validate(index, _template([field]), {"unit": "kg"})
        assert outcome.valid and outcome.unverifiable[0]["kind"] == "terminology"

    def test_a_bare_value_missing_from_a_carried_namespace_is_invalid(self):
        # The document's own namespace is in the archive: absence is proven.
        index = _index([_terminology()], templates=[_template([])])
        field = {"name": "size", "type": "term", "terminology_ref": "SIZE"}
        assert _codes(_validate(index, _template([field]), {"size": "L"})) == {("size", "invalid_term")}

    def test_a_nested_template_outside_the_archive_is_unverifiable(self):
        index = _index()
        field = {"name": "home", "type": "object", "template_ref": "wip:ADDRESS",
                 "template_ref_version": 1}
        outcome = _validate(index, _template([field]), {"home": {"anything": 1}})
        assert outcome.valid
        assert outcome.unverifiable == [{"kind": "template", "ref": "wip:ADDRESS", "field": "home"}]

    def test_duration_units_need_the_wip_namespace(self):
        field = {"name": "took", "type": "object", "semantic_type": "duration"}
        outside = _validate(_index(), _template([field]), {"took": {"value": 3, "unit": "hours"}})
        assert outside.valid
        assert outside.unverifiable == [
            {"kind": "terminology", "ref": "wip:_TIME_UNITS", "field": "took.unit"},
        ]
        units = _terminology(tid="lov-units", value="_TIME_UNITS", ns="wip")
        carried = _index([units], [_term("t-h", "hours", tid="lov-units", ns="wip")])
        assert _validate(carried, _template([field]), {"took": {"value": 3, "unit": "hours"}}).valid
        assert _codes(_validate(carried, _template([field]), {"took": {"value": 3, "unit": "eons"}})) == {
            ("took.unit", "invalid_duration_unit"),
        }


class TestFields:
    def test_a_qualified_terminology_ref_crosses_namespaces(self):
        other = _terminology(tid="lov-other", value="SIZE", ns="shared")
        index = _index([_terminology(), other], [_term("t-l", "L", tid="lov-other", ns="shared")])
        field = {"name": "size", "type": "term", "terminology_ref": "shared:SIZE"}
        assert _validate(index, _template([field]), {"size": "L"}).valid

    def test_nested_templates_validate_at_their_pinned_version(self):
        nested_v1 = _template([{"name": "street", "type": "string", "mandatory": True}],
                              template_id=NESTED, value="ADDRESS")
        nested_v2 = {**nested_v1, "version": 2, "fields": []}
        index = _index(templates=[nested_v1, nested_v2])
        field = {"name": "home", "type": "object", "template_ref": "ADDRESS",
                 "template_ref_version": 1}
        assert _codes(_validate(index, _template([field]), {"home": {}})) == {
            ("home.street", "required"),
        }

    def test_an_unknown_field_type_is_not_checked_and_cannot_reach_internals(self):
        fields = [{"name": "x", "type": "fields"}, {"name": "y", "type": "terms"}]
        assert _validate(_index(), _template(fields), {"x": 1, "y": 2}).valid

    def test_stages_stop_at_the_first_with_errors(self):
        # A field error hides the rule error, as live.
        rule = {"type": "mutual_exclusion", "target_fields": ["a", "b"]}
        fields = [{"name": "a", "type": "integer"}, {"name": "b", "type": "integer"}]
        outcome = _validate(_index(), _template(fields, rules=[rule]), {"a": "x", "b": 2})
        assert _codes(outcome) == {("a", "invalid_type")}

    def test_non_dict_data_is_a_structural_error(self):
        assert _codes(_validate(_index(), _template([]), [1])) == {(None, "invalid_structure")}
