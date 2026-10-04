# Free-hosting preparation validation — updated 4 October 2026

This is evidence for local preparation, **not a deployed-production sign-off**.
No provider accounts or public deployment were created in this work.

## Passed

- `python -m unittest discover -s tests -t .`: **352 tests**, exit 0.
  Python 3.13 on Windows; `TEST_DATABASE_URL` pointed at a temporary real
  PostgreSQL 17 server bound to localhost, not an account's cloud database.
- `python -m unittest tests.test_postgres_store -v`: **26 tests**, exit 0.
  Covers account ownership, encrypted values, durable conversations, expiry,
  concurrent locks/budgets, assignment/study timestamps, reminder claims, and
  atomic SQLite migration with wrong-key rejection and nonempty-target refusal.
- After pinning the free Qwen default,
  `python -m unittest tests.test_llm tests.test_scheduler_trigger tests.test_free_hosting`:
  **17 tests**, exit 0.
- `python -m unittest tests.test_deployment_runner -v`: **4 tests**, exit 0,
  run after adding the image/native smoke harness. Missing Docker is explicitly
  unverified; an exited server cannot be mistaken for an older ready process.
- `npm run build`: production TypeScript/Vite build succeeded.
- `npm test`: **12 tests** passed.
- `npx playwright test --reporter=line`: **10 browser tests** passed.
- `python -m app.evaluation`: the **45-query dataset** validates. This command
  validates the dataset; it does not measure live-model accuracy.
- Combined `app.web` smoke check: `/`, `/api/health`, and `/api/auth/me` respond;
  anonymous identity stays signed out. Local API restart exposes the tested
  `qwen/qwen3.8-27b:free` model and IST timezone.
- `python scripts/verify_deployment.py --native`: exit 0, **3 checks passed**.
  Runs the exact `python -m app.serve` entrypoint with synthetic hosting settings,
  real localhost TLS PostgreSQL, the actual compiled frontend, a dynamically
  selected port, Secure/HttpOnly OAuth cookies, anonymous API denials, correct
  public callback, and encrypted-record persistence after a real process restart.
  Temporary processes and the UUID-owned database schema were cleaned up.
- Public model catalog advertises zero token prices and structured outputs for
  the configured model. A synthetic live structured planner request passed with
  the pinned free Qwen model. It made **no Google Calendar calls**.
- `git diff --check` succeeded. Real `.env`, OAuth JSON, tokens, and the local
  data directory are not Git-tracked; deployment examples contain blank secrets.

## Failed external checks / limitation

The random `openrouter/free` router failed live structured-planning smoke checks:
one selected provider returned an empty response with an error finish reason.
A direct free-Qwen attempt also failed before a subsequent attempt passed.
This is **not** a guarantee of uninterrupted free-provider service. The default
is now pinned to the passing Qwen variant, without paid fallback or automatic
HTTP retries. Quotas and safety checks continue to apply.

## Unverified until suitable environment / deployment

- Docker image builds/runtime: Docker Desktop could not initialize its Linux
  engine because WSL is not installed. `python scripts/verify_deployment.py`
  explicitly reported **UNVERIFIED**. CI now builds and starts the combined
  production image against disposable TLS PostgreSQL with 512 MB/0.1 CPU limits;
  that CI run has not occurred. Native-entrypoint success is not a Docker pass.
- Neon remote connectivity, persistence across an actual Render redeploy,
  Brevo real delivery, and GitHub's scheduled invocation.
- Google sign-in for two invited accounts on the eventual public HTTPS origin.
- Production DNS/TLS, provider dashboards, OAuth publishing/verification status,
  and cloud backup/restore rehearsal.

The account settings and activation sequence are in `FREE_HOSTING.md`. These
cannot be completed safely before choosing the deployed origin and providing
the corresponding account secrets. The scheduler remains disabled by default.

One Antigravity worker performed a read-only file review (no source edits or
test runs). Codex inspected its evidence and retained final judgment: static
EXPOSE metadata is not a Render port error; this UI currently has no pathname
SPA routes; secrets are deliberately dashboard-managed; and PostgreSQL uses
psycopg[binary], not a source-built psycopg2 dependency. No worker patch was applied.
