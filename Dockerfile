FROM python:3.13-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY pyproject.toml README.md ./
COPY config/ config/
COPY src/ src/
RUN pip install --no-cache-dir -e .

# Create artifacts dir for logs
RUN mkdir -p artifacts

# Default: run nothing (override with command in compose)
CMD ["python", "-c", "print('Use docker compose to start services')"]
