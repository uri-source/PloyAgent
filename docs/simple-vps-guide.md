# Simple VPS guide (no AWS)

Run PloyAgent 24/7 on a small Linux server (~$6–12/month). Your laptop can sleep; tracking is at **Paper trading** (`/paper`) on the dashboard.

**Time:** ~30–45 minutes the first time.

---

## What you are building

```text
Your Mac (browser) ──SSH tunnel optional──► VPS
                                              ├── Docker: ingest, enrich, reason, notify
                                              ├── Docker: sim-forward (paper trades)
                                              ├── Docker: TimescaleDB
                                              └── Docker: web (127.0.0.1:8765 only)
```

No Slack required. No AWS ALB/Cognito.

---

## Step 1 — Pick a VPS

| Provider | Suggestion |
|----------|------------|
| [Hetzner](https://www.hetzner.com/cloud) | CX22 or CPX21, **EU** (e.g. Helsinki / Falkenstein) |
| [DigitalOcean](https://www.digitalocean.com) | Basic Droplet 2 vCPU / 4 GB, **Amsterdam** or **Frankfurt** |

**Why EU:** Polymarket often blocks US egress. If prices stay stale, try another region or see `infra/README.md`.

1. Create account → **Create server**
2. Image: **Ubuntu 24.04**
3. Add your **SSH key** (or use password once, then switch to keys)
4. Note the **public IP** (e.g. `95.217.x.x`)

---

## Step 2 — SSH in from your Mac

```bash
ssh root@YOUR_VPS_IP
# or: ssh ubuntu@YOUR_VPS_IP  (some providers use ubuntu)
```

---

## Step 3 — Install Docker on the VPS

```bash
sudo apt update && sudo apt install -y git curl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# Log out and back in so docker works without sudo:
exit
```

SSH in again, then:

```bash
docker --version
docker compose version
```

---

## Step 4 — Clone the repo and configure `.env`

```bash
git clone https://github.com/uri-source/PloyAgent.git
cd PloyAgent
cp .env.production.example .env
```

Generate a database password:

```bash
openssl rand -hex 24
```

Edit `.env`:

```bash
nano .env
```

**Minimum changes:**

| Variable | Set to |
|----------|--------|
| `POSTGRES_PASSWORD` | your generated password |
| `DATABASE_URL` | `postgresql://postgres:SAME_PASSWORD@timescaledb:5432/ploy_agent` |
| `ANTHROPIC_API_KEY` | your key (or leave empty for statistical confidence) |
| `POLY_GAMMA_TAGS` / `POLY_GAMMA_EVENT_SLUGS` | markets you want (copy from local `.env` if unsure) |
| `AGENT_STRATEGIES` | same as local |
| `SIM_FORWARD_RUN_HOURS` | `0` for unlimited paper run, or `336` for 14 days |

Save (`Ctrl+O`, Enter, `Ctrl+X`).

---

## Step 5 — Start the stack

```bash
chmod +x scripts/vps-deploy.sh
./scripts/vps-deploy.sh
```

Wait until you see `OK: http://127.0.0.1:8765/healthz`.

**One-time:** seed simulation profiles:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  run --rm web ploy-sim init-profiles
```

Check everything is up:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
```

You should see `ingest`, `enrich`, `reason`, `notify`, `sim-forward`, `web`, `timescaledb` **running**.

---

## Step 6 — View the dashboard from your Mac (SSH tunnel)

On your **laptop** (new terminal, keep VPS running):

```bash
ssh -N -L 18875:127.0.0.1:8765 root@YOUR_VPS_IP
```

Use local port **18875** (not 8765) so you do not hit a **local** Docker stack if you also run PloyAgent on your Mac. Leave the tunnel open. In the browser:

- Dashboard: http://127.0.0.1:18875/
- **Paper trading:** http://127.0.0.1:18875/paper

Confirm you are on the VPS: **Paper trading** should show the same run id as on the server (`curl` below). If you see an old run with thousands of trades, you are on local Docker — stop it (`docker compose down`) or keep using port 18875 for the tunnel only.

**On the VPS** (sanity check):

```bash
curl -s http://127.0.0.1:8765/api/sim/tracker | python3 -m json.tool | head -20
```

Compare `current_run.id` with what the browser shows.

---

## Step 7 — Verify paper trading is working

On the **VPS**:

```bash
# Health
curl -s http://127.0.0.1:8765/healthz

# Tracker
curl -s http://127.0.0.1:8765/api/sim/tracker | python3 -m json.tool

# Recent prices (should be fresh within ~1–2 min if ingest works)
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail=30 ingest
```

On **Paper trading** page you want:

- Run status **LIVE**
- `trades` count increasing over time
- Daily table filling in as positions close

---

## Day-2 operations

| Task | Command (on VPS, in `PloyAgent/`) |
|------|-----------------------------------|
| View logs | `docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f sim-forward ingest` |
| Restart stack | `docker compose -f docker-compose.yml -f docker-compose.prod.yml restart` |
| Stop stack | `docker compose -f docker-compose.yml -f docker-compose.prod.yml down` |
| Update code | `git pull && docker compose -f docker-compose.yml -f docker-compose.prod.yml build && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` |
| Paper performance API | `curl -s http://127.0.0.1:8765/api/sim/performance \| python3 -m json.tool` |

**Firewall (recommended):** only SSH public; dashboard stays on localhost + tunnel.

```bash
sudo ufw allow OpenSSH
sudo ufw enable
```

---

## Optional — HTTPS for friends (later)

If you want a real URL without opening port 8765 to the world, add **Cloudflare Tunnel + Access** (not required for solo use):

[docs/cloudflare-private-dashboard.md](cloudflare-private-dashboard.md)

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `healthz` fails | `docker compose ... ps` — wait for `migrate` to finish; check `docker compose ... logs migrate` |
| No prices / stale ingest | EU VPS; check `ingest` logs for blocked network; see `docs/local-monitoring.md` |
| `sim-forward` exits | `docker compose ... logs sim-forward`; ensure `init-profiles` ran |
| DB auth errors on sim | Pull latest repo (prod compose must pass `POSTGRES_PASSWORD` to `sim-forward`) |
| Tunnel drops | Re-run SSH `-L 18875:127.0.0.1:8765` |
| Browser shows wrong run / old Kelly Oubre rows | Local Docker on `:8765`; use tunnel port **18875** or `docker compose down` locally |

---

## Cost

- VPS: ~€4–12 / month  
- Domain + Cloudflare: optional (~$10/year)  
- Anthropic API: usage-based  

---

## Checklist

- [ ] VPS created (EU region)
- [ ] Docker installed
- [ ] `.env` with strong `POSTGRES_PASSWORD` + matching `DATABASE_URL`
- [ ] `./scripts/vps-deploy.sh` → healthz OK
- [ ] `ploy-sim init-profiles` once
- [ ] `sim-forward` container running
- [ ] SSH tunnel from Mac → `/paper` loads
- [ ] Ingest logs show ticks (not constant errors)

When all boxes are checked, you can close the laptop; the VPS keeps running the agent.
