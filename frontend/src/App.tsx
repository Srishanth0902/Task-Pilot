import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUp,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Clock3,
  ExternalLink,
  MapPin,
  MessageSquare,
  Plus,
  RefreshCw,
  Settings2,
  X,
  Check,
  ArrowRight,
  LogOut,
  UserRound,
} from "lucide-react";
import {
  request,
  dateKey,
  shiftDay,
  clock,
  dayLabel,
  range,
  safeLink,
  type CalendarEvent,
  type ChatResponse,
  type Health,
  type Slot,
} from "./api";

type Message = {
  role: "user" | "assistant";
  text: string;
  payload?: ChatResponse;
};
type UserAccount = {id:string;name:string;email:string;picture?:string|null};
export default function App() {
  const client = useQueryClient();
  const auth = useQuery({queryKey: ['auth'], queryFn: () => request<{authenticated:boolean; login_configured:boolean; user:UserAccount|null}>('/auth/me'), retry:false});
  if (!auth.data?.authenticated || !auth.data.user) return <main style={{maxWidth:520,margin:'12vh auto',padding:32}}>
    <CalendarDays size={32}/><h1>Task Pilot</h1><p>Your calendar, with a little more room for what matters.</p>
    {auth.isPending ? <p>Loading your account…</p> : auth.isError ? <p>Could not connect. <button onClick={()=>auth.refetch()}>Try again</button></p> : <>
      <p>Sign in to manage your Google Calendar and continue your saved conversations.</p>
      {auth.data?.login_configured ? <a href="/api/auth/login">Choose a Google account</a> : <p>Google sign-in is being configured. Please check back shortly.</p>}
    </>}
  </main>;
  const endSession = async (next: string) => {
    await request('/auth/logout',{method:'POST'});
    client.clear();
    window.location.assign(next);
  };
  return <AuthenticatedApp key={auth.data.user.id} user={auth.data.user}
    logout={()=>endSession('/')}
    switchAccount={()=>endSession('/api/auth/login')}/>;
}
function AuthenticatedApp({user,logout,switchAccount}:{user:UserAccount;logout:()=>Promise<void>;switchAccount:()=>Promise<void>}) {
  const client = useQueryClient();
  const [day, setDay] = useState(dateKey());
  const [view, setView] = useState<"agenda" | "week">("agenda");
  const [page, setPage] = useState("schedule");
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [thread, setThread] = useState<string>(() => crypto.randomUUID());
  const profileMenu = useRef<HTMLDivElement>(null);
  const profilePicture = safeLink(user.picture || undefined);
  const profileInitial = (user.name.trim()[0] || user.email[0] || 'U').toUpperCase();
  const conversations = useQuery({queryKey:['conversations',user.id],queryFn:()=>request<{id:string;title:string;updated:number}[]>('/conversations')});
  async function openConversation(id:string) {
    if(busy) return;
    setBusy(true); setError('');
    try {
      const saved = await request<{messages:Message[];latest:ChatResponse|null;interrupted:boolean}>(`/conversations/${encodeURIComponent(id)}`);
      const restored = [...saved.messages];
      if(restored.length && saved.latest) restored[restored.length-1] = {...restored[restored.length-1],payload:saved.latest};
      setThread(id); setMessages(restored); setPending(saved.latest?.requires_confirmation || false);
      if(saved.interrupted) setError('This conversation was interrupted. Check your calendar and start a new chat before making changes.');
    } catch(e) {setError(e instanceof Error ? e.message : 'Unable to load conversation.');}
    finally {setBusy(false);}
  }
  const [eventForm, setEventForm] = useState<{
    date: string;
    time: string;
    title: string;
    duration: number;
  } | null>(null);
  const [detail, setDetail] = useState<CalendarEvent | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const sending = useRef(false);
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => request<Health>("/health"),
  });
  const events = useQuery({
    queryKey: ["events", user.id, day, view],
    queryFn: () =>
      request<{ success: boolean; events: CalendarEvent[]; count: number }>(
        `/events?max_results=250&time_min=${encodeURIComponent(day + "T00:00:00+05:30")}&time_max=${encodeURIComponent(shiftDay(day, view === "week" ? 7 : 1) + "T00:00:00+05:30")}`,
      ),
  });
  const rows = (events.data?.events || [])
    .slice()
    .sort((a, b) => a.start.localeCompare(b.start));
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);
  useEffect(() => {
    if (eventForm || detail) dialog.current?.showModal();
    else dialog.current?.close();
  }, [eventForm, detail]);
  useEffect(() => {
    if (!profileOpen) return;
    const close = (event: MouseEvent) => {
      if (!profileMenu.current?.contains(event.target as Node)) setProfileOpen(false);
    };
    const escape = (event: KeyboardEvent) => event.key === 'Escape' && setProfileOpen(false);
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', escape);
    };
  }, [profileOpen]);
  async function send(text: string) {
    if (!text.trim() || sending.current || busy) return;
    sending.current = true;
    setBusy(true);
    setError("");
    setDraft("");
    setMessages((prev) => [...prev, { role: "user", text }]);
    try {
      const result = await request<ChatResponse>("/chat", {
        method: "POST",
        body: JSON.stringify({ message: text, thread_id: thread }),
      });
      setMessages((prev) => [
        ...prev,
        { role: "assistant", text: result.response, payload: result },
      ]);
      setPending(result.requires_confirmation);
      if (!result.success) {
        setError(result.response);
        setDraft(text);
      }
      await client.invalidateQueries({ queryKey: ["events"] });
      await client.invalidateQueries({ queryKey: ["conversations",user.id] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to send your request.");
      setDraft(text);
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }
  function selectSlot(slot: Slot, payload: ChatResponse) {
    if (payload.intent === "free_slot") {
      const local = new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Kolkata",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(new Date(slot.start));
      setEventForm({
        date: dateKey(new Date(slot.start)),
        time: local,
        title: "",
        duration: Math.round(
          (+new Date(slot.end) - +new Date(slot.start)) / 60000,
        ),
      });
    } else {
      setDraft(`Use ${clock(slot.start)}`);
      composer.current?.focus();
    }
  }
  function newChat() {
    if (busy) return;
    setMessages([]);
    setPending(false);
    setError("");
    setDraft("");
    setThread(crypto.randomUUID());
  }
  const heading =
    day === dateKey()
      ? "Today"
      : day === shiftDay(dateKey(), 1)
        ? "Tomorrow"
        : null;
  const totalMinutes = rows.reduce(
    (sum, e) =>
      sum +
      (e.start.includes("T")
        ? Math.max(0, (+new Date(e.end) - +new Date(e.start)) / 60000)
        : 0),
    0,
  );
  const closeDialog = () => {
    setEventForm(null);
    setDetail(null);
  };
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setPage("schedule");
          }}
        >
          <span className="brand-mark">
            <CalendarDays size={19} />
          </span>
          <span>
            Task Pilot<small>CALENDAR ASSISTANT</small>
          </span>
        </a>
        <nav className="sidebar-navigation" aria-label="Main navigation">
          {[
            ["schedule", "Schedule", CalendarDays],
            ["assistant", "Conversations", MessageSquare],
          ].map(([id, label, Icon]) => (
            <button
              key={String(id)}
              className={page === id ? "active" : ""}
              onClick={() => setPage(String(id))}
            >
              <Icon size={16} />
              <span>{String(label)}</span>
            </button>
          ))}
        </nav>
        <section className="conversation-section" aria-label="Saved conversations">
          <button className="new-conversation" disabled={busy} onClick={()=>{newChat();setPage('assistant');}}>
            <Plus size={14}/> New conversation
          </button>
          <div className="conversation-list">
            {conversations.isPending ? <small>Loading conversations…</small> : conversations.data?.length ? conversations.data.slice(0,8).map(c=><button
              key={c.id}
              className={c.id===thread?'active':''}
              disabled={busy}
              onClick={()=>{setPage('assistant');void openConversation(c.id);}}
              title={c.title}
            >
              <MessageSquare size={13}/>
              <span><strong>{c.title}</strong><small>{new Date(c.updated*1000).toLocaleDateString('en-IN',{timeZone:'Asia/Kolkata',day:'numeric',month:'short'})}</small></span>
            </button>) : <small>No saved conversations yet.</small>}
          </div>
        </section>
        <div className="sidebar-bottom">
          <button className={`settings-nav ${page==='settings'?'active':''}`} onClick={()=>setPage('settings')}>
            <Settings2 size={16}/><span>Settings</span>
          </button>
          <div className="connection">
            <span className={`status-dot ${events.isSuccess ? "connected" : ""}`}/>
            <span>
              {events.isSuccess ? "Calendar connected" : events.isError ? "Calendar unavailable" : "Connecting to calendar"}
              <small>Google Calendar · IST</small>
            </span>
          </div>
        </div>
      </aside>
      <header className="topbar">
        <span className="eyebrow">WORKSPACE</span>
        <span className="slash">/</span>
        <span>Your daily ledger</span>
        <span className="timezone">IST · India Standard Time</span>
        <div className="profile-menu" ref={profileMenu}>
          <button className="profile-button" aria-label={`Google account: ${user.email}`} aria-expanded={profileOpen} onClick={()=>setProfileOpen(open=>!open)}>
            {profilePicture ? <img src={profilePicture} alt="" referrerPolicy="no-referrer"/> : <span>{profileInitial}</span>}
          </button>
          {profileOpen && <div className="profile-popover" role="menu">
            <div className="profile-identity">
              {profilePicture ? <img src={profilePicture} alt="" referrerPolicy="no-referrer"/> : <span>{profileInitial}</span>}
              <div><strong>{user.name}</strong><small>{user.email}</small></div>
            </div>
            <button role="menuitem" onClick={()=>{setProfileOpen(false);switchAccount().catch(()=>setError('Unable to switch accounts. Please try again.'));}}>
              <UserRound size={15}/> Switch account
            </button>
            <button role="menuitem" onClick={()=>{setProfileOpen(false);logout().catch(()=>setError('Unable to sign out. Please try again.'));}}>
              <LogOut size={15}/> Sign out
            </button>
          </div>}
        </div>
      </header>
      <main
        className={`workspace ${page === "assistant" ? "assistant-view" : ""} ${page === "settings" ? "settings-view" : ""}`}
      >
        <section className="schedule-pane" aria-label="Schedule">
          {page === "settings" ? (
            <div className="settings">
              <span className="eyebrow">PREFERENCES</span>
              <h1>A little housekeeping.</h1>
              <p>Your calendar, on your terms.</p>
              <div className="setting-row">
                <span>Display timezone</span>
                <strong>India Standard Time (IST)</strong>
              </div>
              <div className="setting-row">
                <span>Calendar</span>
                <strong>
                  {events.isSuccess
                    ? "Google Calendar connected"
                    : "Connection unavailable"}
                </strong>
              </div>
              <div className="setting-row">
                <span>Google account</span>
                <strong>{user.email}</strong>
              </div>
              <div className="setting-row">
                <span>Assistant</span>
                <strong>
                  {health.isSuccess ? "Available" : "Unavailable"}
                </strong>
              </div>
              <p className="muted">
                All event times are displayed in IST. Calendar access is managed
                by your signed-in Google account. Active sessions stay signed in
                for up to 30 days and renew while you use Task Pilot.
              </p>
              <button className="outline" disabled={busy} onClick={newChat}>
                Start a new conversation
              </button>
              <small className="muted">
                This clears the current conversation and its pending proposal.
              </small>
            </div>
          ) : (
            <>
              <div className="toolbar">
                <div className="date-controls">
                  <button className="outline" onClick={() => setDay(dateKey())}>
                    Today
                  </button>
                  <button
                    className="icon-button"
                    aria-label="Previous day"
                    onClick={() => setDay(shiftDay(day, -1))}
                  >
                    <ChevronLeft size={16} />
                  </button>
                  <button
                    className="icon-button"
                    aria-label="Next day"
                    onClick={() => setDay(shiftDay(day, 1))}
                  >
                    <ChevronRight size={16} />
                  </button>
                  <label className="date-picker">
                    <CalendarDays size={15} />
                    <input
                      aria-label="Choose date"
                      type="date"
                      value={day}
                      onChange={(e) => e.target.value && setDay(e.target.value)}
                    />
                  </label>
                </div>
                <div className="view-controls">
                  <div className="segmented">
                    <button
                      aria-pressed={view === "agenda"}
                      className={view === "agenda" ? "selected" : ""}
                      onClick={() => setView("agenda")}
                    >
                      Agenda
                    </button>
                    <button
                      aria-pressed={view === "week"}
                      className={view === "week" ? "selected" : ""}
                      onClick={() => setView("week")}
                    >
                      Week
                    </button>
                  </div>
                  <button
                    className="primary"
                    disabled={busy || pending}
                    onClick={() =>
                      setEventForm({
                        date: day,
                        time: "18:00",
                        title: "",
                        duration: 60,
                      })
                    }
                  >
                    <Plus size={15} />
                    Add event
                  </button>
                </div>
              </div>
              <div className="date-heading">
                <div className="heading-kicker">
                  <span className="tag">SCHEDULE MANIFEST</span>
                  <span>
                    {new Intl.DateTimeFormat("en-GB", {
                      month: "long",
                      year: "numeric",
                      timeZone: "Asia/Kolkata",
                    }).format(new Date(day + "T12:00:00+05:30"))}
                  </span>
                </div>
                <h1>
                  {heading && (
                    <>
                      {heading}
                      <span className="heading-dot"> · </span>
                    </>
                  )}
                  {dayLabel(day)}
                </h1>
                <p>
                  {view === "week"
                    ? "Seven days, starting here."
                    : "A little structure. A little breathing room."}{" "}
                  <span>All times in IST.</span>
                </p>
              </div>
              <div className="ledger">
                <div className="ledger-head">
                  <span>TIME</span>
                  <span>SCHEDULED PLANS</span>
                  <span>
                    {rows.length} {rows.length === 1 ? "event" : "events"}
                    {totalMinutes > 0
                      ? ` · ${+(totalMinutes / 60).toFixed(1)} hrs`
                      : ""}
                    <button
                      className="icon-button"
                      aria-label="Refresh calendar"
                      onClick={() => events.refetch()}
                      disabled={events.isFetching}
                    >
                      <RefreshCw
                        size={12}
                        className={events.isFetching ? "spin" : ""}
                      />
                    </button>
                  </span>
                </div>
                {events.isPending ? (
                  <div className="empty">
                    <div className="loading-line" />
                    <p>Opening your calendar…</p>
                  </div>
                ) : events.isError ? (
                  <div className="empty error" role="alert">
                    <p>Your calendar couldn’t be loaded.</p>
                    <button
                      className="outline"
                      onClick={() => events.refetch()}
                    >
                      Try again
                    </button>
                  </div>
                ) : rows.length === 0 ? (
                  <div className="empty">
                    <CalendarDays size={30} strokeWidth={1} />
                    <h2>A little room in your day.</h2>
                    <p>
                      No events{" "}
                      {view === "week" ? "in these seven days" : "on this date"}
                      . Ask your assistant to plan something.
                    </p>
                    <button
                      className="outline"
                      onClick={() => {
                        setDraft(`Find available slots on ${day}`);
                        setPage("assistant");
                        composer.current?.focus();
                      }}
                    >
                      Find a free slot <ArrowRight size={14} />
                    </button>
                  </div>
                ) : (
                  rows.map((event, index) => {
                    const previous = rows[index - 1];
                    const gap =
                      previous &&
                      previous.end.includes("T") &&
                      event.start.includes("T")
                        ? Math.round(
                            (+new Date(event.start) - +new Date(previous.end)) /
                              60000,
                          )
                        : 0;
                    return (
                      <div key={event.event_id || index}>
                        {view === "week" &&
                          (!previous ||
                            dateKey(new Date(previous.start)) !==
                              dateKey(new Date(event.start))) && (
                            <h3 className="day-divider">
                              {dayLabel(event.start)}
                            </h3>
                          )}
                        {gap >= 30 && gap < 600 && (
                          <div className="break">
                            <span>
                              {gap >= 60
                                ? `${+(gap / 60).toFixed(1)} hr`
                                : `${gap} min`}{" "}
                              break
                            </span>
                            <div />
                          </div>
                        )}
                        <button
                          className="event-row"
                          onClick={() => setDetail(event)}
                        >
                          <div className="event-time">
                            <strong>
                              {event.start.includes("T")
                                ? clock(event.start)
                                : "ALL DAY"}
                            </strong>
                            <span>
                              {event.end.includes("T") ? clock(event.end) : ""}
                            </span>
                            {event.start.includes("T") && (
                              <small>
                                {Math.round(
                                  (+new Date(event.end) -
                                    +new Date(event.start)) /
                                    60000,
                                )}{" "}
                                min
                              </small>
                            )}
                          </div>
                          <div className="event-content">
                            <h2>{event.title}</h2>
                            {event.description && (
                              <p>{event.description.replace(/<[^>]*>/g, "")}</p>
                            )}
                            {event.location && (
                              <div className="event-meta">
                                <MapPin size={12} />
                                {event.location}
                              </div>
                            )}
                          </div>
                          <ChevronRight className="event-chevron" size={15} />
                        </button>
                      </div>
                    );
                  })
                )}
                <footer className="ledger-footer">
                  <span className="status-dot connected" />
                  <span>Make space for what comes next.</span>
                  <span>YOUR TIME, WELL SPENT</span>
                </footer>
              </div>
            </>
          )}
        </section>
        <section className="assistant-pane" aria-label="Scheduling assistant">
          <div className="assistant-header">
            <span className="assistant-mark">
              <MessageSquare size={18} />
            </span>
            <div>
              <h2>Scheduling Assistant</h2>
              <p>
                <span
                  className={`status-dot ${health.isSuccess ? "connected" : ""}`}
                />
                {busy
                  ? "Working on your request"
                  : health.isSuccess
                    ? "Ready when you are"
                    : "Waiting for connection"}
              </p>
            </div>
            <button
              className="icon-button"
              title="New conversation"
              aria-label="New conversation"
              disabled={busy}
              onClick={newChat}
            >
              <RefreshCw size={15} />
            </button>
          </div>
          <div className="conversation">
            <div className="conversation-date">{dayLabel(dateKey())}</div>
            {messages.length === 0 && (
              <div className="chat-welcome">
                <span className="eyebrow">A CLEARER DAY STARTS HERE</span>
                <h2>What’s on your mind?</h2>
                <p>
                  A meeting to move, an hour to find.
                  <br />
                  Tell me what you need to make room for.
                </p>
                <button
                  onClick={() =>
                    setDraft("What slots are available tomorrow after 6 PM?")
                  }
                >
                  Find some breathing room <ArrowRight size={14} />
                </button>
              </div>
            )}
            {messages.map((message, index) => (
              <div className={`message ${message.role}`} key={index}>
                {message.role === "assistant" && (
                  <span className="eyebrow message-label">TASK PILOT</span>
                )}
                <p>{message.text}</p>
                {message.payload?.alternatives?.length ? (
                  <div className="slots">
                    {message.payload.alternatives.map((slot, i) => (
                      <div className="slot" key={i}>
                        <span>
                          <strong>{range(slot.start, slot.end)}</strong>
                          <small>{dayLabel(slot.start)}</small>
                        </span>
                        {index === messages.length - 1 && !pending && (
                          <button
                            disabled={busy}
                            onClick={() => selectSlot(slot, message.payload!)}
                          >
                            Select slot
                          </button>
                        )}
                      </div>
                    ))}
                    <small className="slot-note">
                      Selecting a slot prepares your next action for review.
                    </small>
                  </div>
                ) : null}
                {message.payload?.proposed_changes?.length ? (
                  <div className="changes">
                    <span className="eyebrow">
                      PROPOSED CHANGES ·{" "}
                      {message.payload.proposed_changes.length}
                    </span>
                    {message.payload.proposed_changes.map((change, i) => (
                      <div key={i}>
                        <strong>{change.title || "Calendar event"}</strong>
                        <small>
                          {change.old_start && (
                            <>
                              {dayLabel(change.old_start)} ·{" "}
                              {range(change.old_start, change.old_end)}
                              <br />
                            </>
                          )}
                          {change.new_start ? (
                            <>
                              → {dayLabel(change.new_start)} ·{" "}
                              {range(change.new_start, change.new_end)}
                            </>
                          ) : change.action === "delete" ? (
                            "To be deleted"
                          ) : (
                            ""
                          )}
                        </small>
                      </div>
                    ))}
                  </div>
                ) : null}
                {safeLink(message.payload?.tool_result?.html_link) && (
                  <a
                    className="event-link"
                    href={safeLink(message.payload?.tool_result?.html_link)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open in Google Calendar <ExternalLink size={12} />
                  </a>
                )}
              </div>
            ))}
            {busy && (
              <div className="thinking" role="status">
                <span className="pulse" />
                Checking your calendar…
              </div>
            )}
            <div ref={bottom} />
          </div>
          <div className="composer-area">
            {error && (
              <div className="send-error" role="alert">
                {error}
                <small>
                  Your request is preserved below. Check the result before
                  retrying a change.
                </small>
              </div>
            )}
            {pending && (
              <div className="confirmation">
                <strong>Review before making changes</strong>
                <p>Nothing changes until you confirm.</p>
                <div>
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() => send("yes")}
                  >
                    <Check size={14} />
                    Confirm
                  </button>
                  <button
                    className="outline"
                    disabled={busy}
                    onClick={() => send("no")}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
            {!pending && (
              <div className="suggestions">
                {["Move my study session", "Find time tomorrow"].map((text) => (
                  <button
                    disabled={busy}
                    key={text}
                    onClick={() => {
                      setDraft(text);
                      composer.current?.focus();
                    }}
                  >
                    {text}
                  </button>
                ))}
              </div>
            )}
            <form
              className="composer"
              onSubmit={(e) => {
                e.preventDefault();
                send(draft);
              }}
            >
              <textarea
                ref={composer}
                aria-label="Message your assistant"
                placeholder={
                  pending
                    ? "Confirm or cancel the proposal above"
                    : "Ask about your schedule…"
                }
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                disabled={busy || pending}
                maxLength={4000}
                rows={2}
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !e.nativeEvent.isComposing
                  ) {
                    e.preventDefault();
                    if (!pending) send(draft);
                  }
                }}
              />
              <button
                type="submit"
                aria-label="Send message"
                disabled={!draft.trim() || busy || pending}
              >
                <ArrowUp size={18} />
              </button>
            </form>
            <div className="composer-footer">
              <span>All times in IST</span>
              <span>Enter to send · Shift + Enter for a new line</span>
            </div>
          </div>
        </section>
      </main>
      <dialog
        ref={dialog}
        onCancel={closeDialog}
        onClick={(e) => {
          if (e.target === dialog.current) closeDialog();
        }}
      >
        <button
          className="dialog-close icon-button"
          aria-label="Close dialog"
          onClick={closeDialog}
        >
          <X size={20} />
        </button>
        {eventForm && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const text = `Create "${eventForm.title}" on ${eventForm.date} at ${eventForm.time} IST for ${eventForm.duration} minutes`;
              closeDialog();
              setPage("assistant");
              send(text);
            }}
          >
            <span className="eyebrow">MAKE A LITTLE SPACE</span>
            <h2>Add an event</h2>
            <p>The assistant will check for conflicts before creating it.</p>
            <label>
              Event title
              <input
                autoFocus
                required
                maxLength={200}
                value={eventForm.title}
                onChange={(e) =>
                  setEventForm({ ...eventForm, title: e.target.value })
                }
                placeholder="What are you planning?"
              />
            </label>
            <div className="form-grid">
              <label>
                Date
                <input
                  type="date"
                  required
                  value={eventForm.date}
                  onChange={(e) =>
                    setEventForm({ ...eventForm, date: e.target.value })
                  }
                />
              </label>
              <label>
                Time · IST
                <input
                  type="time"
                  required
                  value={eventForm.time}
                  onChange={(e) =>
                    setEventForm({ ...eventForm, time: e.target.value })
                  }
                />
              </label>
            </div>
            <label>
              Duration in minutes
              <input
                type="number"
                min={1}
                max={1440}
                required
                value={eventForm.duration}
                onChange={(e) =>
                  setEventForm({
                    ...eventForm,
                    duration: Number(e.target.value),
                  })
                }
              />
            </label>
            <div className="dialog-actions">
              <button type="button" className="outline" onClick={closeDialog}>
                Cancel
              </button>
              <button
                className="primary"
                disabled={busy || pending || !eventForm.title.trim()}
              >
                Create event <ArrowRight size={15} />
              </button>
            </div>
          </form>
        )}
        {detail && (
          <div>
            <span className="eyebrow">YOUR CALENDAR</span>
            <h2>{detail.title}</h2>
            <p>{dayLabel(detail.start)}</p>
            <p>
              <Clock3 size={15} /> {range(detail.start, detail.end)}
            </p>
            {detail.description && (
              <p className="detail-description">
                {detail.description.replace(/<[^>]*>/g, "")}
              </p>
            )}
            {detail.location && (
              <p>
                <MapPin size={15} /> {detail.location}
              </p>
            )}
            <div className="dialog-actions">
              {safeLink(detail.html_link) && (
                <a
                  className="outline"
                  href={safeLink(detail.html_link)}
                  target="_blank"
                  rel="noreferrer"
                >
                  Google Calendar <ExternalLink size={14} />
                </a>
              )}
              <button
                className="primary"
                disabled={busy || pending}
                onClick={() => {
                  setDraft(
                    `Move "${detail.title}" on ${dateKey(new Date(detail.start))} to `,
                  );
                  closeDialog();
                  setPage("assistant");
                  composer.current?.focus();
                }}
              >
                Reschedule
              </button>
            </div>
          </div>
        )}
      </dialog>
    </div>
  );
}
