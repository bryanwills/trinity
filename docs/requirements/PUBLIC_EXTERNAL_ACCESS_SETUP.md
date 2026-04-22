# Public External Access Setup for Trinity

> **Purpose**: Enable external (non-VPN) access to Trinity's public endpoints while keeping the main platform internal.
> **Infrastructure**: Cloudflare Tunnel + Tailscale VPN
> **Status**: Implemented (Cloudflare Tunnel)
> **Date**: 2026-04-11

---

## Current State

- Trinity instances run behind Tailscale VPN (private access)
- Public endpoints exposed via Cloudflare Tunnel (`cloudflared` Docker service)
- Path filtering configured in Cloudflare dashboard (only public routes exposed)
- No inbound firewall ports required — tunnel connects outbound only

## Architecture

```
┌──────────────┐                         ┌─────────────────────────────┐
│ VPN Users    │──► Tailscale ─────────►│ Caddy → Frontend/Backend    │
│ (internal)   │   (private access)      │                             │
└──────────────┘                         │                             │
                                         │                             │
┌──────────────┐   ┌───────────────┐     │  ┌───────────┐             │
│ Telegram     │──►│  Cloudflare   │────►│  │cloudflared│──► nginx/   │
│ Slack        │   │  Edge         │     │  │ (docker)  │   backend   │
│ Public users │   │  (TLS, DDoS)  │     │  └───────────┘             │
│ Nevermined   │   │               │     │                             │
└──────────────┘   └───────────────┘     └─────────────────────────────┘
                   public.your-domain.com
```

---

## Endpoints Exposed Externally

Configure these paths in Cloudflare dashboard ingress rules:

| Route | Service | Purpose |
|-------|---------|---------|
| `/` | `http://frontend:80` | Frontend SPA root |
| `/chat/*` | `http://frontend:80` | Public chat UI |
| `/api/public/*` | `http://backend:8000` | Public API + Slack OAuth callback |
| `/api/telegram/webhook/*` | `http://backend:8000` | Telegram bot webhooks |
| `/api/whatsapp/webhook/*` | `http://backend:8000` | Twilio WhatsApp inbound webhooks |
| `/api/paid/*/chat` | `http://backend:8000` | Nevermined paid chat |
| `/api/paid/*/info` | `http://backend:8000` | Payment info (public) |
| `/assets/*` | `http://frontend:80` | Static assets (JS, CSS) |
| `/favicon.ico`, `/vite.svg` | `http://frontend:80` | Icons |

Everything else is not routed (returns 404 from Cloudflare).

---

## Setup Guide (Per Instance)

### Prerequisites

- Cloudflare account (free tier works)
- Domain added to Cloudflare (or CNAME access from your DNS provider)
- Trinity instance running with docker-compose

### Step 1: Create Tunnel in Cloudflare

