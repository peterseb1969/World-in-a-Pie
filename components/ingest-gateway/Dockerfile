# WIP Ingest Gateway Service
FROM docker.io/library/python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

# Copy source code
COPY src/ ./src/

# Set Python path
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Health check
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

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8006/api/ingest-gateway/health || exit 1

EXPOSE 8006

CMD ["uvicorn", "ingest_gateway.main:app", "--host", "0.0.0.0", "--port", "8006"]
