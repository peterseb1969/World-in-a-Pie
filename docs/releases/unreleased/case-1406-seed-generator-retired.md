---
section: operational
cases: [1406]
---

### The seed data generator is retired

`scripts/seed_comprehensive.py`, its `components/seed_data/` package and the
older Registry-only `components/registry/scripts/seed_data.py` are removed.
The generator no longer worked on a wip-deploy v2 install. It did not start
from the repo venv, and the platform refused most of its templates, yet it
printed them as created and exited 0. It was removed rather than repaired.
The release checklist no longer lists it, and `docs/testing/deployment-tests.md`,
which described a deployment test script removed with the v1 installers, is
deleted too.

reporting-sync's `test_pipeline.py` built its fixtures from the generator and
is removed with it. It needed the services on their old direct ports, so it
had skipped on every v2 install. `test_e2e.py` still covers events through
NATS into PostgreSQL.

WIP-Toolkit's `seed`, which bootstraps a namespace's terminologies and
templates from `/export-model` files, is unaffected.
