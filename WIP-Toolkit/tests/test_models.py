"""Tests for Pydantic data models."""

from datetime import UTC, datetime

from wip_toolkit.models import (
    ClosureInfo,
    EntityCounts,
    ExportStats,
    ImportStats,
    Manifest,
    NamespaceConfig,
)


class TestEntityCounts:
    """Test EntityCounts model."""

    def test_total_all_zero(self):
        counts = EntityCounts()
        assert counts.total == 0

    def test_total_single_type(self):
        counts = EntityCounts(terminologies=5)
        assert counts.total == 5

    def test_total_multiple_types(self):
        counts = EntityCounts(
            terminologies=2, terms=10, templates=3, documents=100, files=5,
        )
        assert counts.total == 120

    def test_default_values_are_zero(self):
        counts = EntityCounts()
        assert counts.terminologies == 0
        assert counts.terms == 0
        assert counts.templates == 0
        assert counts.documents == 0
        assert counts.files == 0

    def test_total_is_property_not_stored(self):
        """total is a computed property, not a stored field."""
        counts = EntityCounts(terms=5)
        assert counts.total == 5
        # Modifying a field should update the total
        counts.terms = 10
        assert counts.total == 10


class TestManifest:
    """Test Manifest model."""

    def test_default_format_version(self):
        m = Manifest()
        assert m.format_version == "1.1"

    def test_default_tool_version(self):
        m = Manifest()
        assert m.tool_version == "0.2.3"

    def test_default_namespace_empty(self):
        m = Manifest()
        assert m.namespace == ""

    def test_default_include_inactive_false(self):
        m = Manifest()
        assert m.include_inactive is False

    def test_default_include_files_false(self):
        m = Manifest()
        assert m.include_files is False

    def test_default_include_all_versions_false(self):
        m = Manifest()
        assert m.include_all_versions is False

    def test_default_counts_zero(self):
        m = Manifest()
        assert m.counts.total == 0

    def test_default_closure_empty(self):
        m = Manifest()
        assert m.closure.external_terminologies == []
        assert m.closure.external_templates == []
        assert m.closure.iterations == 0

    def test_exported_at_is_set_automatically(self):
        before = datetime.now(UTC)
        m = Manifest()
        after = datetime.now(UTC)
        assert before <= m.exported_at <= after

    def test_namespace_config_default_none(self):
        m = Manifest()
        assert m.namespace_config is None

    def test_custom_values_preserved(self):
        m = Manifest(
            format_version="2.0",
            source_host="pi-poe-8gb.local",
            namespace="custom-ns",
            include_inactive=True,
            include_files=True,
            include_all_versions=True,
            counts=EntityCounts(terminologies=5, terms=100),
        )
        assert m.format_version == "2.0"
        assert m.source_host == "pi-poe-8gb.local"
        assert m.namespace == "custom-ns"
        assert m.include_inactive is True
        assert m.include_files is True
        assert m.include_all_versions is True
        assert m.counts.terminologies == 5
        assert m.counts.terms == 100

    def test_manifest_with_namespace_config(self):
        ns_config = NamespaceConfig(
            prefix="wip",
            description="Test namespace",
            isolation_mode="strict",
        )
        m = Manifest(namespace="wip", namespace_config=ns_config)
        assert m.namespace_config is not None
        assert m.namespace_config.prefix == "wip"
        assert m.namespace_config.isolation_mode == "strict"

    def test_manifest_with_closure_info(self):
        closure = ClosureInfo(
            external_terminologies=["TERM-EX0190b000-0000-7000-0000-000000000001"],
            external_templates=["TPL-EX0190b000-0000-7000-0000-000000000001", "TPL-EX0190b000-0000-7000-0000-000000000002"],
            iterations=3,
            warnings=["Missing reference"],
        )
        m = Manifest(closure=closure)
        assert len(m.closure.external_terminologies) == 1
        assert len(m.closure.external_templates) == 2
        assert m.closure.iterations == 3
        assert len(m.closure.warnings) == 1


