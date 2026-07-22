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

    # --- Synonym pairs ---

    def test_all_synonym_pairs(self):
        pairs = self.remapper.all_synonym_pairs()
        assert len(pairs) == 8
        # Check one pair from each type
        assert ("0190a000-0000-7000-0000-000000000001", "0190e000-0000-7000-0000-000000000001", "terminologies") in pairs
        assert ("0190b000-0000-7000-0000-000000000001", "0190e000-0000-7000-0000-000000000011", "terms") in pairs
        assert ("0190c000-0000-7000-0000-000000000001", "0190e000-0000-7000-0000-000000000021", "templates") in pairs
        assert ("FILE-000001", "0190e000-0000-7000-0000-000000000031", "files") in pairs


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
