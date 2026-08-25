# Task-Pilot

An agentic AI assistant for calendar and task management.

The goal is a conversational assistant you can talk to in plain English —
*"Add a meeting tomorrow at 10 AM"*, *"Move my project work to Friday"*,
*"Cancel my 3 PM task"* — that figures out which calendar operations are needed
and carries them out for you, instead of making you click through a calendar UI.

**Status: Week 1 complete.** The app authenticates with Google Calendar and
creates real events. The reasoning layer (LangChain / LangGraph) comes next.

## Roadmap

| Week | Goal | Status |
|------|------|--------|
| 1 | Project scaffold; connect to Google Calendar and create an event | Done |
| 2 | Full CRUD helpers (update, delete, reschedule, query) | Next |
| 3 | LangGraph agent: natural language in, calendar operations out | Planned |
| 4 | Multi-step reasoning ("reschedule all my tasks for tomorrow") | Planned |
| 5 | FastAPI backend, Streamlit UI, test suite | Planned |
| 6 | Evaluation harness, logging, deployment | Planned |

## Project structure

```
Task-Pilot/
├── app/
│   ├── __init__.py
│   ├── config.py            # settings loaded from .env, with defaults
│   ├── calendar_service.py  # the only module that talks to Google Calendar
│   └── main.py              # Week 1 smoke test
├── docs/
│   └── ARCHITECTURE.md      # target architecture for weeks 2-6
├── requirements.txt
├── .env.example             # copy to .env
├── .gitignore
└── README.md
```

`calendar_service.py` is deliberately the only place that knows about the Google
API. Its functions take an authenticated `service` object as their first
argument, so the agent added in a later week can wrap them as tools directly,
authenticating once and reusing the client across many calls.

## Architecture

[**docs/ARCHITECTURE.md**](docs/ARCHITECTURE.md) is the design the remaining
weeks build toward — layer boundaries, the LangGraph state machine, the safety
model for calendar mutations, and the migration path from the code above.

![Task-Pilot architecture: five layers from interface down to domain. Requests enter through Streamlit and FastAPI, run through a LangGraph node chain, call read or plan tools, and every write passes a confirmation gate and the single EventService.apply function before reaching the CalendarPort and its Google or fake adapter.](docs/architecture-diagram.png)

The four decisions that shape everything else:

- **The calendar sits behind a port**, with an in-memory fake alongside the
  Google adapter — so the agent, the API, and the Week 6 evaluations all run
  offline, deterministically, against a seeded calendar.
- **Nothing writes to a calendar directly.** Every change is first built as an
  inert `MutationPlan` that can be previewed, confirmed, logged, and asserted
  on. A single function executes one.
- **The model classifies and phrases; it never computes.** Dates, overlaps, and
  free-slot arithmetic are ordinary Python resolved against an injected clock.
- **Business logic lives in services, not in tools.** Every capability stays
  callable — and testable — with no LLM in the loop.

## Google Cloud setup

You need an OAuth client so the app can act on your calendar with your
permission. This is free and takes about ten minutes.

### 1. Create a project

1. Go to <https://console.cloud.google.com/>.
2. Click the project dropdown in the top bar, then **New Project**.
3. Name it `Task-Pilot` and click **Create**.
4. Make sure the new project is selected in the top bar before continuing.

### 2. Enable the Google Calendar API

1. Go to **APIs & Services → Library**
   (<https://console.cloud.google.com/apis/library>).
2. Search for **Google Calendar API** and open it.
3. Click **Enable**.

### 3. Configure the consent screen

In newer versions of the console this lives under **Google Auth Platform**; in
older ones it is **APIs & Services → OAuth consent screen**. Either way:

1. Choose **External** as the user type and click **Create**.
   (**Internal** is only available on Google Workspace accounts and skips the
   test-user step below.)
2. Fill in the required fields — app name (`Task-Pilot`), user support email,
   and developer contact email. Everything else can be left blank.
3. On the **Scopes** step, click **Save and Continue** without adding any. The
   app requests its scope at runtime; you do not need to declare it here.
4. On the **Test users** step, click **Add users** and enter **your own Google
   account address** — the one whose calendar you want to manage. **Do not skip
   this.** While the app is unpublished, only listed test users can sign in.
5. Click **Save and Continue**, then **Back to Dashboard**.

### 4. Create the OAuth client

1. Go to **APIs & Services → Credentials**
   (<https://console.cloud.google.com/apis/credentials>).
2. Click **Create Credentials → OAuth client ID**.
3. Set **Application type** to **Desktop app**. This matters — a *Web
   application* client will fail with `redirect_uri_mismatch`.
4. Name it `Task-Pilot Desktop` and click **Create**.
5. Click **Download JSON** in the confirmation dialog.
6. Save that file as **`credentials.json`** in the root of this repository
   (next to `requirements.txt`).

> `credentials.json` and `token.json` are both listed in `.gitignore`. Never
> commit them — they grant access to your calendar.

## Install and run

```bash
git clone https://github.com/Srishanth0902/Task-Pilot.git
cd Task-Pilot

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env
```

Put your `credentials.json` in the project root, then:

```bash
python -m app.main
```

The first run opens a browser asking you to sign in and grant calendar access.
Google will warn that the app is unverified — click **Advanced → Go to
Task-Pilot (unsafe)**. That warning is expected for an app in testing; you are
granting access to your own project.

After you approve, a `token.json` is written and later runs need no interaction.

Expected output:

```
Connecting to Google Calendar...
Connected to calendar: you@example.com
Calendar ID: primary   Timezone: Asia/Kolkata

Creating event 'Task-Pilot Test Event'
  Mon 24 Aug 2026, 10:00 AM - 11:00 AM
Event created (id: 7f3k2m9p1q...)
  https://www.google.com/calendar/event?eid=...

Next 5 upcoming event(s):
  2026-08-24T10:00:00+05:30 Task-Pilot Test Event
  ...

Done. Week 1 objective met: connected and created an event.
```

Open the printed link, or check your Google Calendar for tomorrow at 10 AM, to
confirm the event is really there.

## Configuration

All settings are optional — see `.env.example` for the full list.

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_CREDENTIALS_FILE` | `credentials.json` | OAuth client downloaded from Google Cloud |
| `GOOGLE_TOKEN_FILE` | `token.json` | Cached login, created automatically |
| `GOOGLE_CALENDAR_ID` | `primary` | Which calendar to use |
| `TIMEZONE` | `Asia/Kolkata` | IANA timezone for created events |

## Troubleshooting

**`Google OAuth client file not found`**
`credentials.json` is missing from the project root. Redo step 4 above, or point
`GOOGLE_CREDENTIALS_FILE` in `.env` at wherever you saved it.

**`Error 403: access_denied`**
Your Google account is not on the consent screen's **Test users** list. Add it
(step 3.4) and try again.

**`Error 400: redirect_uri_mismatch`**
The OAuth client was created as a *Web application*. Create a new one with
application type **Desktop app** (step 4.3) and replace `credentials.json`.

**`Google Calendar API has not been used in project ... before or it is disabled`**
The API is not enabled. Redo step 2, then wait a minute for it to propagate.

**`invalid_grant` or repeated auth failures**
The cached token has gone stale or its scopes changed. Delete `token.json` and
run again to re-authenticate.

**Browser does not open, or you are on a remote/headless machine**
`run_local_server()` needs a browser on the same machine. Run the first
authentication on your own computer, then copy the generated `token.json` to the
remote machine.
