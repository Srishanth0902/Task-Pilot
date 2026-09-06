# Task-Pilot

An agentic calendar assistant that turns natural-language requests into safe,
structured Google Calendar operations.

**Status:** Week 2 implementation is complete and tested offline. Google OAuth
and a live LLM provider are deliberately deferred, so no real calendar was
changed during development.

## Roadmap

| Week | Goal | Status |
|---|---|---|
| 1 | Project scaffold and Google Calendar CRUD | Code complete; live OAuth pending |
| 2 | Structured CRUD, Pydantic schemas, LangChain tools, relative dates | Implemented and offline-tested |
| 3 | Advanced agent workflows | Planned |
| 4 | Multi-step reasoning | Planned |
| 5 | Interface and polish | Planned |

## Architecture

```text
User request
    |
    v
Injected LangChain-compatible LLM
    |
    v
Tool selection + Pydantic validation
    |
    v
Structured Calendar tool
    |
    v
Google Calendar API
```

Neither the model nor the Google service is created at import time. Both are
injected, which keeps the code provider-independent and makes the whole Week 2
path testable using scripted fakes.

## Target architecture

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

## Project structure

```text
Task-Pilot/
|-- app/
|   |-- agent.py              # provider-independent LangChain agent factory
|   |-- calendar_service.py   # Google OAuth and structured Calendar CRUD
|   |-- calendar_tools.py     # five LangChain StructuredTool definitions
|   |-- config.py             # environment-backed configuration
|   |-- date_utils.py         # timezone-aware relative-date parsing
|   |-- main.py               # live Google Calendar CRUD demonstration
|   `-- schemas.py            # Pydantic input contracts
|-- docs/
|   |-- ARCHITECTURE.md       # target architecture the later weeks build toward
|   `-- architecture-diagram.png
|-- tests/                    # offline service, schema, tool, date, and agent tests
|-- requirements.txt
|-- .env.example
|-- .gitignore
`-- README.md
```

## Week 2 functionality

### Structured Calendar operations

The functions in `app/calendar_service.py` all return dictionaries instead of
raw Google resources. Successful create/update output follows this shape:

```json
{
  "success": true,
  "event_id": "abc123",
  "title": "DSA Study",
  "start": "2026-08-25T18:00:00+05:30",
  "end": "2026-08-25T19:00:00+05:30",
  "description": null,
  "location": null,
  "html_link": "https://calendar.google.com/...",
  "status": "confirmed"
}
```

Implemented operations:

| Function | Purpose |
|---|---|
| `get_events()` | List upcoming events in chronological order |
| `search_events()` | Search upcoming event text using Google's `q` parameter |
| `create_event()` | Create a timezone-aware timed event |
| `update_event()` | Patch only supplied fields |
| `delete_event()` | Idempotently delete by event ID |

API failures also return structured data with `success: false`, an error type,
message, and HTTP status code when Google supplies one.

### Pydantic schemas

`app/schemas.py` defines:

- `CreateEventInput`
- `GetEventsInput`
- `SearchEventInput`
- `UpdateEventInput`
- `DeleteEventInput`

They validate required fields, event ranges, update semantics, result limits,
and timezone handling. A missing create-event end time defaults to one hour
after the start.

### LangChain tools

`build_calendar_tools(service)` returns five `StructuredTool` instances:

- `create_calendar_event`
- `list_calendar_events`
- `search_calendar_events`
- `update_calendar_event`
- `delete_calendar_event`

The service can be a real authenticated Google client or a fake one. The test
suite also runs a complete agent loop with a scripted fake chat model, proving
that LangChain can select and execute `create_calendar_event` without an API
key.

### Relative dates and timezone rules

Internally, timed events always use timezone-aware ISO-8601 values. Supported
input includes:

- `tomorrow at 6 PM`
- `next Monday at 9 AM`
- `Friday at 6 PM`
- `in two hours`
- `next week`
- ISO-8601, with or without an offset

Naive ISO values are interpreted in `TIMEZONE`; aware values are converted to
that timezone. A bare weekday always resolves to a future occurrence. `next
week` means exactly seven days later and preserves the current local time unless
a time is explicitly included.

## Install and test offline

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m unittest discover -s tests -t . -v
```

The tests do not need `credentials.json`, `token.json`, an OpenAI key, or any
network access. They cover the Google request payloads, structured responses,
schemas, all required date phrases, LangChain tool schemas, and a fake-model
tool-selection loop.

## Connecting a model later

The project intentionally does not install a model-provider package. When a
provider is selected, create its LangChain chat model and inject it:

```python
from app.agent import run_calendar_request
from app.calendar_service import get_calendar_service

service = get_calendar_service()
model = your_langchain_compatible_chat_model

result = run_calendar_request(
    "Add ML study tomorrow at 6 PM",
    model=model,
    service=service,
)
```

The application code does not need to change when switching between OpenAI,
Anthropic, Google, or a compatible local model; only model construction changes.

## Google Cloud setup (deferred live test)

1. Create or select a project in the Google Cloud Console.
2. Enable the Google Calendar API.
3. Configure Google Auth Platform branding and audience.
4. For an external app in Testing, add your Google account as a test user.
5. Under Google Auth Platform > Clients, create a **Desktop app** client.
6. Download it as `credentials.json` into the repository root.
7. Run `python -m app.main` and approve access in the browser.

The app writes the resulting OAuth token to `token.json`. Both files, along
with `.env`, are excluded by `.gitignore` and must never be committed.

The live demo reads upcoming events, creates `Agentic AI Project Work` tomorrow
at 6 PM for one hour, reads the calendar again, and exercises update/delete on
a throwaway event.

## Configuration

Copy `.env.example` to `.env` if you want to override defaults.

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_CREDENTIALS_FILE` | `credentials.json` | Desktop OAuth client file |
| `GOOGLE_TOKEN_FILE` | `token.json` | Cached user authorization |
| `GOOGLE_CALENDAR_ID` | `primary` | Calendar to operate on |
| `TIMEZONE` | `Asia/Kolkata` | IANA timezone for event operations |

## Deferred acceptance checks

These require credentials and have not been claimed as complete:

- OAuth sign-in against a real Google account
- Reading a real calendar
- Creating and visually confirming a real event
- Natural-language tool selection by a live LLM provider
