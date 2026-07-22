"""OBO Graph import detects the term prefix from the DATA, not the filename (CASE-775).

The importer derived its node-URI filter from the graph's filename
(`goslim_generic.owl` → prefix `GOSLIM_GENERIC`), which matches the term
prefix only for a full ontology named after itself. Every subset/slim/
renamed file (whose filename is not its term prefix) filtered out every
real node and imported ZERO terms while returning 200 OK — the
silent-green class.

These are pure parse-layer tests (no container): _detect_prefix and
_parse_obo_graph over a small hand-built graph mirroring the goslim shape
(GO term nodes in a file that would misdetect as GOSLIM). The empty-import
guard lives in import_ontology and is exercised at the service layer.
"""

import pytest

from def_store.services.import_export import ImportExportService


def _graph(node_prefix: str, filename_stem: str, n: int = 3) -> dict:
    """An OBO Graph JSON whose FILENAME stem differs from its node PREFIX —
    the CASE-775 shape (a subset/renamed file)."""
    nodes = [
        {
            "id": f"http://purl.obolibrary.org/obo/{node_prefix}_{i:07d}",
            "type": "CLASS",
            "lbl": f"term {i}",
        }
        for i in range(1, n + 1)
    ]
    edges = [
        {
            "sub": f"http://purl.obolibrary.org/obo/{node_prefix}_{i+1:07d}",
            "obj": f"http://purl.obolibrary.org/obo/{node_prefix}_{i:07d}",
            "pred": "is_a",
        }
        for i in range(1, n)
    ]
    return {
        "graphs": [{
            "id": f"http://purl.obolibrary.org/obo/x/subsets/{filename_stem}.owl",
            "meta": {},
            "nodes": nodes,
            "edges": edges,
        }]
    }


class TestDetectPrefixFromData:
    def test_majority_node_prefix_beats_filename(self):
        # Filename stem GOSLIM_GENERIC, nodes are GO_ — detection must say GO.
        graph = _graph("GO", "goslim_generic")["graphs"][0]
        assert ImportExportService._detect_prefix(graph) == "GO"

    def test_envo_subset_shape(self):
        graph = _graph("ENVO", "envoPolar")["graphs"][0]
        assert ImportExportService._detect_prefix(graph) == "ENVO"

    def test_full_ontology_still_detects_its_own_prefix(self):
        # The case that worked before (filename == prefix) still works.
        graph = _graph("HP", "hp")["graphs"][0]
        assert ImportExportService._detect_prefix(graph) == "HP"

    def test_non_obo_graph_falls_back_to_filename(self):
        graph = {
            "id": "http://example.org/myvocab.json",
            "nodes": [{"id": "http://example.org/thing/A", "type": "CLASS", "lbl": "A"}],
            "edges": [],
        }
        assert ImportExportService._detect_prefix(graph) == "MYVOCAB"


class TestParseHonorsDetectedPrefix:
    def test_subset_file_parses_its_real_nodes(self):
        # The regression: no explicit prefix_filter, subset filename — the
        # GO nodes must survive (pre-fix this returned zero).
        parsed = ImportExportService._parse_obo_graph(_graph("GO", "goslim_generic", n=5))
        assert parsed["prefix"] == "GO"
        assert parsed["stats"]["nodes_parsed"] == 5
        assert parsed["stats"]["edges_parsed"] == 4  # is_a chain of 5 nodes
        assert set(parsed["nodes"].values().__iter__().__next__().keys()) >= {"value", "label"}

    def test_explicit_prefix_filter_still_overrides(self):
        parsed = ImportExportService._parse_obo_graph(
            _graph("GO", "goslim_generic", n=3), prefix_filter="GO"
        )
        assert parsed["prefix"] == "GO"
        assert parsed["stats"]["nodes_parsed"] == 3

    def test_wrong_explicit_prefix_matches_nothing(self):
        # An explicit-but-wrong filter is the operator's choice — parse
        # returns empty; the service-layer guard turns that into a 400.
        parsed = ImportExportService._parse_obo_graph(
            _graph("GO", "goslim_generic", n=3), prefix_filter="NOPE"
        )
        assert parsed["stats"]["nodes_parsed"] == 0


class TestEmptyImportGuard:
    """The service refuses a zero-match import instead of creating an empty
    terminology. Exercised without a container by driving import_ontology
    with a graph whose explicit prefix_filter matches nothing and asserting
    it raises before any terminology write."""

    @pytest.mark.asyncio
    async def test_zero_match_raises_naming_observed_prefixes(self):
        graph = _graph("GO", "goslim_generic", n=4)
        with pytest.raises(ValueError) as exc_info:
            await ImportExportService.import_ontology(
                graph,
                {"namespace": "unit-test-ns", "prefix_filter": "NOPE"},
            )
        msg = str(exc_info.value)
        assert "0 of 4" in msg
        assert "GO" in msg  # observed prefix reported
