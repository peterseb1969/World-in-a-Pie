"""Tests for the ID remapping engine."""

from wip_archive.remap import IDRemapper


class TestIDRemapper:
    """Test ID remapping for templates and documents."""

    def setup_method(self):
        self.remapper = IDRemapper()
        self.remapper.add_terminology_mapping("0190a000-0000-7000-0000-000000000001", "0190e000-0000-7000-0000-000000000001")
        self.remapper.add_terminology_mapping("0190a000-0000-7000-0000-000000000002", "0190e000-0000-7000-0000-000000000002")
        self.remapper.add_term_mapping("0190b000-0000-7000-0000-000000000001", "0190e000-0000-7000-0000-000000000011")
        self.remapper.add_term_mapping("0190b000-0000-7000-0000-000000000003", "0190e000-0000-7000-0000-000000000013")
        self.remapper.add_template_mapping("0190c000-0000-7000-0000-000000000001", "0190e000-0000-7000-0000-000000000021")
        self.remapper.add_template_mapping("0190c000-0000-7000-0000-000000000002", "0190e000-0000-7000-0000-000000000022")
        self.remapper.add_document_mapping(
            "019abc00-0000-7000-8000-000000000001",
            "019def00-0000-7000-8000-000000000001",
        )
        self.remapper.add_file_mapping("FILE-000001", "0190e000-0000-7000-0000-000000000031")

    def test_total_mappings(self):
        assert self.remapper.total_mappings == 8

    # --- Template remapping ---

    def test_remap_template_extends(self):
        tpl = {"template_id": "TPL-X", "extends": "0190c000-0000-7000-0000-000000000001", "fields": []}
        result = self.remapper.remap_template(tpl)
        assert result["extends"] == "0190e000-0000-7000-0000-000000000021"

    def test_remap_template_extends_none(self):
        tpl = {"template_id": "TPL-X", "extends": None, "fields": []}
        result = self.remapper.remap_template(tpl)
        assert result["extends"] is None

    def test_remap_template_extends_unknown_passthrough(self):
        tpl = {"template_id": "TPL-X", "extends": "TPL-UNKNOWN", "fields": []}
        result = self.remapper.remap_template(tpl)
        assert result["extends"] == "TPL-UNKNOWN"

    def test_remap_template_endpoint_entries(self):
        """A two-half endpoint entry: resolved rides the map, lookup stays.

        The resolved half is an absolute canonical id — without the rewrite
        the declaration keeps naming the SOURCE install's template after a
        fresh restore. The lookup half is the submitted anchor; a bare one is
        namespace-agnostic and survives verbatim.
        """
        tpl = {
            "template_id": "TPL-EDGE",
            "usage": "relationship",
            "source_templates": [
                {"lookup_value": "MONSTER",
                 "resolved": "0190c000-0000-7000-0000-000000000001"}
            ],
            "target_templates": [
                {"lookup_value": "0190c000-0000-7000-0000-000000000002",
                 "resolved": "0190c000-0000-7000-0000-000000000002"}
            ],
            "fields": [],
        }
        result = self.remapper.remap_template(tpl)
        assert result["source_templates"] == [
            {"lookup_value": "MONSTER",
             "resolved": "0190e000-0000-7000-0000-000000000021"}
        ]
        # An id-form lookup is itself a mapped reference string and follows
        # its entity (an old id kept anywhere points into the source).
        assert result["target_templates"] == [
            {"lookup_value": "0190e000-0000-7000-0000-000000000022",
             "resolved": "0190e000-0000-7000-0000-000000000022"}
        ]

    def test_remap_template_endpoints_out_of_archive_passthrough(self):
        """An endpoint outside the archive was never re-minted — pass through.

        No map knows its resolved half, so it survives here UNCHANGED; making
        it honest (repair via the lookup on the target, or null + warning) is
        the restore door's job, not the remapper's. The lookup half keeps the
        submitted anchor that the door will re-resolve.
        """
        tpl = {
            "template_id": "TPL-EDGE",
            "usage": "relationship",
            "source_templates": [
                {"lookup_value": "OUTSIDE_TPL",
                 "resolved": "0190dead-0000-7000-0000-00000000beef"}
            ],
            "target_templates": [],
            "fields": [],
        }
        result = self.remapper.remap_template(tpl)
        assert result["source_templates"] == [
            {"lookup_value": "OUTSIDE_TPL",
             "resolved": "0190dead-0000-7000-0000-00000000beef"}
        ]
        assert result["target_templates"] == []

    def test_remap_template_endpoint_legacy_strings(self):
        """A pre-entry archive holds bare strings — reference-string rules.

        Id-form follows the map (the CASE-830 defect population), value-form
        passes through (it re-resolves in the target namespace), qualified
        form follows the namespace map.
        """
        remapper = IDRemapper(namespace_map={"oldns": "newns"})
        remapper.add_template_mapping(
            "0190c000-0000-7000-0000-000000000001",
            "0190e000-0000-7000-0000-000000000021",
        )
        tpl = {
            "template_id": "TPL-EDGE",
            "usage": "relationship",
            "source_templates": [
                "0190c000-0000-7000-0000-000000000001",  # id-form → remapped
                "MONSTER",                                # value-form → stays
                "oldns:SPELL",                            # qualified → ns half
            ],
            "target_templates": [],
            "fields": [],
        }
        result = remapper.remap_template(tpl)
        assert result["source_templates"] == [
            "0190e000-0000-7000-0000-000000000021",
            "MONSTER",
            "newns:SPELL",
        ]

    def test_remap_template_entity_has_no_endpoints(self):
        tpl = {"template_id": "TPL-X", "fields": []}
        result = self.remapper.remap_template(tpl)
        assert "source_templates" not in result

    def test_remap_template_terminology_ref(self):
        tpl = {
            "template_id": "TPL-X",
            "fields": [
                {"name": "f1", "type": "term", "terminology_ref": "0190a000-0000-7000-0000-000000000001"},
            ],
        }
        result = self.remapper.remap_template(tpl)
        assert result["fields"][0]["terminology_ref"] == "0190e000-0000-7000-0000-000000000001"

    def test_remap_template_array_terminology_ref(self):
        tpl = {
            "template_id": "TPL-X",
            "fields": [
                {"name": "f1", "type": "array", "array_terminology_ref": "0190a000-0000-7000-0000-000000000002"},
            ],
        }
        result = self.remapper.remap_template(tpl)
        assert result["fields"][0]["array_terminology_ref"] == "0190e000-0000-7000-0000-000000000002"

    def test_remap_template_template_ref(self):
        tpl = {
            "template_id": "TPL-X",
            "fields": [
                {"name": "f1", "type": "object", "template_ref": "0190c000-0000-7000-0000-000000000002"},
            ],
        }
        result = self.remapper.remap_template(tpl)
        assert result["fields"][0]["template_ref"] == "0190e000-0000-7000-0000-000000000022"

    def test_remap_template_array_template_ref(self):
        tpl = {
            "template_id": "TPL-X",
            "fields": [
                {"name": "f1", "type": "array", "array_template_ref": "0190c000-0000-7000-0000-000000000001"},
            ],
        }
        result = self.remapper.remap_template(tpl)
        assert result["fields"][0]["array_template_ref"] == "0190e000-0000-7000-0000-000000000021"

    def test_remap_template_target_templates(self):
        tpl = {
            "template_id": "TPL-X",
            "fields": [
                {
                    "name": "f1",
                    "type": "reference",
                    "target_templates": ["0190c000-0000-7000-0000-000000000001", "0190c000-0000-7000-0000-000000000002", "TPL-UNKNOWN"],
                },
            ],
        }
        result = self.remapper.remap_template(tpl)
        assert result["fields"][0]["target_templates"] == [
            "0190e000-0000-7000-0000-000000000021", "0190e000-0000-7000-0000-000000000022", "TPL-UNKNOWN",
        ]

    def test_remap_template_target_terminologies(self):
        tpl = {
            "template_id": "TPL-X",
            "fields": [
                {
                    "name": "f1",
                    "type": "reference",
                    "target_terminologies": ["0190a000-0000-7000-0000-000000000001", "TERM-UNKNOWN"],
                },
            ],
        }
        result = self.remapper.remap_template(tpl)
        assert result["fields"][0]["target_terminologies"] == [
            "0190e000-0000-7000-0000-000000000001", "TERM-UNKNOWN",
        ]

    def test_remap_template_preserves_other_fields(self):
        tpl = {
            "template_id": "TPL-X",
            "value": "TEST",
            "label": "Test Template",
            "version": 3,
            "extends_version": 2,
            "identity_fields": ["email"],
            "fields": [
                {"name": "f1", "type": "string", "mandatory": True},
            ],
        }
        result = self.remapper.remap_template(tpl)
        assert result["value"] == "TEST"
        assert result["label"] == "Test Template"
        assert result["version"] == 3
        assert result["extends_version"] == 2
        assert result["identity_fields"] == ["email"]
        assert result["fields"][0]["name"] == "f1"
        assert result["fields"][0]["mandatory"] is True

    # --- Document remapping ---

    def test_remap_document_template_id(self):
        doc = {
            "document_id": "DOC-X",
            "template_id": "0190c000-0000-7000-0000-000000000001",
            "data": {},
        }
        result = self.remapper.remap_document(doc)
        assert result["template_id"] == "0190e000-0000-7000-0000-000000000021"

    def test_remap_document_term_references(self):
        doc = {
            "document_id": "DOC-X",
            "template_id": "0190c000-0000-7000-0000-000000000001",
            "data": {},
            "term_references": [
                {"field_path": "country", "term_id": "0190b000-0000-7000-0000-000000000001", "terminology_ref": "0190a000-0000-7000-0000-000000000001"},
            ],
        }
        result = self.remapper.remap_document(doc)
        assert result["term_references"][0]["term_id"] == "0190e000-0000-7000-0000-000000000011"
        assert result["term_references"][0]["terminology_ref"] == "0190e000-0000-7000-0000-000000000001"

    def test_remap_document_references(self):
        doc = {
            "document_id": "DOC-X",
            "template_id": "0190c000-0000-7000-0000-000000000001",
            "data": {},
            "references": [
                {
                    "field_path": "manager",
                    "resolved": {
                        "document_id": "019abc00-0000-7000-8000-000000000001",
                        "template_id": "0190c000-0000-7000-0000-000000000001",
                        "identity_hash": "hash1",
                    },
                },
            ],
        }
        result = self.remapper.remap_document(doc)
        resolved = result["references"][0]["resolved"]
        assert resolved["document_id"] == "019def00-0000-7000-8000-000000000001"
        assert resolved["template_id"] == "0190e000-0000-7000-0000-000000000021"
        assert resolved["identity_hash"] == "hash1"  # Pass through

    def test_remap_document_file_references(self):
        doc = {
            "document_id": "DOC-X",
            "template_id": "0190c000-0000-7000-0000-000000000001",
            "data": {},
            "file_references": [
                {"field_path": "avatar", "file_id": "FILE-000001"},
            ],
        }
        result = self.remapper.remap_document(doc)
        assert result["file_references"][0]["file_id"] == "0190e000-0000-7000-0000-000000000031"

    def test_remap_document_unknown_ids_passthrough(self):
        doc = {
            "document_id": "DOC-X",
            "template_id": "TPL-UNKNOWN",
            "data": {},
            "term_references": [
                {"field_path": "f1", "term_id": "T-UNKNOWN", "terminology_ref": "TERM-UNKNOWN"},
            ],
            "references": [
                {
                    "field_path": "ref",
                    "resolved": {
                        "document_id": "DOC-UNKNOWN",
                        "template_id": "TPL-UNKNOWN",
                    },
                },
            ],
            "file_references": [
                {"field_path": "file", "file_id": "FILE-UNKNOWN"},
            ],
        }
        result = self.remapper.remap_document(doc)
        assert result["template_id"] == "TPL-UNKNOWN"
        assert result["term_references"][0]["term_id"] == "T-UNKNOWN"
        assert result["references"][0]["resolved"]["document_id"] == "DOC-UNKNOWN"
        assert result["file_references"][0]["file_id"] == "FILE-UNKNOWN"

    def test_remap_document_preserves_data(self):
        doc = {
            "document_id": "DOC-X",
            "template_id": "0190c000-0000-7000-0000-000000000001",
            "data": {"name": "John", "email": "john@example.com"},
            "version": 2,
            "identity_hash": "abc",
        }
        result = self.remapper.remap_document(doc)
        assert result["data"] == {"name": "John", "email": "john@example.com"}
        assert result["version"] == 2
        assert result["identity_hash"] == "abc"

    # --- Data-value id rewriting (recursive walk) ---

    def test_remap_data_scalar_document_id(self):
        doc = {
            "document_id": "DOC-X",
            "data": {"link": "019abc00-0000-7000-8000-000000000001"},
        }
        result = self.remapper.remap_document(doc)
        assert result["data"]["link"] == "019def00-0000-7000-8000-000000000001"

    def test_remap_data_array_of_reference_ids(self):
        doc = {
            "document_id": "DOC-X",
            "data": {
                "kb_refs": [
                    "019abc00-0000-7000-8000-000000000001",
                    "not-an-archived-id",
                ],
            },
        }
        result = self.remapper.remap_document(doc)
        assert result["data"]["kb_refs"] == [
            "019def00-0000-7000-8000-000000000001",
            "not-an-archived-id",
        ]

    def test_remap_data_id_nested_in_object_and_array_of_objects(self):
        doc = {
            "document_id": "DOC-X",
            "data": {
                "meta": {"source_doc": "019abc00-0000-7000-8000-000000000001"},
                "rows": [
                    {"file": "FILE-000001", "count": 3},
                ],
            },
        }
        result = self.remapper.remap_document(doc)
        assert result["data"]["meta"]["source_doc"] == "019def00-0000-7000-8000-000000000001"
        assert result["data"]["rows"][0]["file"] == "0190e000-0000-7000-0000-000000000031"
        assert result["data"]["rows"][0]["count"] == 3

    def test_remap_data_non_string_values_untouched(self):
        doc = {
            "document_id": "DOC-X",
            "data": {"n": 42, "flag": True, "none": None, "tags": [1, 2.5, False]},
        }
        result = self.remapper.remap_document(doc)
        assert result["data"] == {"n": 42, "flag": True, "none": None, "tags": [1, 2.5, False]}

    def test_remap_data_does_not_mutate_input(self):
        data = {"kb_refs": ["019abc00-0000-7000-8000-000000000001"], "meta": {"k": "v"}}
        doc = {"document_id": "DOC-X", "data": data}
        self.remapper.remap_document(doc)
        assert data["kb_refs"] == ["019abc00-0000-7000-8000-000000000001"]
        assert data["meta"] == {"k": "v"}

    def test_remap_document_empty_refs(self):
        doc = {
            "document_id": "DOC-X",
            "template_id": "0190c000-0000-7000-0000-000000000001",
            "data": {},
            "term_references": [],
            "references": [],
            "file_references": [],
        }
        result = self.remapper.remap_document(doc)
        assert result["term_references"] == []
        assert result["references"] == []
        assert result["file_references"] == []



