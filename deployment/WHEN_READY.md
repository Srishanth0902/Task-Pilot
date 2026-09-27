# When you choose to deploy

Nothing in this preparation publishes the site or changes Google's testing status.
The reference target is one Linux server with persistent local storage.

## Prepared in the repository

- `compose.production.yml`: API, frontend and Caddy HTTPS gateway. Only ports
  80 and 443 are exposed publicly; the API is on the private container network.
- `.env.production.example`: no real domain or secrets have been filled in.
- Encrypted SQLite data and separate logs are retained in named volumes.
- Secure session cookies are selected automatically from the HTTPS public URL.
- OAuth callback access logging is disabled at Nginx and Uvicorn to avoid saving
  authorization codes in request logs. Production workflow logs omit user messages,
  event details, model outputs and exception strings, retaining timing metadata.
- `python -m app.deployment_check`: read-only configuration preflight. It does
  not certify Google verification, DNS, certificate issuance or model capacity.
- `python -m evaluation.load_multiuser`: 56 simultaneous conversations using
  actual graph execution, encrypted storage and locks, but fake model/Calendar.
  It verifies persistence and cross-account denial after reopening the database.

## Settings that must wait for your hosting choice

1. Choose a domain, point its DNS at the server and ensure ports 80/443 are available.
2. Copy `.env.production.example` to `.env.production`. Set `PUBLIC_APP_URL` to
   the exact HTTPS origin, add OpenRouter credits/key and an external Fernet key.
   Keep this file readable only by the server operator. Do not copy the development
   database unless intentionally migrating its users and matching encryption key.
3. In Google Cloud, add `https://YOUR-DOMAIN/api/auth/callback` to the Web client's
   authorized redirect URIs and download its updated JSON as `credentials.web.json`.
   Retain the localhost URI if continuing local development.
4. Provide a public app homepage, privacy policy, terms and support contact. The
   policy must accurately disclose Google Calendar data use, encrypted storage,
   retention/deletion practices, and transmission of conversation/calendar context
   to OpenRouter and the model provider. Decide these practices before publishing
   policy statements; this repository does not invent an operator identity or promises.
5. While Google is in Testing, add the actual testers' account addresses in Audience.
   For general availability, complete the required Google branding/data-access
   verification and publishing steps. A deployed server alone does not remove
   Google's test-user restrictions. Prepare a video demonstrating Calendar scopes
   if Google requests it. Do not claim verification until Google approves it.
6. Only when ready, run `docker compose -f compose.production.yml up --build -d`.
   The API performs the preflight before startup; Caddy requests certificates.
   Its first run needs working DNS and Internet connectivity.
7. Test two real Google accounts from different devices: each should see only its
   own calendar and saved conversations. Check sign-out and a restart. Then measure
   real model latency, errors and cost under expected traffic. The synthetic
   56-user test does not establish OpenRouter's capacity or pricing.

## Operations

Back up SQLite consistently with SQLite's backup API (or stop the API before
copying the data volume). Back up the encryption key separately. Rehearse restoring
both in an isolated environment. Never delete the data volume during routine
updates (`docker compose down -v` deletes named volumes).

Start with one API worker. Multiple workers can share local storage, but each worker
must use the same database and key. Do not scale this deployment across hosts or
network filesystems without replacing SQLite/file locks with a shared database.
Use a provider account with sufficient credits and monitor its limits. Calendar
mutations must not be blindly retried after uncertain failures.

References:
- https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification
- https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification
- https://caddyserver.com/docs/automatic-https
