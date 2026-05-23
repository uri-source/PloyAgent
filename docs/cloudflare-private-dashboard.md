# Private dashboard for friends (VPS + Cloudflare)

Expose the dashboard at a public HTTPS URL **only for people you allow**, without opening the app or database to the whole internet.

**Security model:** [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/) (outbound from your server) + [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/) (email login). The PloyAgent UI has **no built-in password**.

---

## What you need to sign up for (one-time)

| Service | Required? | What to do |
|---------|-----------|------------|
| **[Cloudflare](https://dash.cloudflare.com/sign-up)** | **Yes** | Free account. Add your domain and point its nameservers to Cloudflare. |
| **[Cloudflare Zero Trust](https://one.dash.cloudflare.com/)** | **Yes** | Open Zero Trust once; pick a team name (free). Used for Access policies. |
| **VPS provider** | **Yes** | e.g. Hetzner, DigitalOcean, Linode, GCP. ~2 vCPU / 4GB RAM. Prefer **non-US** region if Polymarket blocks US egress (see `infra/README.md`). |
| **Domain name** | **Yes** | Can be cheap; must use Cloudflare DNS for tunnel + Access. |

You do **not** need a separate “hosting” product beyond the VPS. Friends only need the URL you send them.

**I cannot create these accounts for you.** Steps that open a browser (`cloudflared tunnel login`, Access policies) must be done on your side.

---

## Architecture

```text
Friend → https://dashboard.yourdomain.com
         → Cloudflare Access (email allowlist)
         → cloudflared tunnel (on VPS)
         → http://127.0.0.1:8765 (ploy-web, not public on firewall)
```

Postgres and other services stay on the Docker network; **no** public port `5433` or `8765` on the VPS firewall.

---

## Part A — Deploy PloyAgent on the VPS

### 1. Server prep (Ubuntu)

```bash
# SSH into the VPS
sudo apt update && sudo apt install -y git curl

# Install Docker: https://docs.docker.com/engine/install/ubuntu/
# Optional firewall — allow SSH only:
sudo ufw allow OpenSSH
sudo ufw enable
```

### 2. Clone and configure

```bash
git clone https://github.com/YOUR_ORG/PloyAgent.git
cd PloyAgent
cp .env.production.example .env
nano .env   # set POSTGRES_PASSWORD, DATABASE_URL (same password), ANTHROPIC_API_KEY, etc.
```

Generate a password:

```bash
openssl rand -hex 24
```

### 3. Start the stack (production compose)

```bash
chmod +x scripts/vps-deploy.sh
./scripts/vps-deploy.sh
```

This runs:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

[`docker-compose.prod.yml`](../docker-compose.prod.yml) binds `web` to `127.0.0.1:8765` and removes public TimescaleDB port mapping.

Verify on the VPS:

```bash
curl -s http://127.0.0.1:8765/healthz
# {"status":"ok"}
```

### 4. Optional: simulation data

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  run --rm web ploy-sim init-profiles
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  run --rm web ploy-sim replay --days 14
```

---

## Part B — Cloudflare Tunnel on the VPS

### 1. Install cloudflared

```bash
# Ubuntu/Debian
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb
sudo dpkg -i /tmp/cloudflared.deb
cloudflared --version
```

### 2. Log in (browser)

```bash
cloudflared tunnel login
```

Authorize the zone that contains your domain. Cert is saved under `~/.cloudflared/`.

### 3. Create tunnel

```bash
cloudflared tunnel create ployagent-dashboard
```

Save the **Tunnel ID** and credentials path (`~/.cloudflared/<UUID>.json`).

### 4. Config file

Copy [`infra/cloudflared/config.example.yml`](../infra/cloudflared/config.example.yml) to `~/.cloudflared/config.yml` and edit hostnames.

### 5. DNS route

```bash
cloudflared tunnel route dns ployagent-dashboard dashboard.yourdomain.com
```

### 6. Run tunnel (test)

```bash
cloudflared tunnel run ployagent-dashboard
```

From your laptop:

```bash
curl -sS https://dashboard.yourdomain.com/healthz
```

### 7. Run tunnel as a service (survives reboot)

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

---

## Part C — Friends-only access (Cloudflare Access)

1. Go to [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → **Access** → **Applications**.
2. **Add application** → **Self-hosted**.
3. **Application domain:** `dashboard.yourdomain.com` (must match tunnel hostname).
4. **Add a policy** → **Include** → **Emails** → add your email and each friend’s email.
5. Save.

**Share with friends:**

```text
https://dashboard.yourdomain.com
```

They log in with an **allowed email** (one-time code or Google, depending on your Access settings).

Optional: enable **MFA** for your own email in the same policy.

---

## Part D — Slack buttons (optional)

If you use Approve/Reject in Slack, add a second ingress host in `config.yml`:

```yaml
  - hostname: slack.yourdomain.com
    service: http://127.0.0.1:8766
```

Set Slack **Interactivity Request URL** to:

```text
https://slack.yourdomain.com/slack/interactions
```

[`docker-compose.prod.yml`](../docker-compose.prod.yml) already binds `8766` to localhost only.

---

## Mac-only setup (local tunnel)

If the agent runs on your Mac instead of a VPS, use the same Tunnel + Access steps but point `service: http://127.0.0.1:8765` at your Mac and keep `WEB_HOST=127.0.0.1` in `.env`. Run `cloudflared` on the Mac.

---

## Troubleshooting

| Symptom | What to check |
|--------|----------------|
| `502` / bad gateway | On VPS: `curl http://127.0.0.1:8765/healthz`; `docker compose ... ps` |
| Tunnel won’t start | `config.yml` tunnel ID + `credentials-file` path |
| No Access login | Access application domain must match URL hostname |
| Friend can’t log in | Their exact email must be in the Access policy |
| Empty dashboard | `ploy-ingest`, `ploy-enrich`, `ploy-reason` running; check logs |
| DB connection errors | `POSTGRES_PASSWORD` in `.env` matches `DATABASE_URL` |

---

## Checklist before sharing the link

- [ ] Cloudflare account + domain on Cloudflare DNS
- [ ] Zero Trust Access app + friend emails added
- [ ] VPS firewall: **no** public 8765 / 5433
- [ ] `POSTGRES_PASSWORD` changed from default
- [ ] `curl http://127.0.0.1:8765/healthz` on VPS
- [ ] `https://dashboard.yourdomain.com` works after Access login in incognito

---

## What stays private

- `.env`, API keys, SSH keys
- Direct database access
- Anyone not on the Access email list
