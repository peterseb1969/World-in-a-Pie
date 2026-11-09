"""A field the services retired is no difference between two definitions.

def-store removed `extensible`. An archive written before still carries it
on its terminologies, and a target row stored before may too; the merge's
comparison ignores it on both sides, so an otherwise identical terminology
is unchanged, not a substantive difference.
"""

from document_store.services.merge_definitions import compare_definitions


def test_extensible_on_one_side_only_is_no_difference():
    archive = {"value": "COLOR", "label": "Color", "extensible": False, "mutable": False}
    target = {"value": "COLOR", "label": "Color", "mutable": False}

    assert compare_definitions(archive, target) == ([], [])


def test_a_real_difference_is_still_found():
    archive = {"value": "COLOR", "label": "Color", "extensible": True, "mutable": True}
    target = {"value": "COLOR", "label": "Color", "mutable": False}

    assert compare_definitions(archive, target) == ([], ["mutable"])