class TestImportStats:
    """Test ImportStats model."""

    def test_mode_required(self):
        stats = ImportStats(mode="restore")
        assert stats.mode == "restore"

    def test_default_namespaces_empty(self):
        stats = ImportStats(mode="fresh")
        assert stats.source_namespace == ""
        assert stats.target_namespace == ""

    def test_default_counts_zero(self):
        stats = ImportStats(mode="restore")
        assert stats.created.total == 0
        assert stats.skipped.total == 0
        assert stats.failed.total == 0

    def test_error_tracking(self):
        stats = ImportStats(mode="fresh")
        stats.errors.append("Failed to create document X")
        stats.errors.append("Failed to create document Y")
        assert len(stats.errors) == 2
        assert "document X" in stats.errors[0]

    def test_warning_tracking(self):
        stats = ImportStats(mode="restore")
        stats.warnings.append("Skipped inactive entity")
        assert len(stats.warnings) == 1

    def test_id_mappings_default_zero(self):
        stats = ImportStats(mode="fresh")
        assert stats.id_mappings == 0

    def test_synonyms_registered_default_zero(self):
        stats = ImportStats(mode="fresh")
        assert stats.synonyms_registered == 0

    def test_duration_default_zero(self):
        stats = ImportStats(mode="restore")
        assert stats.duration_seconds == 0.0

    def test_created_skipped_failed_independent(self):
        stats = ImportStats(mode="fresh")
        stats.created.terminologies = 5
        stats.skipped.terminologies = 2
        stats.failed.terminologies = 1
        assert stats.created.terminologies == 5
        assert stats.skipped.terminologies == 2
        assert stats.failed.terminologies == 1

    def test_errors_and_warnings_are_independent_lists(self):
        """Ensure errors and warnings lists are not shared across instances."""
        stats_a = ImportStats(mode="fresh")
        stats_b = ImportStats(mode="restore")
        stats_a.errors.append("error_a")
        stats_b.warnings.append("warning_b")
        assert len(stats_a.errors) == 1
        assert len(stats_a.warnings) == 0
        assert len(stats_b.errors) == 0
        assert len(stats_b.warnings) == 1


class TestExportStats:
    """Test ExportStats model."""

    def test_required_namespace(self):
        stats = ExportStats(namespace="wip")
        assert stats.namespace == "wip"

    def test_default_counts_zero(self):
        stats = ExportStats(namespace="wip")
        assert stats.counts.total == 0

    def test_default_closure_values(self):
        stats = ExportStats(namespace="wip")
        assert stats.closure_iterations == 0
        assert stats.external_terminologies == 0
        assert stats.external_templates == 0

    def test_default_warnings_empty(self):
        stats = ExportStats(namespace="wip")
        assert stats.warnings == []

    def test_default_duration_zero(self):
        stats = ExportStats(namespace="wip")
        assert stats.duration_seconds == 0.0

    def test_custom_values(self):
        stats = ExportStats(
            namespace="wip",
            counts=EntityCounts(terminologies=3, terms=50),
            closure_iterations=2,
            external_terminologies=1,
            external_templates=0,
            warnings=["missing ref"],
            duration_seconds=1.5,
        )
        assert stats.counts.total == 53
        assert stats.closure_iterations == 2
        assert stats.external_terminologies == 1
        assert stats.duration_seconds == 1.5


class TestNamespaceConfig:
    """Test NamespaceConfig model."""

    def test_prefix_required(self):
        ns = NamespaceConfig(prefix="wip")
        assert ns.prefix == "wip"

    def test_default_description_empty(self):
        ns = NamespaceConfig(prefix="wip")
        assert ns.description == ""

    def test_default_isolation_mode(self):
        ns = NamespaceConfig(prefix="wip")
        assert ns.isolation_mode == "open"

    def test_default_id_config_none(self):
        ns = NamespaceConfig(prefix="wip")
        assert ns.id_config is None

    def test_custom_values(self):
        ns = NamespaceConfig(
            prefix="medical",
            description="Medical data namespace",
            isolation_mode="strict",
            id_config={"format": "UUID7", "prefix": "MED"},
        )
        assert ns.prefix == "medical"
        assert ns.description == "Medical data namespace"
        assert ns.isolation_mode == "strict"
        assert ns.id_config["format"] == "UUID7"


class TestClosureInfo:
    """Test ClosureInfo model."""

    def test_defaults_empty(self):
        c = ClosureInfo()
        assert c.external_terminologies == []
        assert c.external_templates == []
        assert c.iterations == 0
        assert c.warnings == []

    def test_custom_values(self):
        c = ClosureInfo(
            external_terminologies=["0190a000-0000-7000-0000-000000000001", "0190a000-0000-7000-0000-000000000002"],
            external_templates=["0190c000-0000-7000-0000-000000000001"],
            iterations=5,
            warnings=["Missing reference to 0190a000-0000-7000-0000-000000000003"],
        )
        assert len(c.external_terminologies) == 2
        assert len(c.external_templates) == 1
        assert c.iterations == 5
        assert len(c.warnings) == 1

    def test_warnings_are_independent_across_instances(self):
        a = ClosureInfo()
        b = ClosureInfo()
        a.warnings.append("w1")
        assert len(b.warnings) == 0
