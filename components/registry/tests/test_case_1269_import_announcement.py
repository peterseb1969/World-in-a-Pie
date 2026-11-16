"""A key-file import announces a key change only when a key changed.

Provenance: CASE-1269 (0ef2bf47 R7). Any takeover used to announce
``keys: true``, so taking over only a grant or a reservation made every
service re-sync its keys, and a reservation-only takeover announced at all,
though the services cache no reservations.
"""

from registry.services.key_import import ImportReport, _access_announcement


def test_a_key_change_announces_keys():
    assert _access_announcement(ImportReport(keys_updated=["k"], taken_over=["api_key:k"])) == (
        True, True,
    )


def test_a_grant_taken_over_announces_access_but_no_key_change():
    assert _access_announcement(ImportReport(taken_over=["grant:ns:k"])) == (True, False)


def test_a_reservation_taken_over_announces_nothing():
    assert _access_announcement(ImportReport(taken_over=["reservation:ns"])) == (False, False)


def test_a_grant_change_announces_access_only():
    assert _access_announcement(ImportReport(grants_changed=["ns:k"])) == (True, False)


def test_a_pass_that_changed_nothing_announces_nothing():
    assert _access_announcement(ImportReport()) == (False, False)
