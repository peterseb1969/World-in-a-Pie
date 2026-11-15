---
section: new
cases: [1312]
packages: [wip-deploy]
---

### add-app --app-from-registry runs an app on a dev install from its published image

`wip-deploy add-app --app-from-registry`: on a dev install an enabled app needs a source, and this is the verb's own opt-in to run it from its published image — `add-app wip-kb --app-from-registry --tag 20261113d --name tc01`. It takes no name, and is refused on a non-dev target or with `--app-source`, which dev builds from instead. `add-app` now refuses a sourceless app in its own words (`--app-source <path>`, `--app-from-registry`, `remove-app`) instead of repeating install's flags, and refuses `--tag` on an app dev builds locally (`<image>:dev-<install>`), where the tag could never pull. `remove-app` clears the opt-in. A run refused at the render no longer leaves newly generated secrets on disk. *Version: `wip-deploy` 2.1.0 → 2.2.0 would fit this additive flag; left unchanged.*
