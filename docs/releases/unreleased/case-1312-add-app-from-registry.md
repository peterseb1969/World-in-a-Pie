---
section: new
cases: [1312]
packages: [wip-deploy]
---

### add-app --app-from-registry runs an app on a dev install from its published image

`wip-deploy add-app --app-from-registry`: on a dev install an enabled app needs a source, and this is the verb's own opt-in to run it from its published image — `add-app wip-kb --app-from-registry --tag 20261113d --name tc01`. The image is pulled from the install's registry: a dev install made without `--registry` refuses such an app, naming `install … --registry <host> --reconcile`, instead of rendering a bare `wip-kb:<tag>` that pulls nothing published. It takes no name, and is refused on a non-dev target or with `--app-source`, which dev builds from instead; a later `add-app --app-source` replaces the opt-in, and an install naming an app both ways is refused. `add-app` now refuses a sourceless app in its own words (`--app-source <path>`, `--app-from-registry`, and `remove-app` for an app the install already runs) instead of repeating install's flags, and refuses `--tag` on an app dev builds locally (`<image>:dev-<install>`), where the tag could never pull. `remove-app` clears the opt-in. A run refused at the render no longer leaves newly generated secrets on disk.
