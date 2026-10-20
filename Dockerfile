FROM python:3.13-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set up isolated virtual environment outside the mounted /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV UV_COMPILE_BYTECODE=1

WORKDIR /app

# Cache dependency layer
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project

# Copy project source
COPY . .
RUN uv sync --frozen

EXPOSE 8000
