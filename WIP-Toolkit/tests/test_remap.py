"""Tests for the ID remapping engine."""

from wip_toolkit.import_.remap import IDRemapper


class TestIDRemapper:
    """Test ID remapping for templates and documents."""

    def setup_method(self):
        self.remapper = IDRemapper()
        self.remapper.add_terminology_mapping("TERM-000001", "TERM-NEW-001")
        self.remapper.add_terminology_mapping("TERM-000002", "TERM-NEW-002")
        self.remapper.add_term_mapping("T-000001", "T-NEW-001")
        self.remapper.add_term_mapping("T-000003", "T-NEW-003")
        self.remapper.add_template_mapping("TPL-000001", "TPL-NEW-001")
        self.remapper.add_template_mapping("TPL-000002", "TPL-NEW-002")
        self.remapper.add_document_mapping(
            "019abc00-0000-7000-8000-000000000001",
            "019def00-0000-7000-8000-000000000001",
        )
        self.remapper.add_file_mapping("FILE-000001", "FILE-NEW-001")

    def test_total_mappings(self):
        assert self.remapper.total_mappings == 8

    # --- Template remapping ---

    def test_remap_template_extends(self):
        tpl = {"template_id": "TPL-X", "extends": "TPL-000001", "fields": []}
        result = self.remapper.remap_template(tpl)
        assert result["extends"] == "TPL-NEW-001"

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
                {"name": "f1", "type": "term", "terminology_ref": "TERM-000001"},
            ],
        }
        result = self.remapper.remap_template(tpl)
        assert result["fields"][0]["terminology_ref"] == "TERM-NEW-001"

    def test_remap_template_array_terminology_ref(self):
        tpl = {
            "template_id": "TPL-X",
            "fields": [
                {"name": "f1", "type": "array", "array_terminology_ref": "TERM-000002"},
            ],
        }
        result = self.remapper.remap_template(tpl)
        assert result["fields"][0]["array_terminology_ref"] == "TERM-NEW-002"

    def test_remap_template_template_ref(self):
        tpl = {
            "template_id": "TPL-X",
            "fields": [
                {"name": "f1", "type": "object", "template_ref": "TPL-000002"},
            ],
        }
        result = self.remapper.remap_template(tpl)
        assert result["fields"][0]["template_ref"] == "TPL-NEW-002"

    def test_remap_template_array_template_ref(self):
        tpl = {
            "template_id": "TPL-X",
            "fields": [
                {"name": "f1", "type": "array", "array_template_ref": "TPL-000001"},
            ],
        }
        result = self.remapper.remap_template(tpl)
        assert result["fields"][0]["array_template_ref"] == "TPL-NEW-001"

    def test_remap_template_target_templates(self):
        tpl = {
            "template_id": "TPL-X",
            "fields": [
                {
                    "name": "f1",
                    "type": "reference",
                    "target_templates": ["TPL-000001", "TPL-000002", "TPL-UNKNOWN"],
                },
            ],
        }
        result = self.remapper.remap_template(tpl)
        assert result["fields"][0]["target_templates"] == [
            "TPL-NEW-001", "TPL-NEW-002", "TPL-UNKNOWN",
        ]

    def test_remap_template_target_terminologies(self):
        tpl = {
            "template_id": "TPL-X",
            "fields": [
                {
                    "name": "f1",
                    "type": "reference",
                    "target_terminologies": ["TERM-000001", "TERM-UNKNOWN"],
                },
            ],
        }
        result = self.remapper.remap_template(tpl)
        assert result["fields"][0]["target_terminologies"] == [
            "TERM-NEW-001", "TERM-UNKNOWN",
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
            "template_id": "TPL-000001",
            "data": {},
        }
        result = self.remapper.remap_document(doc)
        assert result["template_id"] == "TPL-NEW-001"

    def test_remap_document_term_references(self):
        doc = {
            "document_id": "DOC-X",
            "template_id": "TPL-000001",
            "data": {},
            "term_references": [
                {"field_path": "country", "term_id": "T-000001", "terminology_ref": "TERM-000001"},
            ],
        }
        result = self.remapper.remap_document(doc)
        assert result["term_references"][0]["term_id"] == "T-NEW-001"
        assert result["term_references"][0]["terminology_ref"] == "TERM-NEW-001"

    def test_remap_document_references(self):
        doc = {
            "document_id": "DOC-X",
            "template_id": "TPL-000001",
            "data": {},
            "references": [
                {
                    "field_path": "manager",
                    "resolved": {
                        "document_id": "019abc00-0000-7000-8000-000000000001",
                        "template_id": "TPL-000001",
                        "identity_hash": "hash1",
                    },
                },
            ],
        }
        result = self.remapper.remap_document(doc)
        resolved = result["references"][0]["resolved"]
        assert resolved["document_id"] == "019def00-0000-7000-8000-000000000001"
        assert resolved["template_id"] == "TPL-NEW-001"
        assert resolved["identity_hash"] == "hash1"  # Pass through

    def test_remap_document_file_references(self):
        doc = {
            "document_id": "DOC-X",
            "template_id": "TPL-000001",
            "data": {},
            "file_references": [
                {"field_path": "avatar", "file_id": "FILE-000001"},
            ],
        }
        result = self.remapper.remap_document(doc)
        assert result["file_references"][0]["file_id"] == "FILE-NEW-001"

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
            "template_id": "TPL-000001",
            "data": {"name": "John", "email": "john@example.com"},
            "version": 2,
            "identity_hash": "abc",
        }
        result = self.remapper.remap_document(doc)
        assert result["data"] == {"name": "John", "email": "john@example.com"}
        assert result["version"] == 2
        assert result["identity_hash"] == "abc"

    def test_remap_document_empty_refs(self):
        doc = {
            "document_id": "DOC-X",
            "template_id": "TPL-000001",
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
        assert ("TERM-000001", "TERM-NEW-001", "terminologies") in pairs
        assert ("T-000001", "T-NEW-001", "terms") in pairs
        assert ("TPL-000001", "TPL-NEW-001", "templates") in pairs
        assert ("FILE-000001", "FILE-NEW-001", "files") in pairs
