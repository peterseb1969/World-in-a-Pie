"""Tests for the WIP Registry Service."""

import bcrypt

# Hash test API keys at bcrypt's minimum cost. At the default cost (12) one
# check takes ~170 ms, and a key's first use checks it against every stored
# hash before it is cached, so tests that mint keys spent most of the suite's
# five minutes there. This runs before conftest imports the app (which hashes
# its configured keys at import). Verification follows the cost stored in each
# hash, so only the tests' own keys get cheap; production hashing is unchanged.
_gensalt = bcrypt.gensalt
bcrypt.gensalt = lambda rounds=4, prefix=b"2b": _gensalt(4, prefix)
