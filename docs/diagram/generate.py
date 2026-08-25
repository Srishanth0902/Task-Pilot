# -*- coding: utf-8 -*-
"""Emit the Task-Pilot architecture diagram as a standalone SVG.

The diagram is generated rather than hand-drawn so the grid stays exact and
the labels stay in step with docs/ARCHITECTURE.md. To regenerate both files:

    python docs/diagram/generate.py     # -> docs/architecture-diagram.svg
    node   docs/diagram/render.js       # -> docs/architecture-diagram.png (2x)

render.js needs Playwright's Chromium; no other dependencies.
"""

from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "architecture-diagram.svg"

W, H = 1900, 1160

INK   = "#1A201E"; MUTED = "#5C6562"; FAINT = "#8C948F"
LINE  = "#CDD2CC"; BG    = "#F4F5F2"; CARD  = "#FFFFFF"
BAND  = "#EAECE8"
PINE  = "#1F6F5C"; PINE_S= "#E2EDE8"
CLAY  = "#A8412F"; CLAY_S= "#F7E7E3"

SANS = "Archivo,'Liberation Sans',Arial,Helvetica,sans-serif"
MONO = "'IBM Plex Mono','DejaVu Sans Mono',monospace"

o = []
def add(s): o.append(s)

def esc(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def txt(x, y, s, size=12, fill=INK, weight="400", anchor="start",
        family=SANS, ls=None, op=None):
    a = f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" ' \
        f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}"'
    if ls is not None: a += f' letter-spacing="{ls}"'
    if op is not None: a += f' opacity="{op}"'
    add(a + f'>{esc(s)}</text>')

def rect(x, y, w, h, fill=CARD, stroke=LINE, sw=1, r=6, dash=None, op=None):
    a = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" ' \
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"'
    if dash: a += f' stroke-dasharray="{dash}"'
    if op is not None: a += f' opacity="{op}"'
    add(a + '/>')

def arrow(x1, y1, x2, y2, color=MUTED, sw=1.6, marker="a-mut", dash=None):
    a = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" ' \
        f'stroke-width="{sw}" marker-end="url(#{marker})"'
    if dash: a += f' stroke-dasharray="{dash}"'
    add(a + '/>')

# ---------------------------------------------------------------- canvas
add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
    f'width="{W}" height="{H}" role="img" '
    f'aria-label="Task-Pilot architecture: five layers from interface down to domain. '
    f'Requests enter through Streamlit and FastAPI, run through a LangGraph node chain, '
    f'call read or plan tools, and every write passes a confirmation gate and the single '
    f'EventService.apply function before reaching the CalendarPort and its Google or fake adapter.">')
add('<defs>')
for mid, col in (("a-mut", MUTED), ("a-pine", PINE), ("a-clay", CLAY), ("a-faint", FAINT)):
    add(f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" '
        f'markerHeight="6.5" orient="auto-start-reverse">'
        f'<path d="M 0 1 L 9 5 L 0 9 z" fill="{col}"/></marker>')
add('</defs>')
add(f'<rect width="{W}" height="{H}" fill="{BG}"/>')

# ---------------------------------------------------------------- header
txt(56, 62, "Task-Pilot", 36, INK, "700")
txt(56, 90, "Agentic calendar assistant  ·  target architecture", 15, MUTED)

# legend
lx = 1245
def leg_line(x, y, col, marker, label):
    add(f'<line x1="{x}" y1="{y}" x2="{x+34}" y2="{y}" stroke="{col}" '
        f'stroke-width="2" marker-end="url(#{marker})"/>')
    txt(x + 44, y + 4, label, 11.5, MUTED)
leg_line(lx, 46, PINE, "a-pine", "read / plan path")
add(f'<line x1="{lx+210}" y1="46" x2="{lx+244}" y2="46" stroke="{CLAY}" '
    f'stroke-width="2" marker-end="url(#a-clay)"/>')
txt(lx + 254, 50, "write path — gated", 11.5, MUTED)
add(f'<path d="M {lx+6} 74 l 7 -7 l 7 7 l -7 7 z" fill="{PINE}"/>')
txt(lx + 30, 78, "node uses the LLM", 11.5, MUTED)
rect(lx + 210, 67, 22, 14, CARD, FAINT, 1, 3, dash="3 2")
txt(lx + 242, 78, "external system", 11.5, MUTED)

# ---------------------------------------------------------------- bands
BX, BW = 176, 1444
IX = BX + 22                     # inner left
IW = BW - 44                     # inner width
bands = [
    ("L5", "INTERFACE", 130, 114),
    ("L4", "AGENT",     264, 300),
    ("L3", "SERVICES",  584, 114),
    ("L2", "PROVIDERS", 718, 180),
    ("L1", "DOMAIN",    918,  92),
]
for i, (num, name, by, bh) in enumerate(bands):
    rect(BX, by, BW, bh, BAND if i % 2 == 0 else "#E4E7E2", "#DBDFDA", 1, 10)
    cy = by + bh / 2
    txt(165, cy - 2, name, 14.5, INK, "600", anchor="end")
    txt(165, cy + 15, num, 10, FAINT, "500", anchor="end", family=MONO, ls=1.2)

# one-way dependency arrow on the far left
add(f'<line x1="72" y1="146" x2="72" y2="994" stroke="{FAINT}" stroke-width="1.4" '
    f'marker-end="url(#a-faint)"/>')
add(f'<text x="56" y="570" font-family="{MONO}" font-size="10.5" fill="{FAINT}" '
    f'letter-spacing="1.6" text-anchor="middle" transform="rotate(-90 56 570)">'
    f'IMPORTS POINT DOWNWARD ONLY</text>')

def box(x, y, w, h, title, sub=None, fill=CARD, stroke=LINE, sw=1,
        tcol=INK, tsize=14, dash=None, llm=False, mono_sub=True):
    rect(x, y, w, h, fill, stroke, sw, 6, dash=dash)
    if sub:
        txt(x + w / 2, y + h / 2 - 3, title, tsize, tcol, "600", anchor="middle")
        txt(x + w / 2, y + h / 2 + 16, sub, 10.5, MUTED, anchor="middle",
            family=MONO if mono_sub else SANS)
    else:
        txt(x + w / 2, y + h / 2 + 5, title, tsize, tcol, "600", anchor="middle")
    if llm:
        add(f'<path d="M {x+w-16} {y+9} l 6 -6 l 6 6 l -6 6 z" fill="{PINE}"/>')

# ---------------------------------------------------------------- L5
y5 = 155
box(210, y5, 120, 64, "User", None, CARD, FAINT, 1, INK, 14, dash="4 3")
box(380, y5, 280, 64, "Streamlit UI", "chat · preview · approve", CARD, LINE)
box(710, y5, 420, 64, "FastAPI", "POST /chat · /chat/confirm · GET /events")
box(1180, y5, 418, 64, "deps.py — composition root",
    "settings · clock · adapter · services · graph", CARD, LINE, 1, INK, 13)
arrow(330, y5 + 32, 372, y5 + 32, MUTED)
arrow(660, y5 + 32, 702, y5 + 32, MUTED)

# FastAPI down into the agent layer
arrow(920, 219, 920, 296, PINE, 1.8, "a-pine")
txt(932, 244, "invoke(thread_id)", 10.5, PINE, family=MONO)

# ---------------------------------------------------------------- L4
txt(IX, 290, "LANGGRAPH", 10.5, FAINT, "500", family=MONO, ls=1.4)
rect(IX, 300, IW, 32, PINE_S, "#CBDED6", 1, 5)
txt(IX + 14, 321, "AgentState", 11.5, PINE, "600", family=MONO)
txt(IX + 100, 321,
    "now (injected)  ·  intent  ·  time_window  ·  candidates  ·  selected  ·  plan  ·  "
    "awaiting  ·  result  ·  trace_id", 11.5, MUTED, family=MONO)

nodes = [
    ("understand", "query", True,  False),
    ("resolve", "time", False, False),
    ("search", "calendar", False, False),
    ("resolve", "event", True,  False),
    ("plan", "mutation", False, False),
    ("confirm", "gate", False, True),
    ("execute", "action", False, True),
    ("verify", "result", False, False),
    ("generate", "response", True,  False),
]
nw, ng, ny, nh = 143, 14, 356, 68
nx = IX
for i, (l1, l2, llm, hot) in enumerate(nodes):
    fill = CLAY_S if hot else CARD
    stroke = CLAY if hot else LINE
    rect(nx, ny, nw, nh, fill, stroke, 1.6 if hot else 1, 6)
    c = CLAY if hot else INK
    txt(nx + nw / 2, ny + 29, l1, 12.5, c, "600", anchor="middle", family=MONO)
    txt(nx + nw / 2, ny + 46, l2, 12.5, c, "600", anchor="middle", family=MONO)
    if llm:
        add(f'<path d="M {nx+nw-15} {ny+9} l 5.5 -5.5 l 5.5 5.5 l -5.5 5.5 z" fill="{PINE}"/>')
    if i < len(nodes) - 1:
        nxt_hot = hot and nodes[i + 1][3]
        col = CLAY if nxt_hot else MUTED
        mk = "a-clay" if nxt_hot else "a-mut"
        arrow(nx + nw + 1, ny + nh / 2, nx + nw + ng - 2, ny + nh / 2, col, 1.5, mk)
    nx += nw + ng
txt(IX + 6 * (nw + ng) - ng / 2, ny + nh + 15, "approved", 10, CLAY, "600",
    anchor="middle", family=MONO)

# tools
ty, th = 452, 92
box_defs = [
    (IX, 560, "READ TOOLS", PINE,
     ["search_events · get_event_details", "check_availability · find_free_slots"], False),
    (IX + 574, 520, "PLAN TOOLS  →  MutationPlan", PINE,
     ["plan_create · plan_update", "plan_delete · plan_bulk_shift"], False),
    (IX + 1108, 292, "WRITE TOOL", CLAY, ["apply_plan", "the only mutator"], True),
]
for bx, bw, label, col, lines, hot in box_defs:
    rect(bx, ty, bw, th, CLAY_S if hot else CARD, CLAY if hot else LINE, 1.6 if hot else 1, 6)
    txt(bx + bw / 2, ty + 24, label, 10.5, col, "600", anchor="middle", family=MONO, ls=1.1)
    for j, ln in enumerate(lines):
        bold = "600" if hot and j == 0 else "400"
        size = 14 if hot and j == 0 else 11.5
        colr = CLAY if hot and j == 0 else MUTED
        txt(bx + bw / 2, ty + 50 + j * 20, ln, size, colr, bold, anchor="middle", family=MONO)

# tools -> services
for cx, hot in ((IX + 280, False), (IX + 834, False), (IX + 1254, True)):
    arrow(cx, ty + th, cx, 602, CLAY if hot else PINE, 1.8,
          "a-clay" if hot else "a-pine")
txt(755, ty + th + 34, "tools are thin adapters — the logic lives in services",
    10.5, FAINT, anchor="middle", family=MONO)

# ---------------------------------------------------------------- L3
sy, sh = 606, 70
svc = [
    (IX, 560, "AvailabilityService", "conflicts_for · free_slots · suggest_alternatives", False),
    (IX + 574, 520, "BulkService", "plan_shift · plan_move_to_day · plan_delete_matching", False),
    (IX + 1108, 292, "EventService", "search · plan_* · apply()", True),
]
for bx, bw, title, sub, hot in svc:
    box(bx, sy, bw, sh, title, sub, CARD, CLAY if hot else LINE, 1.6 if hot else 1,
        CLAY if hot else INK)
txt(IX + 1254, sy + sh + 16, "apply() — the only writer", 10, CLAY, "600",
    anchor="middle", family=MONO)

# services -> port
for cx, hot in ((IX + 280, False), (IX + 834, False), (IX + 1254, True)):
    arrow(cx, sy + sh + (22 if hot else 0), cx, 734, CLAY if hot else PINE, 1.8,
          "a-clay" if hot else "a-pine")

# ---------------------------------------------------------------- L2
py, ph = 738, 52
rect(IX, py, IW, ph, PINE_S, PINE, 1.8, 6)
txt(IX + 22, py + 32, "CalendarPort", 15, PINE, "700")
txt(IX + 170, py + 31,
    "list_events  ·  get_event  ·  insert_event  ·  patch_event(etag)  ·  "
    "delete_event(etag)  ·  calendar_name", 12, MUTED, family=MONO)

ay, ah = 812, 66
box(380, ay, 440, ah, "FakeCalendarAdapter", "in-memory · tests, evals, offline demo")
box(980, ay, 440, ah, "GoogleCalendarAdapter", "maps resource → CalendarEvent · If-Match")
arrow(600, py + ph, 600, ay - 4, PINE, 1.6, "a-pine")
arrow(1200, py + ph, 1200, ay - 4, PINE, 1.6, "a-pine")
txt(900, ay + 40, "one contract,", 10.5, FAINT, anchor="middle", family=MONO)
txt(900, ay + 55, "two implementations", 10.5, FAINT, anchor="middle", family=MONO)

# ---------------------------------------------------------------- L1
dy, dh = 938, 52
rect(IX, dy, IW, dh, CARD, LINE, 1, 6)
txt(IX + 22, dy + 22, "Domain", 14, INK, "700")
txt(IX + 22, dy + 40, "pure Python — no I/O, no LLM, no framework", 10.5, FAINT, family=MONO)
txt(IX + 360, dy + 32,
    "CalendarEvent  ·  TimeWindow  ·  EventPatch  ·  Operation  ·  MutationPlan  ·  "
    "Conflict  ·  Clock  ·  AgentError", 12.5, MUTED, family=MONO)

# ---------------------------------------------------------------- externals
EXX, EXW = 1650, 210
rect(EXX, 300, EXW, 88, CARD, FAINT, 1, 6, dash="4 3")
txt(EXX + EXW / 2, 332, "Checkpointer", 14, INK, "600", anchor="middle")
txt(EXX + EXW / 2, 353, "thread_id = session", 10.5, MUTED, anchor="middle", family=MONO)
txt(EXX + EXW / 2, 370, "pauses survive restarts", 10.5, MUTED, anchor="middle", family=MONO)
add(f'<line x1="{IX+IW+4}" y1="316" x2="{EXX-6}" y2="316" stroke="{MUTED}" '
    f'stroke-width="1.6" marker-end="url(#a-mut)" marker-start="url(#a-mut)"/>')

rect(EXX, 800, EXW, 88, CARD, FAINT, 1, 6, dash="4 3")
txt(EXX + EXW / 2, 833, "Google Calendar API", 13.5, INK, "600", anchor="middle")
txt(EXX + EXW / 2, 856, "the only network hop", 10.5, MUTED, anchor="middle", family=MONO)
arrow(1424, ay + ah / 2, EXX - 6, ay + ah / 2, MUTED, 1.8, "a-mut")

# ---------------------------------------------------------------- invariant
fy, fh = 1040, 76
rect(BX, fy, BW, fh, CLAY_S, "#E4C7BF", 1, 8)
add(f'<rect x="{BX}" y="{fy}" width="5" height="{fh}" fill="{CLAY}"/>')
txt(BX + 26, fy + 27, "THE INVARIANT", 10.5, CLAY, "700", family=MONO, ls=1.6)
txt(BX + 26, fy + 52,
    "Every calendar write passes the confirmation gate and exactly one function — "
    "EventService.apply(). Reads and plans never mutate, so a hallucinated tool call "
    "cannot destroy data.", 13.5, INK)

txt(W - 56, fy + 52, "docs/ARCHITECTURE.md", 11, FAINT, anchor="end", family=MONO)

add('</svg>')

OUT.write_text("\n".join(o), encoding="utf-8")
print(f"wrote {OUT}")
