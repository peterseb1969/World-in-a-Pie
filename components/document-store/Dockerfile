# WIP Document Store Service Dockerfile
FROM docker.io/library/python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
# Note: wip-auth is mounted and installed at runtime via docker-compose
COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

# Copy application source
COPY src/ /app/src/

# Set Python path
ENV PYTHONPATH=/app/src

# Expose port
EXPOSE 8004

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8004/health || exit 1

# Run the application
CMD ["uvicorn", "document_store.main:app", "--host", "0.0.0.0", "--port", "8004"]
