"""Tests for the referential integrity closure algorithm."""

from unittest.mock import MagicMock, patch

from wip_toolkit.export.closure import (
    MAX_CLOSURE_ITERATIONS,
    _check_document_references,
    _scan_template_references,
    compute_closure,
)


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


# ===========================================================================
# compute_closure
# ===========================================================================
CLOSURE_MODULE = "wip_toolkit.export.closure"


class TestComputeClosureNoExternalRefs:
    """Closure completes immediately when all references are internal."""

    @patch(f"{CLOSURE_MODULE}.EntityCollector")
    def test_no_external_refs_zero_iterations(self, MockCollector):
        mock_collector = MagicMock()
        MockCollector.return_value = mock_collector

        client = MagicMock()
        terminologies = [
            {"terminology_id": "TERM-001", "value": "COUNTRY"},
        ]
        terms = [{"term_id": "T-001"}]
        templates = [
            {
                "template_id": "TPL-001",
                "extends": None,
                "fields": [
                    {"name": "country", "type": "term", "terminology_ref": "TERM-001"},
                ],
            },
        ]
        documents = []

        extra_terms, extra_items, extra_tpls, warnings = compute_closure(
            client, "wip", terminologies, terms, templates, documents,
        )

        assert extra_terms == []
        assert extra_items == []
        assert extra_tpls == []
        assert warnings == []
        # Collector should NOT have been asked to fetch anything
        mock_collector.fetch_terminology_by_id.assert_not_called()
        mock_collector.fetch_template_versions_by_id.assert_not_called()


class TestComputeClosureExternalTerminology:
    """Closure fetches external terminologies and their terms."""

    @patch(f"{CLOSURE_MODULE}.EntityCollector")
    def test_one_external_terminology_fetched(self, MockCollector):
        mock_collector = MagicMock()
        MockCollector.return_value = mock_collector

        ext_terminology = {"terminology_id": "TERM-EXT", "value": "EXT_LIST"}
        ext_terms = [{"term_id": "T-EXT-1"}, {"term_id": "T-EXT-2"}]
        mock_collector.fetch_terminology_by_id.return_value = ext_terminology
        mock_collector.fetch_terms.return_value = ext_terms

        client = MagicMock()
        terminologies = [{"terminology_id": "TERM-001"}]
        templates = [
            {
                "template_id": "TPL-001",
                "extends": None,
                "fields": [
                    {"name": "ext", "type": "term", "terminology_ref": "TERM-EXT"},
                ],
            },
        ]

        extra_terms_list, extra_items, extra_tpls, warnings = compute_closure(
            client, "wip", terminologies, [], templates, [],
        )

        assert len(extra_terms_list) == 1
        assert extra_terms_list[0]["terminology_id"] == "TERM-EXT"
        assert extra_terms_list[0]["_source"] == "closure"
        assert len(extra_items) == 2
        assert warnings == []

    @patch(f"{CLOSURE_MODULE}.EntityCollector")
    def test_missing_external_terminology_warns(self, MockCollector):
        mock_collector = MagicMock()
        MockCollector.return_value = mock_collector
        mock_collector.fetch_terminology_by_id.return_value = None

        client = MagicMock()
        templates = [
            {
                "template_id": "TPL-001",
                "extends": None,
                "fields": [
                    {"name": "ext", "type": "term", "terminology_ref": "TERM-MISSING"},
                ],
            },
        ]

        _, _, _, warnings = compute_closure(
            client, "wip", [], [], templates, [],
        )

        assert any("TERM-MISSING" in w for w in warnings)


class TestComputeClosureExternalTemplate:
    """Closure fetches external templates (all versions)."""

    @patch(f"{CLOSURE_MODULE}.EntityCollector")
    def test_one_external_template_fetched(self, MockCollector):
        mock_collector = MagicMock()
        MockCollector.return_value = mock_collector

        ext_tpl_v1 = {"template_id": "TPL-EXT", "version": 1, "extends": None, "fields": []}
        ext_tpl_v2 = {"template_id": "TPL-EXT", "version": 2, "extends": None, "fields": []}
        mock_collector.fetch_template_versions_by_id.return_value = [ext_tpl_v1, ext_tpl_v2]

        client = MagicMock()
        templates = [
            {
                "template_id": "TPL-001",
                "extends": "TPL-EXT",
                "fields": [],
            },
        ]

        _, _, extra_tpls, warnings = compute_closure(
            client, "wip", [], [], templates, [],
        )

        assert len(extra_tpls) == 2
        assert all(t["_source"] == "closure" for t in extra_tpls)
        assert warnings == []

    @patch(f"{CLOSURE_MODULE}.EntityCollector")
    def test_missing_external_template_warns(self, MockCollector):
        mock_collector = MagicMock()
        MockCollector.return_value = mock_collector
        mock_collector.fetch_template_versions_by_id.return_value = []

        client = MagicMock()
        templates = [
            {
                "template_id": "TPL-001",
                "extends": "TPL-GHOST",
                "fields": [],
            },
        ]

        _, _, _, warnings = compute_closure(
            client, "wip", [], [], templates, [],
        )

        assert any("TPL-GHOST" in w for w in warnings)


