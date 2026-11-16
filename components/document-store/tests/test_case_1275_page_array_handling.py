"""One `array_handling` label for a page of documents.

Provenance: CASE-1275 (6756d932 R2). The response carries a single label for the
whole page and it has to hold for every row on it: a page whose first document
overflowed max_cross_product and whose last was expanded used to report
'flattened', a label under which no array cell is JSON, while its own rows held
JSON cells.

"""

from document_store.api.table_view import _page_array_handling


def test_a_page_mixing_json_cells_and_expanded_rows_says_json():
    """'json' outranks: one JSON cell makes the page not-flattened, however
    many of its documents were expanded."""
    assert _page_array_handling({"json", "flattened"}) == "json"
    assert _page_array_handling({"flattened", "json"}) == "json"


def test_a_page_of_one_handling_says_that_handling():
    assert _page_array_handling({"flattened"}) == "flattened"
    assert _page_array_handling({"json"}) == "json"
    assert _page_array_handling({"none"}) == "none"


def test_an_empty_page_says_none():
    assert _page_array_handling(set()) == "none"
