# Job image for the Gitea Actions runner on the development Mac (container
# mode; see docs/design/ci-local-runner.md): Debian trixie, Python 3.13 with
# venv, Node 24 and npm (actions/checkout and the tarball check), git, curl.
#
# Node is the scaffold's major (node:24 in scripts/scaffold-query). Debian
# trixie's own nodejs package is Node 20, end of life since 2026-04-30, so
# Node and npm are copied from the official node:24 image of the same
# Debian release (same glibc) instead of installed with apt.
#
# Build on the runner machine; the runner label points at the local tag and
# does not pull it:
#   podman build -t localhost/wip-ci:py3.13-trixie ci/runner-image
#
# Both bases are pinned to their multi-arch indexes: python:3.13-trixie
# (2026-10-24) and node:24-trixie-slim (Node 24.22.2, npm 11.19.1, 2026-11-04).
FROM docker.io/library/node:24-trixie-slim@sha256:54f84b4cf8432b21aaffd5d3ce8d41e12cea0d10d33c09d2f3c9879d49d6b446 AS node

FROM docker.io/library/python:3.13-trixie@sha256:23cf55275b5f3fa89ad0082e6507d3ba74e9a2aeb83e4fe740c24570a1385dd1
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=node /usr/local/include/node /usr/local/include/node
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && ln -s ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack \
 && node --version && npm --version
