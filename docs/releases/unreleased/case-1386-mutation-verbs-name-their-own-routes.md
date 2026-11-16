---
section: fixes
cases: [1386]
packages: [wip-deploy]
---

### A verb editing a dev install names the per-app routes for an app it cannot build

An install whose saved spec enabled an app for `--target dev` with neither a checkout nor the published-image opt-in — a spec written before the render started refusing it, or hand-edited — aborted in `add-app`, `redeploy`, `app-deploy`, `add-module` and the other verbs that apply a saved spec with `install`'s advice: `pass --app-source NAME=<path-to-checkout>`, `--app-from-registry NAME`, "disable the app and re-install", none of which those verbs take. The refusal now comes before the render and names what the running verb does take: `wip-deploy add-app <app> --app-source <path-to-checkout> --name <install>`, `wip-deploy add-app <app> --app-from-registry --name <install>`, or `wip-deploy remove-app <app> --name <install>`. `remove-app <app>` itself still goes through, since it clears the app before the check. `install` and `render` assemble their spec from flags and keep the renderer's wording, which is theirs. Exit code unchanged (2).
