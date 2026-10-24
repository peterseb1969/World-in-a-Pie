# Job image for the Gitea Actions runner on the development Mac (container
# mode; see docs/design/ci-local-runner.md). It matches the tools the jobs
# used on the Pi host: Debian trixie, Python 3.13 with venv, Node 20 and npm
# (actions/checkout and the tarball check), git, curl.
#
# Build on the runner machine; the runner label points at the local tag and
# does not pull it:
#   podman build -t localhost/wip-ci:py3.13-trixie ci/runner-image
#
# The base is pinned to the python:3.13-trixie multi-arch index (2026-10-24).
FROM docker.io/library/python:3.13-trixie@sha256:23cf55275b5f3fa89ad0082e6507d3ba74e9a2aeb83e4fe740c24570a1385dd1
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm \
 && rm -rf /var/lib/apt/lists/*
