---
section: deployment
cases: [1402]
packages: [wip-deploy]
---

### An app keeps its manifest pin when `--tag` is set

`--tag` sets the tag of the platform's own images. An app whose manifest pins a tag now runs at that pin on every target, even when `--tag` is set. Apps are built and tagged in their own repos, so the platform tag rarely names an image of them. Before this change, `install --tag` sent every app without an `--image-tag` to the platform tag, where its image usually did not exist, and the app waited out the health check in `ImagePullBackOff`. The order is now:

1. `--image-tag NAME=TAG` (also set by `add-app --tag` and `app-deploy --tag`);
2. an app's manifest pin;
3. `--tag`;
4. a component's manifest pin;
5. `latest`.

An app that pins no tag still follows `--tag`, and `redeploy --tag` still keeps such an app on the image it runs. `wip-deploy images` shows the manifest pin as `manifest` for such an app.

**Action:** none for an install that names every app with `--image-tag`; this includes every install the deploy guides describe. If an install ran a pinned app at the platform tag without an `--image-tag`, its next `redeploy`, `add-app` or `app-deploy` moves that app to its manifest pin. Preview the move with `wip-deploy images --name <install>`. To keep the app where it is, pass `--image-tag <app>=<tag>`.
