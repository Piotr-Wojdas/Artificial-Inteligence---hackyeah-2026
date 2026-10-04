FROM python:3.12-slim

# Install system dependencies (libgomp for LightGBM, curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install Python dependencies first for caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application source code
COPY solary ./solary
COPY web ./web

# Cache directory for downloads and geocoding
RUN mkdir -p /app/data
ENV SOLARY_DATA=/app/data

# Environment configuration
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["sh", "-c", "python -m uvicorn solary.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
