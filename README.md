# Task-Pilot

An agentic AI assistant for calendar and task management.

The goal is a conversational assistant you can talk to in plain English —
*"Add a meeting tomorrow at 10 AM"*, *"Move my project work to Friday"*,
*"Cancel my 3 PM task"* — that figures out which calendar operations are needed
and carries them out for you, instead of making you click through a calendar UI.

**Status: Week 1 code complete.** The app authenticates with Google Calendar
and can read, create, update and delete events. The reasoning layer (LangChain /
LangGraph) comes next.

## Roadmap

| Week | Goal | Status |
|------|------|--------|
| 1 | Project scaffold; full calendar CRUD against the real API | Code done |
| 2 | LangChain tool definitions wrapping the calendar helpers | Next |
| 3 | LangGraph agent: natural language in, calendar operations out | Planned |
| 4 | Multi-step reasoning ("reschedule all my tasks for tomorrow") | Planned |
| 5 | Interface and polish | Planned |

### Week 1 sprint tasks

| Task | Deliverable | Where |
|------|-------------|-------|
| 1 | Project structure and dependencies | this repo |
| 2 | Google Cloud project, OAuth consent, credentials, local auth | `get_calendar_service()` + the setup steps below |
| 3 | `get_events()` | `app/calendar_service.py` |
| 4 | `create_event()` | `app/calendar_service.py` |
| 5 | `update_event()`, `delete_event()` | `app/calendar_service.py` |

## Project structure

```
Task-Pilot/
├── app/
│   ├── __init__.py
│   ├── config.py            # settings loaded from .env, with defaults
│   ├── calendar_service.py  # the only module that talks to Google Calendar
│   └── main.py              # Week 1 demo: read, create, update, delete
├── tests/
│   └── test_calendar_service.py
├── requirements.txt
├── .env.example             # copy to .env
├── .gitignore
└── README.md
```

`calendar_service.py` is deliberately the only place that knows about the Google
API. Its functions take an authenticated `service` object as their first
argument, so the agent added in a later week can wrap them as tools directly,
authenticating once and reusing the client across many calls.

## The calendar functions

All of these live in `app/calendar_service.py` and take an authenticated
`service` as their first argument.

| Function | What it does |
|---|---|
| `get_calendar_service()` | Authenticates (browser on first run) and returns the API client |
| `get_calendar_summary(service)` | The calendar's display name — a cheap check that auth worked |
| `get_events(service, max_results=10)` | Upcoming events, recurring ones expanded, ordered by start time |
| `create_event(service, summary, start, end, description=None, location=None)` | Creates a timed event, returns it (with `id` and `htmlLink`) |
| `update_event(service, event_id, summary=None, start=None, end=None, ...)` | Changes only the fields you pass, via `patch` |
| `delete_event(service, event_id)` | Deletes; returns `True`, or `False` if it was already gone |

`start` and `end` are naive `datetime` objects, interpreted in `TIMEZONE`.

Two deliberate choices worth knowing before Week 2 builds on them:

- **`update_event` uses `patch`, not `update`.** You can move a start time
  without sending back the whole event body, and fields you do not mention are
  left untouched. Calling it with nothing to change raises `ValueError` rather
  than making a silent no-op request.
- **`delete_event` treats "already deleted" as success.** Google answers `410
  Gone` for an event that no longer exists; that returns `False` instead of
  raising, which keeps deletion idempotent once an agent can retry a step.

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

The demo walks through every Week 1 operation in order. Expected output:

```
============================================================
Connecting to Google Calendar
============================================================
Connected to calendar: you@example.com
Calendar ID: primary   Timezone: Asia/Kolkata

============================================================
Task 3 - get_events()
============================================================
Upcoming Events:

1. Project Meeting
   2026-08-31 10:00

2. DSA Study
   2026-08-31 18:30

============================================================
Task 4 - create_event()
============================================================
Creating 'Agentic AI Project Work'
  Mon 31 Aug 2026, 06:00 PM - 07:00 PM (1 hour)
Created (id: 7f3k2m9p1q...)
  https://www.google.com/calendar/event?eid=...

Re-reading the calendar to confirm it is really there:
  Confirmed: 'Agentic AI Project Work' is on the calendar.

============================================================
Task 5 - update_event() and delete_event()
============================================================
Created a throwaway event to modify (id: 3a8b...)
  'Task-Pilot Temp Event' at 2026-08-31 21:00

update_event() -> renamed and moved:
  'Task-Pilot Temp Event (renamed)' at 2026-08-31 22:00

delete_event() -> removing it again:
  deleted: True
  deleting again (already gone): False

============================================================
Done
============================================================
All Week 1 operations succeeded: read, create, update, delete.
'Agentic AI Project Work' was left on your calendar - go and look at it.
```

Task 5 deliberately works on a throwaway event and deletes it again, so the
event created for Task 4 stays on your calendar. Open the printed link, or look
at tomorrow at 6 PM in Google Calendar, to confirm it is really there.

## Running the tests

```bash
python -m unittest discover -s tests -t . -v
```

The tests drive the calendar helpers against a fake Google client, so they run
offline with no credentials and never touch a real calendar. They check the
request bodies actually sent to the API — the part that is easy to get wrong and
impossible to verify by reading the code. They use only the standard library, so
they add no dependencies beyond the four the sprint allows.

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
