FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY src/ /app/src/

# Build provenance (CASE-526) — stamped at build time via --build-arg; defaults
# to 'dev' for unstamped/local builds. Surfaced on the service root endpoint's
# `build` block (where wip-auth is available) + as OCI image labels for
# `skopeo`/`podman inspect`.
ARG WIP_BUILD_SHA=dev
ARG WIP_BUILD_STAMP=dev
ARG WIP_IMAGE_TAG=dev
ENV WIP_BUILD_SHA=${WIP_BUILD_SHA} \
    WIP_BUILD_STAMP=${WIP_BUILD_STAMP} \
    WIP_IMAGE_TAG=${WIP_IMAGE_TAG}
LABEL org.opencontainers.image.revision=${WIP_BUILD_SHA} \
      org.opencontainers.image.created=${WIP_BUILD_STAMP} \
      org.opencontainers.image.version=${WIP_IMAGE_TAG}

EXPOSE 4180

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
    CMD curl -f http://localhost:4180/health || exit 1

CMD ["uvicorn", "auth_gateway.main:app", "--host", "0.0.0.0", "--port", "4180"]
