"""Tests for the backfill-synonyms command."""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from wip_toolkit.backfill import _fetch_all, _register_batch, backfill_synonyms


class TestRegisterBatch:
    """Tests for _register_batch helper."""

    def test_dry_run_skips_api_call(self):
        client = MagicMock()
        items = [{"target_id": "t1", "synonym_composite_key": {"ns": "wip"}}]
        counts = _register_batch(client, items, batch_size=100, dry_run=True)
        assert counts["total"] == 1
        assert counts["added"] == 0
        client.post.assert_not_called()

    def test_empty_items(self):
        client = MagicMock()
        counts = _register_batch(client, [], batch_size=100)
        assert counts["total"] == 0
        client.post.assert_not_called()

    def test_counts_added_and_existing(self):
        client = MagicMock()
        client.post.return_value = {
            "results": [
                {"status": "added"},
                {"status": "already_exists"},
                {"status": "added"},
            ]
        }
        items = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
        counts = _register_batch(client, items, batch_size=100)
        assert counts["added"] == 2
        assert counts["existing"] == 1
        assert counts["failed"] == 0

    def test_batching(self):
        client = MagicMock()
        client.post.return_value = {"results": [{"status": "added"}]}
        items = [{"id": str(i)} for i in range(5)]
        _register_batch(client, items, batch_size=2)
        # 5 items / batch_size 2 = 3 calls
        assert client.post.call_count == 3


class TestBackfillSynonyms:
    """Tests for the main backfill_synonyms function."""

    def test_backfill_terminologies(self):
        client = MagicMock()
        # fetch_all_paginated returns terminologies
        client.fetch_all_paginated.side_effect = [
            # terminologies
            [{"terminology_id": "t1", "value": "STATUS"}],
            # terms for STATUS
            [{"term_id": "term1", "value": "active"}],
            # templates
            [],
            # documents (if not skipped)
            [],
        ]
        client.post.return_value = {"results": [{"status": "added"}]}

        summary = backfill_synonyms(client, "wip")
        assert summary["terminologies"]["total"] == 1

        # Check the synonym composite key structure
        terminology_call = client.post.call_args_list[0]
        items = terminology_call[1]["json"] if "json" in terminology_call[1] else terminology_call[0][2]
        assert items[0]["synonym_composite_key"] == {
            "ns": "wip", "type": "terminology", "value": "STATUS"
        }

    def test_backfill_terms(self):
        client = MagicMock()
        client.fetch_all_paginated.side_effect = [
            [{"terminology_id": "t1", "value": "STATUS"}],
            [{"term_id": "term1", "value": "active"}],
            [],
            [],
        ]
        client.post.return_value = {"results": [{"status": "added"}]}

        summary = backfill_synonyms(client, "wip")
        assert summary["terms"]["total"] == 1

        # Check the term synonym composite key includes terminology value
        term_call = client.post.call_args_list[1]
        items = term_call[1]["json"] if "json" in term_call[1] else term_call[0][2]
        assert items[0]["synonym_composite_key"] == {
            "ns": "wip", "type": "term", "terminology": "STATUS", "value": "active"
        }

    def test_skip_documents(self):
        client = MagicMock()
        client.fetch_all_paginated.side_effect = [
            [],  # terminologies
            [],  # templates
        ]
        client.post.return_value = {"results": []}

        summary = backfill_synonyms(client, "wip", skip_documents=True)
        assert summary["documents"]["total"] == 0

    def test_dry_run(self):
        client = MagicMock()
        client.fetch_all_paginated.side_effect = [
            [{"terminology_id": "t1", "value": "STATUS"}],
            [],
            [],
            [],
        ]

        summary = backfill_synonyms(client, "wip", dry_run=True)
        # No POST calls should have been made
        client.post.assert_not_called()


