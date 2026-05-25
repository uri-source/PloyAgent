# Infrastructure notes

## Local development

Use [docker-compose.yml](docker-compose.yml) for TimescaleDB. Point `DATABASE_URL` at `postgresql://postgres:postgres@localhost:5432/ploy_agent`.

## Geo-restrictions (Polymarket)

Polymarket APIs may block or behave differently by region. If ingestion fails from your network, try:

- A non-US egress VPN or cloud VM in an allowed region
- Document the working region for your team

## AWS (recommended for this repo)

Single **EC2** + Docker Compose + **ALB** + **Cognito** for a private HTTPS dashboard.

See [docs/aws-hosting.md](../docs/aws-hosting.md) and [docker-compose.aws.yml](../docker-compose.aws.yml).

## GCP (alternative)

- **Cloud SQL for PostgreSQL** with the TimescaleDB extension enabled (per Timescale Cloud SQL docs) or **Timescale Cloud** linked to your stack
- **Cloud Run**: one service per worker (`ploy-ingest`, `ploy-enrich`, `ploy-reason`, `ploy-notify`, `ploy-web`) with the same container image and different `command` / args
- **Secret Manager** for `DATABASE_URL`, `ANTHROPIC_API_KEY`, and other secrets
- Expose **`ploy-web`** behind HTTPS if accessed outside your VPC

## Web UI

Run `ploy-web` (see root README). Configure `WEB_HOST` / `WEB_PORT` via environment variables.
