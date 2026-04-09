#!/usr/bin/env bash
# Build WIP release images and optionally push to a container registry.
#
# Builds self-contained images with wip-auth (and wip-toolkit for
# document-store) baked in. No volume mounts needed at runtime.
#
# Usage:
#   scripts/build-release.sh                                    # Build all, local tags
#   scripts/build-release.sh --registry gitea.local:3000/peter --tag 1.0.0
#   scripts/build-release.sh --registry gitea.local:3000/peter --tag 1.0.0 --push --insecure
#   scripts/build-release.sh --service document-store           # Build one service
#   scripts/build-release.sh --generate-compose                 # Also emit docker-compose.production.yml
#
# Image naming:
#   With --registry: <registry>/<service>:<tag>    (e.g. gitea.local:3000/peter/registry:1.0.0)
#   Without:         wip/<service>:<tag>           (local only)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Defaults
REGISTRY=""
TAG="latest"
PUSH=false
INSECURE=false
ONLY_SERVICE=""
GENERATE_COMPOSE=false
BUILDER="${BUILDER:-podman}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $1"; }

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Build WIP release images with libraries baked in.

Options:
  --registry REG       Image registry prefix (e.g. gitea.local:3000/peter)
  --tag TAG            Image tag (default: latest)
  --push               Push images after building
  --insecure           Use --tls-verify=false for push (needed for HTTP registries)
  --service NAME       Build only one service
  --generate-compose   Generate docker-compose.production.yml after building
  --builder CMD        Container build tool: podman or docker (default: podman)
  -h, --help           Show this help

Services: registry, def-store, template-store, document-store,
          reporting-sync, ingest-gateway, mcp-server, console

Examples:
  # Build all and push to Gitea
  $(basename "$0") --registry gitea.local:3000/peter --tag 1.0.0 --push --insecure

  # Build one service locally
  $(basename "$0") --service document-store

  # Build all + generate production compose
  $(basename "$0") --registry gitea.local:3000/peter --tag 1.0.0 --push --insecure --generate-compose
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --registry)          REGISTRY="$2"; shift 2 ;;
        --tag)               TAG="$2"; shift 2 ;;
        --push)              PUSH=true; shift ;;
        --insecure)          INSECURE=true; shift ;;
        --service)           ONLY_SERVICE="$2"; shift 2 ;;
        --generate-compose)  GENERATE_COMPOSE=true; shift ;;
        --builder)           BUILDER="$2"; shift 2 ;;
        -h|--help)           usage ;;
        *)                   log_error "Unknown option: $1"; usage ;;
    esac
done

# ── Helpers ──────────────────────────────────────────────────────

image_name() {
    local svc="$1"
    if [[ -n "$REGISTRY" ]]; then
        echo "${REGISTRY}/${svc}:${TAG}"
    else
        echo "wip/${svc}:${TAG}"
    fi
}

push_image() {
    local img="$1"
    if $PUSH; then
        log_info "  Pushing ${img}"
        if $INSECURE; then
            $BUILDER push --tls-verify=false "$img"
        else
            $BUILDER push "$img"
        fi
    fi
}

BUILT_IMAGES=()
FAILED=()

# ── Services requiring wip-auth ─────────────────────────────────
# document-store also needs wip-toolkit (backup engine imports it)
AUTH_SERVICES=(registry def-store template-store document-store reporting-sync)
TOOLKIT_SERVICES=(document-store)
PLAIN_SERVICES=(ingest-gateway mcp-server)

