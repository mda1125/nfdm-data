# dairydesk-agent (Cloudflare Worker)

Backend for the "Ask the Desk" chat bubble on [thedairydesk.com](https://thedairydesk.com).
Runs on Cloudflare's free tier, calls the DeepSeek API server-side so the API
key never reaches the browser.

## Setup

```bash
cd worker
npm install

# 1. Create the KV namespace used for per-IP daily rate limiting
npx wrangler kv namespace create RATE_LIMIT
# copy the returned "id" into wrangler.toml (kv_namespaces[0].id)

# 2. Store the DeepSeek key as a secret (never committed)
npx wrangler secret put DEEPSEEK_API_KEY

# 3. Deploy
npx wrangler deploy
```

Deploy prints your Worker URL, e.g.:

```
https://dairydesk-agent.<your-subdomain>.workers.dev
```

## Wire it into the site

Replace `YOUR-SUBDOMAIN` with the real value in **two** places:

1. `../index.html` — the CSP `connect-src` directive
2. `../app.js` — the `AGENT_ENDPOINT` constant (append `/chat`)

## Notes

- CORS is locked to `thedairydesk.com` / `www.thedairydesk.com` — update
  `ALLOWED_ORIGINS` in `src/index.js` if you test from a different origin
  (e.g. `https://mda1125.github.io`).
- Rate limit is 30 questions/IP/day, stored in KV. Adjust `DAILY_LIMIT_PER_IP`
  in `src/index.js`.
- Each question is answered stateless (no conversation memory) to keep the
  Worker simple and cheap. Add multi-turn history later if needed.
- No bot/abuse challenge (e.g. Cloudflare Turnstile) yet — the rate limit is
  the only guardrail against a key leaking or being hammered. Add Turnstile
  before pointing this at a much larger audience.
