# Two-stage build for a wip-deploy-installable query app (CASE-370).
# Stage 1 builds the SPA; stage 2 runs the Express server (via tsx), which in
# production also serves the built SPA from ./dist under APP_BASE_PATH.

FROM node:24-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
# Vite reads VITE_BASE_PATH at build time for the SPA's asset base. A
# --build-arg is only visible to RUN when the Dockerfile declares it: without
# this ARG the CI's --build-arg VITE_BASE_PATH=/apps/<slug>/ was dropped and
# the bundle built with base '/', so a deployed app's assets 404. Default '/'
# keeps a plain local `docker build` serving at the root.
ARG VITE_BASE_PATH=/
ENV VITE_BASE_PATH=${VITE_BASE_PATH}
# CASE-472: bake a build-stamp into the SPA so a running app shows which build
# it is. The image builder (CI / wip-deploy) supplies these via --build-arg,
# e.g. `--build-arg VITE_BUILD_STAMP=$(date -u +%FT%TZ) --build-arg
# VITE_BUILD_SHA=$(git rev-parse --short HEAD)`. Default 'dev' means an
# unstamped local build → the UI hides the stamp. Vite inlines VITE_* env
# present at `vite build` time, so these must be ENV (not just ARG) here.
ARG VITE_BUILD_STAMP=dev
ARG VITE_BUILD_SHA=dev
ENV VITE_BUILD_STAMP=${VITE_BUILD_STAMP}
ENV VITE_BUILD_SHA=${VITE_BUILD_SHA}
RUN npm run build          # vite build → /app/dist

FROM node:24-slim AS runtime
WORKDIR /app
# The deployer's HTTP healthcheck probe is a shell `curl || wget` chain, and
# node:*-slim ships neither: without one every probe exits 127 and the app
# reports permanently unhealthy while it serves fine. The runtime stays on the
# build stage's slim (glibc) base because node_modules, including tsx's
# platform-specific esbuild binary, is copied from it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget \
    && rm -rf /var/lib/apt/lists/*
ENV NODE_ENV=production
# Reuse the resolved deps from the build stage (includes tsx, used at runtime).
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY package.json tsconfig.json ./
COPY server ./server

EXPOSE 3001
# Health endpoint is mounted under APP_BASE_PATH (default '' → '/api/health').
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://localhost:'+(process.env.PORT||3001)+(process.env.APP_BASE_PATH||'')+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["npx", "tsx", "server/index.ts"]
