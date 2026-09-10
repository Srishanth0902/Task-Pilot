# Task-Pilot

An agentic calendar assistant that turns natural-language requests into safe,
structured Google Calendar operations.

**Status:** Week 2 is complete. Google OAuth and live Calendar CRUD were
verified on September 10, 2026. Qwen3-30B-A3B was then connected through
OpenRouter and used to create a real event from a natural-language request.

## Roadmap

| Week | Goal | Status |
|---|---|---|
| 1 | Project scaffold and Google Calendar CRUD | Live OAuth and CRUD verified |
| 2 | Structured CRUD, Pydantic schemas, LangChain tools, relative dates | Implemented and offline-tested |
| 3 | Advanced agent workflows | Planned |
| 4 | Multi-step reasoning | Planned |
| 5 | Interface and polish | Planned |

## Architecture

```text
User request
    |
    v
OpenRouter / Qwen3-30B-A3B (switchable)
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

## OpenRouter model configuration

The live model is constructed in `app/llm.py`. OpenRouter's OpenAI-compatible
endpoint is accessed through `langchain-openai`, while the calendar agent stays
provider-independent:

```python
python -m app.chat "Add ML study tomorrow at 6 PM"
```

Set `OPENROUTER_MODEL` to another tool-capable OpenRouter model to switch it
without changing agent or calendar code.

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
| `OPENROUTER_API_KEY` | none | Secret API key; required for live LLM calls |
| `OPENROUTER_MODEL` | `qwen/qwen3-30b-a3b` | Switchable OpenRouter model slug |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter API endpoint |

## Live verification and remaining acceptance checks

Verified on September 10, 2026:

- OAuth sign-in and local token creation; credentials and token remain Git-ignored.
- Reading the real calendar and searching for the created event.
- Creating `Agentic AI Project Work` for September 11, 2026, 18:00-19:00 Asia/Kolkata,
  reading it back, and confirming it in the Google Calendar browser interface.
- Renaming and moving a temporary event, deleting it, and checking repeated deletion.

The calendar's display timezone is UTC, so the retained event appears as
12:30-13:30 there: the same instant as 18:00-19:00 Asia/Kolkata.

Also verified on September 10, 2026:

- OpenRouter connectivity with `qwen/qwen3-30b-a3b`.
- Live Qwen tool selection against a fake calendar before allowing a mutation.
- `Add ML study tomorrow at 6 PM for one hour` through the real LLM, LangChain
  agent, calendar tool, and Google Calendar API.
- Live LLM selection and execution of all five tools: create, list, search,
  update, and delete. The CRUD test used a disposable event and verified that
  it was absent after deletion.
- The resulting event was read back as September 11, 2026, 18:00-19:00
  Asia/Kolkata. Google may return UTC timestamps; service responses normalize
  them back to the configured timezone before the LLM sees them.
