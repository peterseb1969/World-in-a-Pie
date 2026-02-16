"""Tests for the referential integrity closure algorithm."""

from wip_toolkit.export.closure import _scan_template_references


class TestScanTemplateReferences:
    """Test template reference scanning."""

    def test_no_external_refs(self, sample_terminologies, sample_templates):
        """When all refs are in known sets, nothing is returned."""
        known_terms = {t["terminology_id"] for t in sample_terminologies}
        known_tpls = {t["template_id"] for t in sample_templates}

        ext_terms, ext_tpls = _scan_template_references(
            sample_templates, known_terms, known_tpls,
        )

        # TPL-000003 references TERM-000003, TERM-000004 which are NOT in known set
        assert "TERM-000003" in ext_terms
        assert "TERM-000004" in ext_terms
        # All template refs should be in known set
        assert len(ext_tpls) == 0

    def test_external_extends(self):
        """Detects external template referenced via extends."""
        templates = [
            {
                "template_id": "TPL-A",
                "extends": "TPL-EXTERNAL",
                "fields": [],
            }
        ]
        known_terms: set[str] = set()
        known_tpls = {"TPL-A"}

        ext_terms, ext_tpls = _scan_template_references(
            templates, known_terms, known_tpls,
        )

        assert "TPL-EXTERNAL" in ext_tpls

    def test_external_terminology_ref(self):
        """Detects external terminology referenced in field."""
        templates = [
            {
                "template_id": "TPL-A",
                "extends": None,
                "fields": [
                    {"name": "f1", "type": "term", "terminology_ref": "TERM-EXT"},
                ],
            }
        ]

        ext_terms, ext_tpls = _scan_template_references(
            templates, set(), {"TPL-A"},
        )

        assert "TERM-EXT" in ext_terms

    def test_external_template_ref(self):
        """Detects external template referenced in field."""
        templates = [
            {
                "template_id": "TPL-A",
                "extends": None,
                "fields": [
                    {"name": "f1", "type": "object", "template_ref": "TPL-EXT"},
                ],
            }
        ]

        ext_terms, ext_tpls = _scan_template_references(
            templates, set(), {"TPL-A"},
        )

        assert "TPL-EXT" in ext_tpls

    def test_external_array_refs(self):
        """Detects external references in array fields."""
        templates = [
            {
                "template_id": "TPL-A",
                "extends": None,
                "fields": [
                    {"name": "f1", "type": "array", "array_terminology_ref": "TERM-ARR"},
                    {"name": "f2", "type": "array", "array_template_ref": "TPL-ARR"},
                ],
            }
        ]

        ext_terms, ext_tpls = _scan_template_references(
            templates, set(), {"TPL-A"},
        )

        assert "TERM-ARR" in ext_terms
        assert "TPL-ARR" in ext_tpls

    def test_external_target_lists(self):
        """Detects external references in target_templates and target_terminologies."""
        templates = [
            {
                "template_id": "TPL-A",
                "extends": None,
                "fields": [
                    {
                        "name": "f1",
                        "type": "reference",
                        "target_templates": ["TPL-A", "TPL-T1", "TPL-T2"],
                        "target_terminologies": ["TERM-TT1"],
                    },
                ],
            }
        ]

        ext_terms, ext_tpls = _scan_template_references(
            templates, set(), {"TPL-A"},
        )

        assert ext_tpls == {"TPL-T1", "TPL-T2"}
        assert ext_terms == {"TERM-TT1"}

    def test_known_refs_not_returned(self):
        """Known IDs are not returned as external."""
        templates = [
            {
                "template_id": "TPL-A",
                "extends": "TPL-B",
                "fields": [
                    {"name": "f1", "type": "term", "terminology_ref": "TERM-X"},
                    {"name": "f2", "type": "object", "template_ref": "TPL-C"},
                ],
            }
        ]

        known_terms = {"TERM-X"}
        known_tpls = {"TPL-A", "TPL-B", "TPL-C"}

        ext_terms, ext_tpls = _scan_template_references(
            templates, known_terms, known_tpls,
        )

        assert len(ext_terms) == 0
        assert len(ext_tpls) == 0

    def test_empty_templates(self):
        """Empty template list returns no externals."""
        ext_terms, ext_tpls = _scan_template_references([], set(), set())
        assert len(ext_terms) == 0
        assert len(ext_tpls) == 0

    def test_none_values_handled(self):
        """None values in fields don't cause errors."""
        templates = [
            {
                "template_id": "TPL-A",
                "extends": None,
                "fields": [
                    {
                        "name": "f1",
                        "terminology_ref": None,
                        "template_ref": None,
                        "array_terminology_ref": None,
                        "array_template_ref": None,
                        "target_templates": None,
                        "target_terminologies": None,
                    },
                ],
            }
        ]

        ext_terms, ext_tpls = _scan_template_references(
            templates, set(), {"TPL-A"},
        )

        assert len(ext_terms) == 0
        assert len(ext_tpls) == 0
