---
section: fixes
cases: [1243]
---

### Deployer: secrets stay out of k8s ConfigMaps, removed components leave no config behind, and silent spec acceptances refuse

- **k8s secrets.** Dex's config (password hashes, connector client secrets) and Garage's (rpc secret, admin token) are rendered as k8s Secrets, not ConfigMaps. The next pruning apply removes the old ConfigMaps.
- **Leftover config on compose and dev.** A component switched off no longer leaves its config file in the install directory, secrets included.
- **Bounded healthchecks.** A `command` healthcheck runs under `timeout`, as HTTP probes already bound themselves.
- **Validation refuses what it used to accept silently:**
  - a required env reference to a service the install does not run;
  - an unknown `from_spec` path (`network.internal_base_url` is removed);
  - a registry with a space inside;
  - `activation` on a core or optional component;
  - two manifests with one name;
  - a `build_context` other than `.`.

  Each of these was previously accepted silently, or failed later as a traceback.
