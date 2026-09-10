# Task-Pilot

An agentic calendar assistant that turns natural-language requests into safe,
structured Google Calendar operations.

**Status:** Weeks 1-5 are complete. Task Pilot now has a tested FastAPI backend
and Streamlit chat application around the live LangGraph, OpenRouter/Qwen, and
Google Calendar workflow.

## Roadmap

| Week | Goal | Status |
|---|---|---|
| 1 | Project scaffold and Google Calendar CRUD | Live OAuth and CRUD verified |
| 2 | Structured CRUD, Pydantic schemas, LangChain tools, relative dates | Implemented and offline-tested |
| 3 | Stateful LangGraph workflow and follow-up context | Implemented and tested |
| 4 | Bulk operations, conflicts, confirmations, and free slots | Implemented and live-tested |
| 5 | FastAPI backend, Streamlit UI, and application testing | Implemented and live-tested |

## Architecture

```text
Streamlit -> FastAPI -> LangGraph -> Calendar tools -> Google Calendar
                         |
                         +----------> OpenRouter / Qwen

LangGraph:
START -> Understand query -> Search needed?
                              | yes            | no
                              v                v
                         Search event      Execute action
                              |
                              v
                         Resolve event ----+
                              |
                              v
                         Execute -> Verify -> Respond -> END
```

Neither the model nor the Google service is created at import time. Both are
injected, which keeps the code provider-independent and makes the complete
workflow testable using scripted fakes.

## Project structure

```text
Task-Pilot/
|-- app/
|   |-- agent.py              # compatibility entry points for the graph
|   |-- api.py                # FastAPI routes, schemas, and lazy shared runtime
|   |-- api_client.py         # typed Streamlit-to-FastAPI HTTP client
|   |-- graph_agent.py        # state, nodes, routing, memory, and conversation API
|   |-- calendar_service.py   # Google OAuth and structured Calendar CRUD
|   |-- calendar_tools.py     # five LangChain StructuredTool definitions
|   |-- config.py             # environment-backed configuration
|   |-- date_utils.py         # timezone-aware relative-date parsing
|   |-- main.py               # live Google Calendar CRUD demonstration
|   |-- chat.py               # one-shot or interactive stateful CLI
|   |-- scheduling.py         # conflicts, gaps, and bulk-change planning
|   `-- schemas.py            # Pydantic input contracts
|-- tests/                    # offline service, schema, tool, date, and agent tests
|-- streamlit_app.py          # chat UI, history, confirmations, event display
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

## Week 3 LangGraph workflow

`app/graph_agent.py` defines `CalendarAgentState` with the requested fields:
`messages`, `user_query`, `intent`, `selected_event`, `tool_result`, and
`pending_action`. It also retains candidate events, clarification/error state,
verification status, and the final response.

The compiled `StateGraph` contains these nodes:

- `understand_query`
- `search_calendar`
- `resolve_event`
- `execute_action`
- `verify_result`
- `generate_response`
- `handle_error`

Update and delete requests without an event ID search first. One match proceeds;
zero matches return safely; multiple matches preserve the candidates and ask the
user to choose without changing the calendar. `InMemorySaver` checkpoints state
under a conversation `thread_id`, so follow-up answers complete the pending
action while retaining the selected event.

Start an interactive session to exercise follow-up context:

```powershell
python -m app.chat
```

Example:

```text
You: Move my DSA session tomorrow
Task Pilot: I found DSA session at 2026-09-11T18:00:00+05:30. What time should I move it to?
You: 8 PM
Task Pilot: Updated DSA session from 2026-09-11T20:00:00+05:30 to 2026-09-11T21:00:00+05:30.
```

## Week 4 advanced scheduling

Bulk changes are planned separately from execution. The graph searches the
requested time range, builds a complete list of affected events, displays every
proposed update or deletion, and checkpoints that plan. Only an explicit `yes`
on the same conversation thread reaches `execute_bulk_action`; `no` clears the
plan without calling a mutation tool.

