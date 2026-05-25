# AWS hosting (EC2 + ALB + Cognito)

Deploy PloyAgent on a single **EC2** instance with the existing Docker Compose stack. Expose the dashboard over **HTTPS** using an **Application Load Balancer** and **Amazon Cognito** (no Cloudflare). Postgres and workers are not exposed to the internet.

**Region:** Prefer **non-US** (e.g. `eu-west-1`, `eu-central-1`) for Polymarket API egress — see [infra/README.md](../infra/README.md).

---

## Architecture

```text
Browser → https://dashboard.yourdomain.com
       → ALB :443 (ACM cert)
       → authenticate-cognito
       → EC2 :8765 (ploy-web)
       → TimescaleDB (Docker network only)

Slack interactivity (optional) → ALB rule → EC2 :8766 (slack-events)
```

Compose files on the instance:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.aws.yml up -d
```

| File | Role |
|------|------|
| [docker-compose.yml](../docker-compose.yml) | Full stack |
| [docker-compose.prod.yml](../docker-compose.prod.yml) | No public DB port |
| [docker-compose.aws.yml](../docker-compose.aws.yml) | `WEB_HOST=0.0.0.0`, ports 8765/8766 for ALB |

Lock down with **security groups**: only the ALB security group may reach ports **8765** and **8766** on the instance.

---

## Prerequisites

| Item | Notes |
|------|--------|
| **IAM user** (deploy) | EC2, ALB, Cognito, ACM, Route53, SSM — for you, not dashboard login |
| **Cognito user** | Email/password for dashboard — create in User Pool after deploy |
| **Route 53 hosted zone** | e.g. `yourdomain.com` |
| **EC2 key pair** | Optional if using SSM Session Manager only |

---

## Phase 1 — Network and TLS

### 1.1 Security groups

**`ploy-alb-sg`**

| Direction | Port | Source |
|-----------|------|--------|
| Inbound | 443 | `0.0.0.0/0` |
| Outbound | All | `0.0.0.0/0` |

**`ploy-ec2-sg`**

| Direction | Port | Source |
|-----------|------|--------|
| Inbound | 8765 | `ploy-alb-sg` |
| Inbound | 8766 | `ploy-alb-sg` (if using Slack interactivity) |
| Outbound | All | `0.0.0.0/0` |

Do **not** open 5432/5433 to the internet.

### 1.2 ACM certificate

1. ACM → Request certificate → `dashboard.yourdomain.com` (add `*.yourdomain.com` if needed).
2. DNS validation → create CNAME records in Route 53.
3. Wait until status is **Issued**.

---

## Phase 2 — Cognito

### 2.1 User pool

1. Cognito → Create user pool → **Email** sign-in.
2. Password policy / MFA as you prefer.
3. Note **User pool ID** and **Region**.

### 2.2 App client (for ALB)

1. User pool → App integration → Create app client.
2. Name: `ploy-alb-client`.
3. **Don't** generate a client secret (ALB integration uses public client flow).
4. Allowed callback URLs (replace host):

   ```text
   https://dashboard.yourdomain.com/oauth2/idpresponse
   ```

5. Allowed sign-out URLs:

   ```text
   https://dashboard.yourdomain.com
   ```

6. OAuth 2.0 grant types: **Authorization code grant**.
7. OpenID scopes: `openid`, `email`, `profile`.

### 2.3 Dashboard user

User pool → Users → **Create user** (your email). This is the person who can open the dashboard after ALB auth.

---

## Phase 3 — Application Load Balancer

### 3.1 Target groups

**`ploy-web-tg`**

| Setting | Value |
|---------|--------|
| Target type | Instances |
| Protocol | HTTP |
| Port | **8765** |
| Health check path | `/healthz` |
| Success codes | `200` |
| VPC | Same as EC2 |

**`ploy-slack-tg`** (optional)

| Setting | Value |
|---------|--------|
| Port | **8766** |
| Health check | `/healthz` or TCP:8766 |

Register the EC2 instance in both target groups after the instance exists.

### 3.2 HTTPS listener (443)

1. Create ALB (internet-facing, public subnets, `ploy-alb-sg`).
2. Listener **HTTPS:443** → certificate from ACM.
3. **Default action** (authenticate then forward):

   - Action 1: **Authenticate** → Cognito
     - User pool + app client from Phase 2
     - On unauthenticated request: **Authenticate** (redirect to Cognito)
     - Scope: `openid`
   - Action 2: **Forward to** `ploy-web-tg`

4. **Optional rule** (Slack): IF path is `/slack/*` OR host is `slack.yourdomain.com` → forward to `ploy-slack-tg` (may need separate listener rule without Cognito for Slack POSTs — Slack cannot complete OAuth; use a **separate listener rule priority** with **forward only** to 8766 for `POST /slack/interactions`).

   For Slack: create rule **before** Cognito default:

   - Condition: Path `/slack/interactions` (exact or prefix)
   - Action: Forward to `ploy-slack-tg` (no authenticate)

   Set Slack app Interactivity URL: `https://dashboard.yourdomain.com/slack/interactions` (or dedicated host).

---

## Phase 4 — EC2 instance

| Setting | Value |
|---------|--------|
| AMI | Ubuntu 24.04 LTS |
| Type | `t3.large` minimum |
| Subnet | Private preferred (with NAT for egress) or public |
| Security group | `ploy-ec2-sg` |
| IAM role | `AmazonSSMManagedInstanceCore` + SSM read on `/ploy-agent/*` |
| Storage | 80 GiB gp3 |

### 4.1 IAM policy for SSM (instance role)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["ssm:GetParametersByPath", "ssm:GetParameter"],
      "Resource": "arn:aws:ssm:REGION:ACCOUNT_ID:parameter/ploy-agent/*"
    }
  ]
}
```

### 4.2 SSM parameters

Create **SecureString** parameters under `/ploy-agent/prod/` — see [infra/aws/ssm-parameters.example](../infra/aws/ssm-parameters.example).

Example CLI:

```bash
export AWS_REGION=eu-west-1
PW=$(openssl rand -hex 24)
aws ssm put-parameter --name /ploy-agent/prod/POSTGRES_PASSWORD --value "$PW" --type SecureString
aws ssm put-parameter --name /ploy-agent/prod/DATABASE_URL \
  --value "postgresql://postgres:${PW}@timescaledb:5432/ploy_agent" --type SecureString
aws ssm put-parameter --name /ploy-agent/prod/ANTHROPIC_API_KEY --value "sk-..." --type SecureString
aws ssm put-parameter --name /ploy-agent/prod/POLY_GAMMA_TAGS --value "nba" --type SecureString
```

Parameter **names** use the last path segment as the `.env` key (`POSTGRES_PASSWORD`, not full path).

### 4.3 Bootstrap the instance

**Option A — cloud-init** (paste and adjust):

```yaml
#cloud-config
runcmd:
  - curl -fsSL https://raw.githubusercontent.com/uri-source/PloyAgent/main/scripts/aws-bootstrap.sh -o /tmp/aws-bootstrap.sh
  - chmod +x /tmp/aws-bootstrap.sh
  - AWS_REGION=eu-west-1 /tmp/aws-bootstrap.sh
```

**Option B — SSM Session Manager** after launch:

```bash
sudo git clone https://github.com/uri-source/PloyAgent.git /opt/PloyAgent
cd /opt/PloyAgent
sudo chmod +x scripts/*.sh
# If no SSM yet: sudo cp .env.production.example .env && sudo nano .env
sudo AWS_REGION=eu-west-1 ./scripts/aws-ssm-pull-env.sh
sudo ./scripts/aws-deploy.sh
```

Scripts:

| Script | Purpose |
|--------|---------|
| [scripts/aws-bootstrap.sh](../scripts/aws-bootstrap.sh) | Docker + clone + SSM + deploy |
| [scripts/aws-ssm-pull-env.sh](../scripts/aws-ssm-pull-env.sh) | SSM → `.env` |
| [scripts/aws-deploy.sh](../scripts/aws-deploy.sh) | `docker compose` up |
| [scripts/aws-verify.sh](../scripts/aws-verify.sh) | Local health checks |

### 4.4 Post-deploy (on EC2)

```bash
cd /opt/PloyAgent
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.aws.yml"

$COMPOSE exec web ploy-migrate
$COMPOSE run --rm web python scripts/backfill_market_type.py
$COMPOSE run --rm web ploy-sim init-profiles   # optional
```

---

## Phase 5 — DNS and verification

### 5.1 Route 53

Create **A/AAAA alias** record:

| Name | Type | Target |
|------|------|--------|
| `dashboard` | A – Alias | ALB DNS name |

### 5.2 Verification checklist

On the **instance**:

```bash
./scripts/aws-verify.sh
curl -s http://127.0.0.1:8765/healthz
```

In the **browser**:

- [ ] `https://dashboard.yourdomain.com` redirects to Cognito
- [ ] After login, dashboard loads (top picks / simulation tracker)
- [ ] `/analytics` loads (TRabi45 analytics page)

**Ingest:**

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.aws.yml \
  exec timescaledb psql -U postgres -d ploy_agent -c "SELECT MAX(ts) FROM prices"
```

Age should stay under a few minutes.

**Target group:** EC2 registered, health **healthy**.

---

## Phase 6 — Operations

### Update app

```bash
cd /opt/PloyAgent
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.aws.yml up -d --build
```

### Logs

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.aws.yml logs -f ingest reason web
```

### Backups

- EBS snapshot of the instance volume, or
- `pg_dump` from the `timescaledb` container on a schedule.

### Add dashboard users

Cognito User Pool → Users → Create user (invite friends by email).

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| ALB **502** | Target unhealthy — `curl localhost:8765/healthz` on EC2; security group ALB→EC2 |
| Cognito redirect loop | Callback URL must be exactly `https://<host>/oauth2/idpresponse` |
| Stale prices | Ingest logs; Polymarket blocked in region — change region or egress |
| Slack buttons fail | Listener rule for `/slack/interactions` **without** Cognito auth |

---

## Cost (rough)

| Resource | Estimate |
|----------|----------|
| EC2 `t3.large` | ~$60/mo |
| ALB | ~$20/mo + LCU |
| NAT Gateway (if private subnet) | ~$35/mo |

Stop the EC2 instance when not experimenting to save cost.

---

## Alternative: Cloudflare on a VPS

See [cloudflare-private-dashboard.md](cloudflare-private-dashboard.md) if you prefer Tunnel + Access instead of ALB + Cognito.
