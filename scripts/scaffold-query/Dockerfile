# Two-stage build for a wip-deploy-installable query app (CASE-370).
# Stage 1 builds the SPA; stage 2 runs the Express server (via tsx), which in
# production also serves the built SPA from ./dist under APP_BASE_PATH.

FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
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

FROM node:22-slim AS runtime
WORKDIR /app
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
