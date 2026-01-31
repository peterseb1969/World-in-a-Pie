# Build stage
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Build arguments for OIDC configuration (baked into static JS at build time)
ARG VITE_OIDC_AUTHORITY=http://localhost:5556/dex
ARG VITE_OIDC_CLIENT_ID=wip-console
ARG VITE_OIDC_CLIENT_SECRET=wip-console-secret

# Set environment variables for build
ENV VITE_OIDC_AUTHORITY=$VITE_OIDC_AUTHORITY
ENV VITE_OIDC_CLIENT_ID=$VITE_OIDC_CLIENT_ID
ENV VITE_OIDC_CLIENT_SECRET=$VITE_OIDC_CLIENT_SECRET

# Build the application
RUN npm run build

# Production stage
FROM nginx:alpine

# Copy the built application
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port 80
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
