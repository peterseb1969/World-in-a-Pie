---
section: fixes
cases: [1229]
packages: [wip-deploy]
---

### A compose apply that leaves a service without a container keeps the old spec when nothing moved

podman-compose can exit 0 while podman refused to create a container, and wip-deploy then stops with "compose up left these services without a container". It used to treat that as a failure after the change was in place: it saved the new spec and said "the change is applied and saved, but a later step failed". The check now counts as part of the up. When this install's containers stand exactly as they did before the apply, the old spec is kept as the last one that worked. When anything did move, the new spec is saved, as before.