Supported advanced requests include:

- `Move all my study tasks from today to tomorrow.`
- `Move all meetings tomorrow by 30 minutes.`
- `Delete all cancelled events this week.`
- `Move my study sessions to Monday.`
- `Find a 2-hour free slot tomorrow and schedule DSA practice.`

Before a single create or move, `detect_conflicts` loads that target day and
uses half-open interval overlap checks. A conflict blocks execution and returns
up to three available alternatives. Free-time scheduling merges overlapping
busy periods, calculates gaps inside the configured working day, selects the
earliest fitting slot, and asks for confirmation before creating anything.

The default free-time window is 08:00-21:00 in `TIMEZONE`; override it with
`WORKDAY_START_HOUR` and `WORKDAY_END_HOUR`.

## Week 5 application

The FastAPI backend exposes validated request and response contracts:

| Endpoint | Purpose |
|---|---|
| `POST /chat` | Send a natural-language request or `yes`/`no` follow-up |
| `GET /events` | Return upcoming structured calendar events |
| `GET /health` | Report service configuration without contacting external APIs |

`POST /chat` accepts:

```json
{
  "message": "Move all meetings tomorrow by 30 minutes",
  "thread_id": "optional-stable-conversation-id"
}
```

When `thread_id` is omitted, the backend generates one and returns it. The
Streamlit frontend retains that ID in session state so LangGraph confirmation
and clarification turns resume the correct checkpoint. API initialization is
lazy: `/health` never triggers OAuth, and `/events` does not require the LLM.

The Streamlit interface includes chat history, a timed loading spinner,
confirmation/cancel buttons, event and conflict displays, alternative slots,
an upcoming-events sidebar, backend status checking, and a new-conversation
control.

### Run the complete application

Install dependencies once:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Start the backend in terminal 1:

```powershell
python -m uvicorn app.api:app --host 127.0.0.1 --port 8000
```

Start the frontend in terminal 2:

```powershell
python -m streamlit run streamlit_app.py
```

Open `http://localhost:8501`. Interactive API documentation is available at
`http://127.0.0.1:8000/docs`.

## OpenRouter model configuration

The live model is constructed in `app/llm.py`. OpenRouter's OpenAI-compatible
endpoint is accessed through `langchain-openai`, while the calendar agent stays
provider-independent:

```powershell
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
| `WORKDAY_START_HOUR` | `8` | Earliest hour considered for free slots |
| `WORKDAY_END_HOUR` | `21` | Latest boundary considered for free slots |
| `OPENROUTER_API_KEY` | none | Secret API key; required for live LLM calls |
| `OPENROUTER_MODEL` | `qwen/qwen3-30b-a3b` | Switchable OpenRouter model slug |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter API endpoint |
| `API_HOST` | `127.0.0.1` | FastAPI bind host |
| `API_PORT` | `8000` | FastAPI bind port |
| `TASK_PILOT_API_URL` | `http://127.0.0.1:8000` | Backend URL used by Streamlit |

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
- The Week 3 graph was exercised end to end with Qwen and the real calendar:
  it created a disposable event, searched and selected it, paused an incomplete
  move for a follow-up time, moved it while preserving its duration, deleted
  it, and confirmed cleanup.
- Week 4 was exercised with Qwen and the real calendar: two disposable events
  were shown and bulk-shifted only after confirmation, then shown and
  bulk-deleted after a second confirmation. A free slot was found and created
  only after approval. A deliberately conflicting 6 PM request was blocked and
  alternatives were returned. All disposable events were removed afterward.
- Week 5 backend tests validate all three endpoints, request rejection, response
  filtering, error translation, thread IDs, and lazy service isolation. The
  real `/events` and `/chat` routes succeeded against Google Calendar and Qwen.
  Uvicorn and Streamlit were each started as real local servers and returned
  HTTP 200. Streamlit's simulated UI test also renders with no backend call or
  application exception.
