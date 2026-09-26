# Vultr VPS setup (I2)

One Ubuntu 24.04 VPS (the box that exists: vx1-g-2c-8g-120s, Atlanta, 2 vCPU / 8 GB).

1. `apt install docker.io docker-compose-plugin`; open ports 80 and 443.
2. DNS (I1): six A records at Cloudflare, all "DNS only" (grey cloud, so Caddy can issue certificates): `@`, `api`, `cloud`, `doctor`, `watch`, `family` → the Vultr IP. `device.` is the Pi's Cloudflare named tunnel (`cloudflared tunnel route dns`), not hand-made.
3. Clone the repo (private; deploy key) and write `.env` beside `deploy/` from `.env.example` with the real ATLAS_URI, TIGER_URI, RELAY_SOURCE_KEYS, RELAY_ADMIN_KEY, RELAY_KEY, DEVICE_ID, DEVICE_TOKEN, ELEVENLABS_*, BACKBOARD_API_KEY, NARRATIVE_ROUTING, ANTHROPIC_API_KEY, META_MODEL_API_KEY, WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, DOMAIN.
4. `docker compose --env-file ../.env -f deploy/docker-compose.yml up -d --build`.
5. Health: https://api.irin-out-of-sleep-at-hackgt.tech/v0/health, https://cloud.irin-out-of-sleep-at-hackgt.tech/v1/health, https://irin-out-of-sleep-at-hackgt.tech (the app's dist), plus the doctor., watch., family. pages.
6. Redeploy after a frontend merge: `git pull && docker compose up -d` (Caddy serves the new dist at once); `--build` only for relay/cloud code changes.