1. Go to [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → Networks → Tunnels
2. Click **Create a tunnel**
3. Choose **Cloudflared** connector type
4. Name: `trinity-<instance-name>` (e.g., `trinity-prod1`)
5. Copy the **tunnel token** (starts with `eyJ...`)

### Step 2: Configure Public Hostname

In the tunnel configuration, add a public hostname:

- **Subdomain**: `public` (or instance-specific, e.g., `prod1`)
- **Domain**: `your-domain.com`
- **Service**: `http://frontend:80`

For backend API paths, add additional routes or use a catch-all with path-based routing in Cloudflare Access.

> **Note**: Cloudflare Tunnel routes the entire hostname to one origin service. To split frontend/backend paths, route to `http://frontend:80` — nginx in the frontend container already proxies `/api/*` to the backend.

### Step 3: Add DNS Record

If your domain's nameservers are NOT on Cloudflare (e.g., Google Cloud DNS):

```bash
# Add CNAME record pointing to tunnel
# Replace <tunnel-id> with your tunnel's ID
gcloud dns record-sets create public.your-domain.com \
  --zone=your-dns-zone \
  --type=CNAME --ttl=300 \
  --rrdatas=<tunnel-id>.cfargotunnel.com. \
  --project=your-project
```

If your domain IS on Cloudflare DNS, the CNAME is created automatically.

### Step 4: Configure Trinity Instance

Add to `.env` on the instance:

```bash
# Cloudflare Tunnel token
TUNNEL_TOKEN=eyJ...

# Public URL (must match your Cloudflare hostname)
PUBLIC_CHAT_URL=https://public.your-domain.com
```

### Step 5: Start with Tunnel Profile

```bash
# Start all services including tunnel
docker compose -f docker-compose.prod.yml --profile tunnel up -d

# Verify tunnel is running
docker logs trinity-cloudflared --tail 20
```

### Step 6: Verify

```bash
PUBLIC=https://public.your-domain.com

# Should return 200:
curl -s -o /dev/null -w "%{http_code}" "$PUBLIC/"
curl -s -o /dev/null -w "%{http_code}" "$PUBLIC/chat/test-token"
curl -s -o /dev/null -w "%{http_code}" "$PUBLIC/api/public/link/test"

# Should return 404 (not routed):
curl -s -o /dev/null -w "%{http_code}" "$PUBLIC/api/agents"
curl -s -o /dev/null -w "%{http_code}" "$PUBLIC/login"
```

---

## Why Cloudflare Tunnel

| Concern | GCP Load Balancer (previous) | Cloudflare Tunnel (current) |
|---------|------------------------------|----------------------------|
| Cost per instance | ~$18-25/mo | Free |
| Setup effort | ~15 gcloud commands | 1 env var + dashboard config |
| Path filtering | Cloud Armor rules | Cloudflare Access / ingress |
| TLS | Google-managed cert | Automatic |
| Inbound ports | Required (80, 443) | None |
| DDoS protection | Basic | Included |
| Multi-instance | Complex per-instance | Token per instance |

---

## GCP Load Balancer Decommission

The previous GCP LB setup for `public.abilityai.dev` has been replaced. Resources to clean up:

```bash
PROJECT=mcp-server-project-455215

# Remove forwarding rules
gcloud compute forwarding-rules delete trinity-https-forwarding --global --project=$PROJECT -q
gcloud compute forwarding-rules delete trinity-http-forwarding --global --project=$PROJECT -q

# Remove proxies
gcloud compute target-https-proxies delete trinity-https-proxy --global --project=$PROJECT -q
gcloud compute target-http-proxies delete trinity-http-proxy --global --project=$PROJECT -q

# Remove SSL cert
gcloud compute ssl-certificates delete trinity-public-cert --global --project=$PROJECT -q

# Remove URL map
gcloud compute url-maps delete trinity-url-map --global --project=$PROJECT -q

# Remove backend service
gcloud compute backend-services delete trinity-backend-service --global --project=$PROJECT -q

# Remove health check
gcloud compute health-checks delete trinity-health-check --project=$PROJECT -q

# Remove instance group
gcloud compute instance-groups unmanaged delete trinity-ig --zone=us-central1-a --project=$PROJECT -q

# Remove security policy
gcloud compute security-policies delete trinity-public-policy --project=$PROJECT -q

# Remove firewall rule
gcloud compute firewall-rules delete allow-trinity-web --project=$PROJECT -q

# Release static IP
gcloud compute addresses delete trinity-public-ip --global --project=$PROJECT -q

# Remove old DNS A record
gcloud dns record-sets delete public.abilityai.dev \
  --zone=abilityai-dev-zone --type=A --project=$PROJECT
```

---

## Troubleshooting

### Tunnel not connecting

```bash
# Check container logs
docker logs trinity-cloudflared --tail 50

# Common issues:
# - Invalid TUNNEL_TOKEN → "failed to unmarshal tunnel credentials"
# - Network issues → "connection refused" (check docker network)
```

### Webhook not reaching backend

```bash
# Verify frontend nginx proxies /api/* to backend
docker exec trinity-frontend curl -s http://backend:8000/health

# Verify tunnel routes to frontend
curl -s https://public.your-domain.com/api/health
```

### DNS not resolving

```bash
# Check CNAME record
dig CNAME public.your-domain.com +short
# Should return: <tunnel-id>.cfargotunnel.com.

# If using Cloudflare DNS, check it's proxied (orange cloud)
```

---

## Security Summary

| Layer | Protection |
|-------|------------|
| **Cloudflare Edge** | TLS termination, DDoS protection, bot filtering |
| **Tunnel (outbound only)** | No inbound ports, no public IP exposure |
| **Path filtering** | Only public routes exposed via Cloudflare config |
| **Token security** | 192-bit random strings for public chat links |
| **Webhook auth** | Dual-layer: URL secret + header secret (Telegram), URL secret + HMAC-SHA1 (Twilio/WhatsApp) |
| **Signature verification** | HMAC-SHA256 (Slack, Process webhooks), HMAC-SHA1 (Twilio — via `twilio.request_validator.RequestValidator`) |
| **Rate limiting** | 30-60 req/min per IP (backend) |

---

## Files Referenced

- `docker-compose.prod.yml` — `cloudflared` service definition (profile: tunnel)
- `.env.example` — `TUNNEL_TOKEN` and `PUBLIC_CHAT_URL` configuration
- `src/backend/config.py` — `PUBLIC_CHAT_URL` env var reading
- `src/backend/routers/telegram.py` — Telegram webhook URL construction
- `src/backend/services/slack_service.py` — Slack OAuth callback URL construction
- `src/backend/routers/public_links.py` — Public chat link URL construction

---

*Last updated: 2026-04-11*