build_python_with_libs() {
    local svc="$1"
    local svc_dir="${PROJECT_ROOT}/components/${svc}"
    local img
    img="$(image_name "$svc")"

    log_step "Building ${img} (with baked libs)"

    local tmpdir
    tmpdir="$(mktemp -d)"
    # Clean up temp dir on function exit
    trap "rm -rf '$tmpdir'" RETURN

    # Copy service files into temp build context
    cp "${svc_dir}/Dockerfile" "$tmpdir/"
    cp "${svc_dir}/requirements-docker.txt" "$tmpdir/"
    [[ -d "${svc_dir}/src" ]] && cp -r "${svc_dir}/src" "$tmpdir/src"
    [[ -d "${svc_dir}/config" ]] && cp -r "${svc_dir}/config" "$tmpdir/config"

    # Copy wip-auth library
    cp -r "${PROJECT_ROOT}/libs/wip-auth" "$tmpdir/wip-auth"

    # Determine if this service also needs wip-toolkit
    local needs_toolkit=false
    for ts in "${TOOLKIT_SERVICES[@]}"; do
        if [[ "$svc" == "$ts" ]]; then
            needs_toolkit=true
            break
        fi
    done

    if $needs_toolkit; then
        cp -r "${PROJECT_ROOT}/WIP-Toolkit" "$tmpdir/wip-toolkit"
    fi

    # Patch Dockerfile: insert lib installs after pip install of requirements.
    # Uses a temp file approach for macOS/BSD sed compatibility.
    local dockerfile="$tmpdir/Dockerfile"
    local patched="$tmpdir/Dockerfile.patched"
    awk -v needs_toolkit="$needs_toolkit" '
    /RUN pip install --no-cache-dir -r requirements-docker.txt/ {
        print
        print ""
        print "# Install wip-auth library (baked for release)"
        print "COPY wip-auth /tmp/wip-auth"
        print "RUN pip install --no-cache-dir /tmp/wip-auth && rm -rf /tmp/wip-auth"
        if (needs_toolkit == "true") {
            print ""
            print "# Install wip-toolkit (baked for release)"
            print "COPY wip-toolkit /tmp/wip-toolkit"
            print "RUN pip install --no-cache-dir /tmp/wip-toolkit && rm -rf /tmp/wip-toolkit"
        }
        next
    }
    { print }
    ' "$dockerfile" > "$patched"
    mv "$patched" "$dockerfile"

    if $BUILDER build -t "$img" "$tmpdir"; then
        BUILT_IMAGES+=("$img")
        push_image "$img"
        log_info "  ${svc}: OK"
    else
        log_error "  ${svc}: BUILD FAILED"
        FAILED+=("$svc")
    fi
    echo ""
}

build_python_plain() {
    local svc="$1"
    local svc_dir="${PROJECT_ROOT}/components/${svc}"
    local img
    img="$(image_name "$svc")"

    log_step "Building ${img}"

    if $BUILDER build -t "$img" "$svc_dir"; then
        BUILT_IMAGES+=("$img")
        push_image "$img"
        log_info "  ${svc}: OK"
    else
        log_error "  ${svc}: BUILD FAILED"
        FAILED+=("$svc")
    fi
    echo ""
}

build_console() {
    local img
    img="$(image_name "console")"

    log_step "Building ${img}"

    if $BUILDER build \
        --target production \
        --build-arg VITE_OIDC_AUTHORITY=/dex \
        --build-arg VITE_OIDC_CLIENT_ID=wip-console \
        --build-arg VITE_OIDC_CLIENT_SECRET=wip-console-secret \
        --build-arg VITE_OIDC_PROVIDER_NAME=Dex \
        --build-arg VITE_REPORTING_ENABLED=true \
        --build-arg VITE_FILES_ENABLED=true \
        --build-arg VITE_INGEST_ENABLED=true \
        -t "$img" \
        "${PROJECT_ROOT}/ui/wip-console"; then
        BUILT_IMAGES+=("$img")
        push_image "$img"
        log_info "  console: OK"
    else
        log_error "  console: BUILD FAILED"
        FAILED+=("console")
    fi
    echo ""
}

# ── Production compose generation ────────────────────────────────

