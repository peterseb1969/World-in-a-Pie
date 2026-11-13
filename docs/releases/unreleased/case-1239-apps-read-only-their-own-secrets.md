---
section: action-required
cases: [1239]
---

### An app reads only the secrets it owns

An app's manifest could name any secret through `from_secret`; only `api-key` was refused. An app could therefore read another app's key (`app-<other>-api-key`, admin on that app's namespaces), MongoDB's root password or a component's secret, straight into its container's environment.

- **A deployer-supplied `from_secret` must be the app's own:** its NATS or MongoDB user password, or the master key for a `privileged_key` app. Anything else refuses the manifest, in `validate-manifest` and at every install.
- **An operator-supplied secret** (an optional var with `supplied_by: operator`) may have any name except one another owner uses: the master key, a component's secret, an app or declared key, another app's deployer-supplied secret, or a secret the deployer generates (NATS, MongoDB, Dex, Garage). The install is refused naming the owner. Operator secrets shared between apps, such as `anthropic-api-key`, are unaffected.
- **brmri** now takes `JIRA_TOKEN` (`jira-token`) and `ANTHROPIC_API_KEY` (`anthropic-api-key`) as optional, operator-supplied secrets. Until now the deployer filled them with random strings, which neither service accepts.

**Action:**

- On an install running brmri, put the real values in the install's secret store, as `secrets/jira-token` and `secrets/anthropic-api-key`, and redeploy. Without them brmri starts with both variables unset.
- An app whose manifest names a secret it does not own must name its own secret instead.
