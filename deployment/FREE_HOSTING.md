# Free pilot deployment (prepared, not published)

The intended setup is one Render **Free Web Service**, Neon **Free PostgreSQL**,
Brevo **Free transactional email**, and GitHub scheduled Actions in this public
repository. There is no paid disk, worker, database, domain, or subscription in
the supplied Blueprint. Stay on each provider's Free plan; do not enable paid
upgrades. Free allowances and availability can change.

## What the code prepares

- `Dockerfile.render` builds the React UI and serves it with FastAPI from one
  origin: `/` is the UI, `/api/*` is the authenticated API.
- `render.yaml` uses Free compute and deploys after linked CI checks pass.
- The host probes `/api/ready`, which returns 503 when durable storage is down.
  `/api/health` still reports non-private configuration for the UI.
- Production startup refuses missing frontend builds, malformed ports/login
  lifetime, missing secrets, or mismatched OAuth callbacks. The UI and API share
  one origin, so Google cookies work without cross-site cookie/CORS workarounds.
- The image runs as non-root UID/GID 1000 so it can read Render's runtime secret
  files, following [Render's Docker secret-file guidance](https://render.com/docs/docker-secrets).
- `DATABASE_URL` selects PostgreSQL with required TLS for remote connections.
  Missing cloud storage fails startup in free mode instead of using temporary SQLite.
- Fernet encryption remains on profiles, tokens, chat state/titles, preferences,
  and assignment/study content. IDs, ownership, dates, status, and counters remain
  database metadata; this is not encryption of every database column.
- Transaction advisory locks protect each conversation and token refresh across
  processes, including Neon's pooled URLs. Reservations survive restarts.
- Actual AI planner calls are limited to 40/day globally, 10/day per account,
  and 10/minute globally. Chat messages have a separate 20/minute per-account
  limit. Deterministic reads and confirmations do not consume AI allowance.
  A failed model request still consumes its reservation. Daily windows reset at
  midnight UTC. Other apps using the same OpenRouter key also use its provider quota.
- `OPENROUTER_FREE_ONLY=true` blocks paid model IDs and sends a zero-price provider
  constraint. The default is the currently available `qwen/qwen3.8-27b:free`,
  which passed a live structured-planning smoke test. `openrouter/free` remains
  selectable, but its random provider selection failed our smoke tests.
  There is no paid fallback and no automatic model HTTP retry. Availability and
  planning quality are not guaranteed; check the catalog before deployment.
- Brevo sends over HTTPS with no delivery retries. Budgets are 250/day globally
  and 20/day per account. A claim before sending prevents duplicate reminder
  attempts; uncertain or rejected delivery is reported and is not retried.
- `POST /api/internal/reminders` requires a long bearer secret, serializes sweeps,
  and caps sweep frequency. It never returns emails, tokens, or event content.
- GitHub's reminder workflow is disabled by default. It can invoke the endpoint
  twice an hour, with a 240-second timeout for Render cold starts. It follows no
  redirects and makes no automatic retries. Schedules can be late; reminders are
  best effort. Do not promise exact-time notifications on free hosting.

## Account settings to fill in only when deployment is authorized

Email reminders are currently deferred: the Blueprint uses `EMAIL_PROVIDER=disabled`
and needs no Brevo account, key, or sender. Calendar, chat, and persistent user
storage still work. Leave the scheduled reminder workflow disabled. To enable
email later, change the Blueprint provider to `brevo`, configure `BREVO_API_KEY`
and a verified `EMAIL_SENDER`, and complete the reminder verification below.

1. Create a Neon Free project. Copy its pooled PostgreSQL connection URL with
   `sslmode=require` into Render's `DATABASE_URL` secret.
2. Generate a Fernet key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
   and store it as `TOKEN_ENCRYPTION_KEY`. Back it up separately. Never change it
   while retaining encrypted records. A fresh cloud DB starts empty; local data
   is not automatically copied.
3. Import `render.yaml` and keep the plan Free. Use the assigned
   `https://YOUR-SERVICE.onrender.com` origin as `PUBLIC_APP_URL`; no purchased
   domain is needed. Render requires its `PORT`; the entrypoint reads it.
4. Update Google Cloud's **Web application** client with the exact authorized
   callback `https://YOUR-SERVICE.onrender.com/api/auth/callback`. Retain localhost
   if continuing development. Upload the downloaded credentials JSON as a Render
   Secret File named `credentials.web.json` (path `/etc/secrets/credentials.web.json`).
   Add invited users in Google's Testing audience. Hosting does not change Google's
   publishing status; broader access depends on Google's verification requirements.
   Optionally restrict `ALLOWED_GOOGLE_EMAILS` to the same comma-separated invite list.
5. Revoke the OpenRouter key previously shared in chat and create a replacement.
   Put it in Render's secret `OPENROUTER_API_KEY`, leaving free-only mode enabled.
6. Create a Brevo Free account, enable transactional sending, verify a sender,
   and provide its API key as `BREVO_API_KEY` and sender as `EMAIL_SENDER`.
   Delivery cannot be tested until these settings and the sender are valid.
7. Run `python -m app.deployment_check` with `.env.free.example` settings filled
   through environment variables. This validates configuration, not remote service
   availability. `python -m app.model_check` checks the live public model catalog;
   `--live` makes one synthetic model request without touching Google Calendar.
8. After deployment, copy Render's generated `REMINDER_TRIGGER_SECRET` to a GitHub
   Actions secret of the same name. Add Actions variables `TASK_PILOT_URL` (the
   HTTPS origin) and `ENABLE_SCHEDULED_REMINDERS=true`. GitHub scheduled Actions
   are free on standard runners in public repositories; private repository limits
   differ. Do not enable this before the deployment works.
9. Verify two invited Google accounts, sign-out, ownership separation, a service
   restart/redeploy, and a real reminder to yourself. Then share the link.

## Optional local-data migration

Stop the local app and back up `data/`. Supply the **same** encryption key used by
the source SQLite store as `TOKEN_ENCRYPTION_KEY`, plus the destination
`DATABASE_URL`, then run `python -m app.migrate_storage --source data/users.sqlite3`.
The destination must be empty. The import is atomic, validates encrypted values,
never overwrites destination records, and never modifies the source. Do not run
it against a database already serving users. New deployments may simply start empty.

## Backups and updates

Keep encrypted database backups and the encryption key in separate private places.
Use PostgreSQL's `pg_dump` and rehearse restoring into a separate test database.
Do not put dumps or keys in Git. GitHub pushes to the linked branch redeploy after
CI passes; Neon data remains outside the app container across those deployments.
Render Free sleeps when idle, and free model/email quotas can pause functionality.
The app reports failures instead of replaying uncertain calendar writes.

## Verify the actual production package before enabling deployment

Run the normal backend and frontend checks, then on a computer with Docker Engine:

```text
python scripts/verify_deployment.py --build
```

This builds `Dockerfile.render`, starts the real production entrypoint with a
disposable TLS PostgreSQL database, tests frontend assets and `/api` routing,
checks anonymous access is denied and OAuth redirects use the public callback,
restarts the app to verify encrypted data survives, and checks a database outage
returns 503. The app container is limited to 512 MB and 0.1 CPU, matching the Free
service's configured resources. The test uses synthetic keys and never exchanges
Google tokens or sends mail. The JSON report is written under `.worker-results/`.
It removes only its own UUID-named containers/network. Missing Docker is
**unverified**, not passed. These steps also run in GitHub CI before Render auto-deploy.

If Docker is unavailable, a developer can test the actual server entrypoint with
`python scripts/verify_deployment.py --native` and a **disposable localhost TLS**
`TEST_DATABASE_URL`. This uses an isolated schema and verifies process restart,
not the image build or public TLS. It does not replace the CI container check.

Before sharing the deployed URL, re-run a real two-account Google login and a
real calendar/reminder round trip. Passing local synthetic tests cannot verify
the final Google audience, callback registration, Brevo sender, or provider quota.

References: [Render Free](https://render.com/docs/free),
[Neon pricing](https://neon.com/pricing),
[Brevo Free](https://help.brevo.com/hc/en-us/articles/208580669),
[OpenRouter free router](https://openrouter.ai/openrouter/free),
[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
