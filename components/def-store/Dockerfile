# WIP Def-Store Service Dockerfile
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy wip-auth library first (for caching)
COPY libs/wip-auth /app/libs/wip-auth

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir /app/libs/wip-auth && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY src/ /app/src/

# Set Python path
ENV PYTHONPATH=/app/src

# Expose port
EXPOSE 8002

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8002/health || exit 1

# Run the application
CMD ["uvicorn", "def_store.main:app", "--host", "0.0.0.0", "--port", "8002"]
