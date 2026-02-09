# Build stage
FROM docker.io/library/node:20-alpine AS builder

WORKDIR /app

# Copy package files
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Build arguments for OIDC configuration (baked into static JS at build time)
# Uses /dex proxy path to avoid CORS issues
ARG VITE_OIDC_AUTHORITY=/dex
ARG VITE_OIDC_CLIENT_ID=wip-console
ARG VITE_OIDC_CLIENT_SECRET=wip-console-secret
ARG VITE_OIDC_PROVIDER_NAME=SSO

# Build arguments for optional module feature flags
ARG VITE_REPORTING_ENABLED=false
ARG VITE_FILES_ENABLED=false
ARG VITE_INGEST_ENABLED=false

# Set environment variables for build
ENV VITE_OIDC_AUTHORITY=$VITE_OIDC_AUTHORITY
ENV VITE_OIDC_CLIENT_ID=$VITE_OIDC_CLIENT_ID
ENV VITE_OIDC_CLIENT_SECRET=$VITE_OIDC_CLIENT_SECRET
ENV VITE_OIDC_PROVIDER_NAME=$VITE_OIDC_PROVIDER_NAME
ENV VITE_REPORTING_ENABLED=$VITE_REPORTING_ENABLED
ENV VITE_FILES_ENABLED=$VITE_FILES_ENABLED
ENV VITE_INGEST_ENABLED=$VITE_INGEST_ENABLED

# Build the application
RUN npm run build

# Production stage
FROM docker.io/library/nginx:alpine

# Copy the built application
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port 80
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
