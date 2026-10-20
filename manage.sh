#!/usr/bin/env bash
set -euo pipefail

# Determine project directory
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# Check if Docker compose web container is running
is_docker_running() {
    command -v docker >/dev/null 2>&1 && \
    docker compose ps --status running -q web 2>/dev/null | grep -q .
}

if is_docker_running; then
    DOCKER_FLAGS=""
    if [ -t 0 ] && [ -t 1 ]; then
        DOCKER_FLAGS="-t"
    fi
    exec docker compose exec -i $DOCKER_FLAGS web python manage.py "$@"
elif command -v uv >/dev/null 2>&1; then
    exec uv run python manage.py "$@"
else
    echo "Error: Neither a running Docker web container nor a local 'uv' installation was found." >&2
    echo "To run with Docker: docker compose up -d" >&2
    echo "To run locally: ./run.sh --local" >&2
    exit 1
fi
