# Private dashboard with Cloudflare

Expose the FastAPI dashboard **only through Cloudflare** (HTTPS + login), while `ploy-web` stays on `127.0.0.1` on your Mac.

**I cannot log into your Cloudflare account or run `cloudflared tunnel login` for you.** Those steps open a browser on **your** machine. Use this doc as a checklist.

## Prerequisites

- A **Cloudflare account** (free tier is enough).
- A **domain** whose DNS is managed by Cloudflare (nameservers point to Cloudflare).
- **Cloudflare Zero Trust** enabled once (dashboard → Zero Trust → it may ask you to pick a team name).
- On your Mac: `ploy-web` running and responding at `http://127.0.0.1:8765` (and `/healthz` returning `ok`).

## Local app settings (important)

In `.env`:

```env
WEB_HOST=127.0.0.1
WEB_PORT=8765
```

Do **not** set `WEB_HOST=0.0.0.0` for this setup. The tunnel connects to localhost from your machine.

## Step-by-step checklist

### 1. Install cloudflared (macOS)

```bash
brew install cloudflared
cloudflared --version
```

### 2. Log in to Cloudflare (browser)

```bash
cloudflared tunnel login
```

Choose the account that owns your domain and authorize. This writes a cert under `~/.cloudflared/`.

### 3. Create a named tunnel

```bash
cloudflared tunnel create ployagent-dashboard
```

Note the printed **Tunnel ID** (UUID). A credentials file is created, for example:

`~/.cloudflared/<TUNNEL_UUID>.json`

### 4. Create `~/.cloudflared/config.yml`

Replace placeholders with your real values:

```yaml
# Tunnel ID from: cloudflared tunnel create ployagent-dashboard
tunnel: <TUNNEL_UUID>
credentials-file: /Users/YOUR_USERNAME/.cloudflared/<TUNNEL_UUID>.json

ingress:
  - hostname: dashboard.yourdomain.com
    service: http://127.0.0.1:8765
  - service: http_status:404
```

- Use a **subdomain** you like (`dashboard`, `ploy`, etc.).
- The **catch-all** `http_status:404` rule at the end is required.

### 5. Route DNS to the tunnel

```bash
cloudflared tunnel route dns ployagent-dashboard dashboard.yourdomain.com
```

In the Cloudflare dashboard you should see a **CNAME** for that hostname pointing at `<uuid>.cfargotunnel.com`.

### 6. Run the tunnel (manual test)

```bash
cloudflared tunnel run ployagent-dashboard
```

Leave this running. In another terminal:

```bash
curl -sS https://dashboard.yourdomain.com/healthz
```

Expect: `{"status":"ok"}` (or similar JSON from your app).

### 7. Lock down with Cloudflare Access (Zero Trust)

1. Open [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → **Access** → **Applications**.
2. **Add an application** → **Self-hosted**.
3. **Application domain**: `dashboard.yourdomain.com` (same as in `config.yml`).
4. **Policy**: add a rule such as **Emails** → list your email and your friends’ emails (or use a Google workspace group, etc.).
5. Save.

Now opening `https://dashboard.yourdomain.com` should show a Cloudflare login **before** the PloyAgent page.

### 8. Optional: run the tunnel as a service (Mac)

After `config.yml` works:

```bash
sudo cloudflared service install
sudo launchctl start com.cloudflare.cloudflared
```

Exact service name can vary; if unsure, keep using `cloudflared tunnel run ployagent-dashboard` in a terminal or use a process manager.

## Slack interactivity (optional)

If you use **Approve/Reject** in Slack, Slack must reach `ploy-slack-events` at **port 8766**. That URL is **not** the same as the dashboard tunnel unless you add it.

Options:

1. **Separate tunnel hostname** in the same `config.yml` (second `ingress` rule) pointing at `http://127.0.0.1:8766`, then put **that** HTTPS URL in Slack → Interactivity Request URL, **or**
2. Use **ngrok**/another tunnel only for port 8766, **or**
3. Run `ploy-slack-events` on a host that already has a public URL.

Do not paste a raw `http://127.0.0.1:8766` URL into Slack; Slack’s servers cannot reach your laptop.

## What your friends use

```text
https://dashboard.yourdomain.com
```

They sign in via Access; only allowed emails can see the dashboard.

## What to keep running locally

- `ploy-web`
- `cloudflared tunnel run ployagent-dashboard` (or the system service)
- `ploy-ingest`, `ploy-enrich`, `ploy-reason` (so data stays fresh)
- `ploy-notify` / `ploy-slack-events` as needed

## Troubleshooting

| Symptom | What to check |
|--------|----------------|
| `502` or connection error | Is `ploy-web` up? `curl http://127.0.0.1:8765/healthz` |
| Tunnel errors on start | `config.yml` tunnel ID and `credentials-file` path must match the JSON file |
| DNS not resolving | Wait a few minutes; confirm CNAME in Cloudflare DNS |
| Page loads but no Access login | Access app domain must match hostname; policy must include the visitor’s email |
| Wrong site / 404 | Last `ingress` rule must be `http_status:404` |

## Notes

- The dashboard has **no built-in login**. Cloudflare Access is the security boundary.
- Keeping `WEB_HOST=127.0.0.1` avoids exposing the app on your LAN unnecessarily.