class TestRemapTerm:
    """A term's parent terminology is its only outward reference — and it is
    load-bearing: left unmapped, the imported term is orphaned."""

    def setup_method(self):
        self.remapper = IDRemapper()
        self.remapper.add_terminology_mapping("old-lov", "new-lov")

    def test_terminology_id_is_remapped(self):
        result = self.remapper.remap_term(
            {"term_id": "T1", "terminology_id": "old-lov", "value": "M"}
        )
        assert result["terminology_id"] == "new-lov"

    def test_unmapped_terminology_passes_through(self):
        result = self.remapper.remap_term(
            {"term_id": "T1", "terminology_id": "untouched", "value": "M"}
        )
        assert result["terminology_id"] == "untouched"

    def test_other_fields_are_untouched_and_input_is_not_mutated(self):
        source = {"term_id": "T1", "terminology_id": "old-lov", "value": "M"}
        result = self.remapper.remap_term(source)
        assert result["term_id"] == "T1" and result["value"] == "M"
        assert source["terminology_id"] == "old-lov"


class TestRemapTermRelation:
    def setup_method(self):
        self.remapper = IDRemapper()
        self.remapper.add_term_mapping("old-a", "new-a")
        self.remapper.add_term_mapping("old-b", "new-b")
        self.remapper.add_terminology_mapping("old-lov", "new-lov")

    def test_both_endpoints_are_remapped(self):
        result = self.remapper.remap_term_relation(
            {"source_term_id": "old-a", "target_term_id": "old-b",
             "relation_type": "is_a"}
        )
        assert result["source_term_id"] == "new-a"
        assert result["target_term_id"] == "new-b"

    def test_denormalized_terminologies_are_remapped(self):
        result = self.remapper.remap_term_relation({
            "source_term_id": "old-a", "target_term_id": "old-b",
            "source_terminology_id": "old-lov",
            "target_terminology_id": "old-lov",
        })
        assert result["source_terminology_id"] == "new-lov"
        assert result["target_terminology_id"] == "new-lov"

    def test_relation_type_as_a_value_passes_through(self):
        # relation_type holds a term ID *or* a plain value depending on how
        # the relation was created; a value must survive the term lookup.
        result = self.remapper.remap_term_relation(
            {"source_term_id": "old-a", "target_term_id": "old-b",
             "relation_type": "part_of"}
        )
        assert result["relation_type"] == "part_of"

    def test_relation_type_as_a_term_id_is_remapped(self):
        result = self.remapper.remap_term_relation(
            {"source_term_id": "old-a", "target_term_id": "old-b",
             "relation_type": "old-b"}
        )
        assert result["relation_type"] == "new-b"