generate_production_compose() {
    if [[ -z "$REGISTRY" ]]; then
        log_error "--generate-compose requires --registry"
        return 1
    fi

    local out="${PROJECT_ROOT}/docker-compose.production.yml"
    log_step "Generating ${out}"

    cat > "$out" <<YAML
# WIP Production Compose — generated by build-release.sh
# Registry: ${REGISTRY}  Tag: ${TAG}
#
# Usage:
#   docker compose -f docker-compose.production.yml pull
#   docker compose -f docker-compose.production.yml up -d
#
# Prerequisites:
#   - Copy .env.example to .env and edit
#   - Ensure the container registry is accessible

services:

  # ── Infrastructure ──────────────────────────────────────────

  mongodb:
    image: docker.io/library/mongo:7
    container_name: wip-mongodb
    ports:
      - "27017:27017"
    volumes:
      - wip-mongo-data:/data/db
    networks:
      - wip-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "mongosh", "--quiet", "--eval", "db.runCommand('ping').ok"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s

  postgres:
    image: docker.io/library/postgres:16
    container_name: wip-postgres
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: \${WIP_POSTGRES_USER:-wip}
      POSTGRES_PASSWORD: \${WIP_POSTGRES_PASSWORD:-wip}
      POSTGRES_DB: \${WIP_POSTGRES_DB:-wip_reporting}
    volumes:
      - wip-postgres-data:/var/lib/postgresql/data
    networks:
      - wip-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U \${WIP_POSTGRES_USER:-wip}"]
      interval: 30s
      timeout: 10s
      retries: 3

  nats:
    image: docker.io/library/nats:2
    container_name: wip-nats
    command: ["-js", "-m", "8222"]
    ports:
      - "4222:4222"
      - "8222:8222"
    volumes:
      - wip-nats-data:/data
    networks:
      - wip-network
    restart: unless-stopped

  minio:
    image: docker.io/minio/minio:latest
    container_name: wip-minio
    command: server /data --console-address ":9001"
    ports:
      - "9000:9000"
      - "9001:9001"
    environment:
      MINIO_ROOT_USER: \${WIP_FILE_STORAGE_ACCESS_KEY:-wip-minio-root}
      MINIO_ROOT_PASSWORD: \${WIP_FILE_STORAGE_SECRET_KEY:-wip-minio-password}
    volumes:
      - wip-minio-data:/data
    networks:
      - wip-network
    restart: unless-stopped

  dex:
    image: docker.io/dexidp/dex:v2.38.0
    container_name: wip-dex
    command: ["dex", "serve", "/etc/dex/config.yaml"]
    volumes:
      - ./config/dex/config.yaml:/etc/dex/config.yaml:ro
    networks:
      - wip-network
    restart: unless-stopped

  caddy:
    image: docker.io/library/caddy:2
    container_name: wip-caddy
    ports:
      - "8443:8443"
    volumes:
      - ./config/caddy/Caddyfile:/etc/caddy/Caddyfile:ro
      - wip-caddy-data:/data
    networks:
      - wip-network
    restart: unless-stopped

  # ── WIP Services ────────────────────────────────────────────

  registry:
    image: ${REGISTRY}/registry:${TAG}
    container_name: wip-registry
    ports:
      - "8001:8001"
    environment:
      PYTHONPATH: /app/src
      MONGO_URI: \${WIP_MONGO_URI:-mongodb://wip-mongodb:27017/}
      DATABASE_NAME: wip_registry
      MASTER_API_KEY: \${API_KEY}
      AUTH_ENABLED: "true"
      CORS_ORIGINS: \${WIP_CORS_ORIGINS:-}
    command: ["uvicorn", "registry.main:app", "--host", "0.0.0.0", "--port", "8001"]
    networks:
      - wip-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8001/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    depends_on:
      mongodb:
        condition: service_healthy

  def-store:
    image: ${REGISTRY}/def-store:${TAG}
    container_name: wip-def-store
    ports:
      - "8002:8002"
    environment:
      PYTHONPATH: /app/src
      MONGO_URI: \${WIP_MONGO_URI:-mongodb://wip-mongodb:27017/}
      DATABASE_NAME: wip_def_store
      REGISTRY_URL: http://wip-registry:8001
      REGISTRY_API_KEY: \${API_KEY}
      NATS_URL: nats://wip-nats:4222
      CORS_ORIGINS: \${WIP_CORS_ORIGINS:-}
    command: ["uvicorn", "def_store.main:app", "--host", "0.0.0.0", "--port", "8002"]
    networks:
      - wip-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8002/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    depends_on:
      mongodb:
        condition: service_healthy
      registry:
        condition: service_healthy

  template-store:
    image: ${REGISTRY}/template-store:${TAG}
    container_name: wip-template-store
    ports:
      - "8003:8003"
    environment:
      PYTHONPATH: /app/src
      MONGO_URI: \${WIP_MONGO_URI:-mongodb://wip-mongodb:27017/}
      DATABASE_NAME: wip_template_store
      REGISTRY_URL: http://wip-registry:8001
      REGISTRY_API_KEY: \${API_KEY}
      DEF_STORE_URL: http://wip-def-store:8002
      DEF_STORE_API_KEY: \${API_KEY}
      NATS_URL: nats://wip-nats:4222
      CORS_ORIGINS: \${WIP_CORS_ORIGINS:-}
    command: ["uvicorn", "template_store.main:app", "--host", "0.0.0.0", "--port", "8003"]
    networks:
      - wip-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8003/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    depends_on:
      mongodb:
        condition: service_healthy
      registry:
        condition: service_healthy

  document-store:
    image: ${REGISTRY}/document-store:${TAG}
    container_name: wip-document-store
    ports:
      - "8004:8004"
    environment:
      PYTHONPATH: /app/src
      MONGO_URI: \${WIP_MONGO_URI:-mongodb://wip-mongodb:27017/}
      DATABASE_NAME: wip_document_store
      REGISTRY_URL: http://wip-registry:8001
      REGISTRY_API_KEY: \${API_KEY}
      TEMPLATE_STORE_URL: http://wip-template-store:8003
      TEMPLATE_STORE_API_KEY: \${API_KEY}
      DEF_STORE_URL: http://wip-def-store:8002
      DEF_STORE_API_KEY: \${API_KEY}
      NATS_URL: nats://wip-nats:4222
      WIP_FILE_STORAGE_ENABLED: "true"
      WIP_FILE_STORAGE_ENDPOINT: http://wip-minio:9000
      WIP_FILE_STORAGE_ACCESS_KEY: \${WIP_FILE_STORAGE_ACCESS_KEY:-wip-minio-root}
      WIP_FILE_STORAGE_SECRET_KEY: \${WIP_FILE_STORAGE_SECRET_KEY:-wip-minio-password}
      WIP_FILE_STORAGE_BUCKET: wip-attachments
      CORS_ORIGINS: \${WIP_CORS_ORIGINS:-}
    command: ["uvicorn", "document_store.main:app", "--host", "0.0.0.0", "--port", "8004"]
    networks:
      - wip-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8004/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    depends_on:
      mongodb:
        condition: service_healthy
      registry:
        condition: service_healthy

  reporting-sync:
    image: ${REGISTRY}/reporting-sync:${TAG}
    container_name: wip-reporting-sync
    ports:
      - "8005:8005"
    environment:
      PYTHONPATH: /app/src
      MONGO_URI: \${WIP_MONGO_URI:-mongodb://wip-mongodb:27017/}
      POSTGRES_URI: postgresql://\${WIP_POSTGRES_USER:-wip}:\${WIP_POSTGRES_PASSWORD:-wip}@wip-postgres:5432/\${WIP_POSTGRES_DB:-wip_reporting}
      TEMPLATE_STORE_URL: http://wip-template-store:8003
      TEMPLATE_STORE_API_KEY: \${API_KEY}
      NATS_URL: nats://wip-nats:4222
      CORS_ORIGINS: \${WIP_CORS_ORIGINS:-}
    command: ["uvicorn", "reporting_sync.main:app", "--host", "0.0.0.0", "--port", "8005"]
    networks:
      - wip-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8005/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    depends_on:
      postgres:
        condition: service_healthy

  ingest-gateway:
    image: ${REGISTRY}/ingest-gateway:${TAG}
    container_name: wip-ingest-gateway
    ports:
      - "8006:8006"
    environment:
      PYTHONPATH: /app/src
      NATS_URL: nats://wip-nats:4222
      DOCUMENT_STORE_URL: http://wip-document-store:8004
      DOCUMENT_STORE_API_KEY: \${API_KEY}
      CORS_ORIGINS: \${WIP_CORS_ORIGINS:-}
    command: ["uvicorn", "ingest_gateway.main:app", "--host", "0.0.0.0", "--port", "8006"]
    networks:
      - wip-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8006/api/ingest-gateway/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s

  console:
    image: ${REGISTRY}/console:${TAG}
    container_name: wip-console
    networks:
      - wip-network
    restart: unless-stopped

volumes:
  wip-mongo-data:
  wip-postgres-data:
  wip-nats-data:
  wip-minio-data:
  wip-caddy-data:

networks:
  wip-network:
    name: wip-network
YAML

    # Generate .env.example
    local env_out="${PROJECT_ROOT}/.env.production.example"
    cat > "$env_out" <<'ENVFILE'
# WIP Production Environment
# Copy to .env and edit before running docker compose up

# Master API key — change this!
API_KEY=change-me-to-a-secure-random-string

# MongoDB
WIP_MONGO_URI=mongodb://wip-mongodb:27017/

# PostgreSQL (reporting)
WIP_POSTGRES_USER=wip
WIP_POSTGRES_PASSWORD=change-me-postgres-password
WIP_POSTGRES_DB=wip_reporting

# MinIO (file storage)
WIP_FILE_STORAGE_ACCESS_KEY=wip-minio-root
WIP_FILE_STORAGE_SECRET_KEY=change-me-minio-password

# CORS — set to your hostname
WIP_CORS_ORIGINS=https://localhost:8443
ENVFILE

    log_info "Generated: ${out}"
    log_info "Generated: ${env_out}"
}

# ── Main ─────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}WIP Release Image Builder${NC}"
echo "Builder:  ${BUILDER}"
echo "Registry: ${REGISTRY:-<local>}"
echo "Tag:      ${TAG}"
echo "Push:     ${PUSH}"
echo "Insecure: ${INSECURE}"
echo ""

START_TIME=$(date +%s)

if [[ -n "$ONLY_SERVICE" ]]; then
    case "$ONLY_SERVICE" in
        console)
            build_console ;;
        ingest-gateway|mcp-server)
            build_python_plain "$ONLY_SERVICE" ;;
        registry|def-store|template-store|document-store|reporting-sync)
            build_python_with_libs "$ONLY_SERVICE" ;;
        *)
            log_error "Unknown service: $ONLY_SERVICE"
            exit 1 ;;
    esac
else
    for svc in "${AUTH_SERVICES[@]}"; do
        build_python_with_libs "$svc"
    done
    for svc in "${PLAIN_SERVICES[@]}"; do
        build_python_plain "$svc"
    done
    build_console
fi

if $GENERATE_COMPOSE; then
    generate_production_compose
fi

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo ""
echo "=========================================="
if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo -e "  ${RED}Build completed with errors${NC} (${DURATION}s)"
    echo ""
    echo "  Failed: ${FAILED[*]}"
else
    echo -e "  ${GREEN}All images built successfully${NC} (${DURATION}s)"
fi
echo ""
echo "  Built ${#BUILT_IMAGES[@]} images:"
for img in "${BUILT_IMAGES[@]}"; do
    echo "    ${img}"
done
if ! $PUSH; then
    echo ""
    echo "  Run with --push to push images to the registry."
fi
echo "=========================================="
