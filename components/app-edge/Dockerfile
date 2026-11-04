FROM docker.io/library/caddy:2

# The sync loop is stdlib-only Python: no pip, no requirements file.
RUN apk add --no-cache python3

WORKDIR /app
COPY src/ /app/src/

# The generator's configs must be ones this Caddy accepts: run `caddy
# validate` over configs exercising every option, or fail the build.
RUN python3 /app/src/edge_sync.py --self-test

ARG WIP_BUILD_SHA=dev
ARG WIP_BUILD_STAMP=dev
ARG WIP_IMAGE_TAG=dev
ENV WIP_BUILD_SHA=${WIP_BUILD_SHA} \
    WIP_BUILD_STAMP=${WIP_BUILD_STAMP} \
    WIP_IMAGE_TAG=${WIP_IMAGE_TAG} \
    PYTHONUNBUFFERED=1
LABEL org.opencontainers.image.revision=${WIP_BUILD_SHA} \
      org.opencontainers.image.created=${WIP_BUILD_STAMP} \
      org.opencontainers.image.version=${WIP_IMAGE_TAG}

EXPOSE 8080

# The loop runs Caddy as its child and exits when Caddy does.
CMD ["python3", "/app/src/edge_sync.py"]