class TestQualifiedReferenceStrings:
    """The platform stores cross-namespace refs as '<ns>:<id-or-value>'
    (split on the first colon). A fresh restore that re-mints a namespace
    must rewrite BOTH halves of such a string; an exact-match walk sees a
    string no id map knows and would leave the copy pointing verbatim at the
    original namespace (the R-06 matrix cell's failure shape).
    """

    def setup_method(self):
        self.remapper = IDRemapper(namespace_map={"ns-a": "copy-a", "ns-b": "copy-b"})
        self.remapper.add_document_mapping("ns-b-D000001", "019def00-0000-7000-8000-0000000000aa")
        self.remapper.add_term_mapping("old-term", "new-term")

    def test_qualified_data_ref_rewrites_both_halves(self):
        doc = {"document_id": "X", "data": {"primary": "ns-b:ns-b-D000001"}}
        result = self.remapper.remap_document(doc)
        assert result["data"]["primary"] == "copy-b:019def00-0000-7000-8000-0000000000aa"

    def test_qualified_ref_in_array_rewrites(self):
        doc = {"document_id": "X", "data": {"links": ["ns-b:ns-b-D000001", "unrelated"]}}
        result = self.remapper.remap_document(doc)
        assert result["data"]["links"] == [
            "copy-b:019def00-0000-7000-8000-0000000000aa", "unrelated",
        ]

    def test_qualified_value_form_keeps_its_value_half(self):
        # A fresh restore re-mints ids, not values: an unmapped rest is a
        # portable value and only the namespace half moves.
        doc = {"document_id": "X", "data": {"ref": "ns-a:SPEC-1"}}
        result = self.remapper.remap_document(doc)
        assert result["data"]["ref"] == "copy-a:SPEC-1"

    def test_three_part_term_form_moves_namespace_only(self):
        doc = {"document_id": "X", "data": {"unit": "ns-a:MATRIX_COLOR:RED"}}
        result = self.remapper.remap_document(doc)
        assert result["data"]["unit"] == "copy-a:MATRIX_COLOR:RED"

    def test_namespace_outside_the_archive_passes_through_whole(self):
        # 'wip' is not being re-minted; the string still describes its entity.
        doc = {"document_id": "X", "data": {"unit": "wip:TIME_UNIT:seconds"}}
        result = self.remapper.remap_document(doc)
        assert result["data"]["unit"] == "wip:TIME_UNIT:seconds"

    def test_qualified_lookup_value_follows_the_maps(self):
        doc = {
            "document_id": "X",
            "data": {},
            "references": [{
                "field_path": "primary",
                "lookup_value": "ns-b:ns-b-D000001",
                "resolved": {"document_id": "ns-b-D000001", "namespace": "ns-b"},
            }],
        }
        result = self.remapper.remap_document(doc)
        ref = result["references"][0]
        assert ref["lookup_value"] == "copy-b:019def00-0000-7000-8000-0000000000aa"
        assert ref["resolved"]["document_id"] == "019def00-0000-7000-8000-0000000000aa"
        assert ref["resolved"]["namespace"] == "copy-b"

    def test_bare_lookup_value_behaviour_is_unchanged(self):
        doc = {
            "document_id": "X",
            "data": {},
            "references": [
                {"field_path": "a", "lookup_value": "old-term"},
                {"field_path": "b", "lookup_value": "Some Human Name"},
            ],
        }
        result = self.remapper.remap_document(doc)
        assert result["references"][0]["lookup_value"] == "new-term"
        assert result["references"][1]["lookup_value"] == "Some Human Name"

    def test_empty_namespace_map_leaves_qualified_strings_alone(self):
        remapper = IDRemapper()
        remapper.add_document_mapping("ns-b-D000001", "new-id")
        doc = {"document_id": "X", "data": {"ref": "ns-b:ns-b-D000001"}}
        result = remapper.remap_document(doc)
        assert result["data"]["ref"] == "ns-b:ns-b-D000001"