class TestComputeClosureMultiIteration:
    """Closure iterates when a fetched template introduces new refs."""

    @patch(f"{CLOSURE_MODULE}.EntityCollector")
    def test_two_iteration_closure(self, MockCollector):
        """Fetched template introduces a new external terminology."""
        mock_collector = MagicMock()
        MockCollector.return_value = mock_collector

        # First iteration: TPL-001 references TPL-EXT which is external
        # Second iteration: TPL-EXT references TERM-EXT which is external
        ext_tpl = {
            "template_id": "TPL-EXT", "version": 1, "extends": None,
            "fields": [
                {"name": "f1", "type": "term", "terminology_ref": "TERM-EXT"},
            ],
        }
        ext_terminology = {"terminology_id": "TERM-EXT", "value": "EXT"}
        ext_terms = [{"term_id": "T-EXT-1"}]

        mock_collector.fetch_template_versions_by_id.return_value = [ext_tpl]
        mock_collector.fetch_terminology_by_id.return_value = ext_terminology
        mock_collector.fetch_terms.return_value = ext_terms

        client = MagicMock()
        templates = [
            {
                "template_id": "TPL-001",
                "extends": "TPL-EXT",
                "fields": [],
            },
        ]

        extra_terms_list, extra_items, extra_tpls, warnings = compute_closure(
            client, "wip", [], [], templates, [],
        )

        assert len(extra_tpls) == 1
        assert extra_tpls[0]["template_id"] == "TPL-EXT"
        assert len(extra_terms_list) == 1
        assert extra_terms_list[0]["terminology_id"] == "TERM-EXT"
        assert len(extra_items) == 1
        assert warnings == []


class TestComputeClosureMaxIterations:
    """Closure warns when max iterations exceeded."""

    @patch(f"{CLOSURE_MODULE}.EntityCollector")
    def test_max_iterations_exceeded_warns(self, MockCollector):
        mock_collector = MagicMock()
        MockCollector.return_value = mock_collector

        # Return a template that always references a NEW external template,
        # creating an infinite chain.
        call_count = 0

        def make_ext_template(tpl_id):
            nonlocal call_count
            call_count += 1
            next_id = f"TPL-CHAIN-{call_count + 1}"
            return [{
                "template_id": tpl_id, "version": 1, "extends": next_id,
                "fields": [],
            }]

        mock_collector.fetch_template_versions_by_id.side_effect = (
            lambda tpl_id: make_ext_template(tpl_id)
        )

        client = MagicMock()
        templates = [
            {
                "template_id": "TPL-001",
                "extends": "TPL-CHAIN-1",
                "fields": [],
            },
        ]

        _, _, _, warnings = compute_closure(
            client, "wip", [], [], templates, [],
        )

        assert any("did not converge" in w for w in warnings)
        assert mock_collector.fetch_template_versions_by_id.call_count == MAX_CLOSURE_ITERATIONS


# ===========================================================================
# _check_document_references
# ===========================================================================
class TestCheckDocumentReferences:
    """Test document external reference checking (warnings only)."""

    def test_no_external_refs_no_warnings(self):
        known_template_ids = {"TPL-001", "TPL-002"}
        documents = [
            {"document_id": "DOC-1", "template_id": "TPL-001", "references": []},
            {"document_id": "DOC-2", "template_id": "TPL-002", "references": []},
        ]
        warnings: list[str] = []

        _check_document_references(documents, known_template_ids, warnings)

        assert warnings == []

    def test_external_template_id_warns(self):
        known_template_ids = {"TPL-001"}
        documents = [
            {"document_id": "DOC-1", "template_id": "TPL-EXTERNAL", "references": []},
        ]
        warnings: list[str] = []

        _check_document_references(documents, known_template_ids, warnings)

        assert len(warnings) == 1
        assert "external template" in warnings[0]
        assert "TPL-EXTERNAL" in warnings[0]

    def test_external_document_reference_warns(self):
        known_template_ids = {"TPL-001"}
        documents = [
            {
                "document_id": "DOC-1",
                "template_id": "TPL-001",
                "references": [
                    {
                        "field_path": "manager",
                        "resolved": {"document_id": "DOC-EXTERNAL", "template_id": "TPL-001"},
                    },
                ],
            },
        ]
        warnings: list[str] = []

        _check_document_references(documents, known_template_ids, warnings)

        assert len(warnings) == 1
        assert "external document" in warnings[0]

    def test_empty_documents_no_warnings(self):
        warnings: list[str] = []

        _check_document_references([], {"TPL-001"}, warnings)

        assert warnings == []

    def test_document_with_none_references(self):
        """Documents with None references field do not crash."""
        known_template_ids = {"TPL-001"}
        documents = [
            {"document_id": "DOC-1", "template_id": "TPL-001", "references": None},
        ]
        warnings: list[str] = []

        _check_document_references(documents, known_template_ids, warnings)

        assert warnings == []
