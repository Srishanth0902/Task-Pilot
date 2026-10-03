export interface CalendarEvent {
  event_id: string;
  title: string;
  start: string;
  end: string;
  description?: string;
  location?: string;
  html_link?: string;
}
export interface Slot {
  start: string;
  end: string;
}
export interface Change {
  action: string;
  title?: string;
  old_start?: string;
  old_end?: string;
  new_start?: string;
  new_end?: string;
}
export interface ChatResponse {
  success: boolean;
  thread_id: string;
  response: string;
  intent?: string;
  requires_confirmation: boolean;
  can_undo?: boolean;
  paused_task?: string | null;
  task_context?: {intent?:string;title?:string;start_time?:string;duration_minutes?:number;awaiting_duration?:boolean};
  events: CalendarEvent[];
  conflicts: CalendarEvent[];
  alternatives: Slot[];
  proposed_changes: Change[];
  tool_result?: { event_id?: string; html_link?: string };
  error?: string;
}
export interface Health {
  status: string;
  timezone: string;
  google_token_configured: boolean;
}
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Task-Pilot": "1", ...options.headers },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok)
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : Array.isArray(body?.detail) && typeof body.detail[0]?.msg === 'string'
          ? body.detail[0].msg.replace(/^Value error, /, '')
          : "Could not reach your calendar. Please try again.",
    );
  return body as T;
}
export const zone = "Asia/Kolkata";
export function dateKey(date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}
export function shiftDay(day: string, amount: number): string {
  const date = new Date(`${day}T12:00:00+05:30`);
  date.setUTCDate(date.getUTCDate() + amount);
  return dateKey(date);
}
export function clock(value: string): string {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: zone,
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  })
    .format(new Date(value))
    .toUpperCase();
}
export function dayLabel(day: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: zone,
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date(day.includes("T") ? day : `${day}T12:00:00+05:30`));
}
export function range(start?: string, end?: string): string {
  if (!start) return "";
  if (!start.includes("T")) return "All day";
  const endDate = end && dateKey(new Date(start)) !== dateKey(new Date(end)) ? ` (${dayLabel(end)})` : "";
  return `${clock(start)}${end ? " to " + clock(end) + endDate : ""} IST`;
}
export function safeLink(url?: string): string | undefined {
  try {
    const u = new URL(url || "");
    return u.protocol === "https:" ? u.href : undefined;
  } catch {
    return undefined;
  }
}

// ---- month grid -----------------------------------------------------------
// All arithmetic goes through dateKey/shiftDay so the grid stays in the
// application timezone rather than the browser's.

export function monthStart(day: string): string {
  return `${day.slice(0, 7)}-01`;
}

export function shiftMonth(day: string, amount: number): string {
  const [year, month] = day.slice(0, 7).split("-").map(Number);
  const moved = new Date(Date.UTC(year, month - 1 + amount, 1, 12));
  return `${moved.getUTCFullYear()}-${String(moved.getUTCMonth() + 1).padStart(2, "0")}-01`;
}

/** The Monday-first 6x7 grid covering a month, as date keys. */
export function monthGrid(day: string): string[] {
  const first = monthStart(day);
  const weekday = new Date(`${first}T12:00:00+05:30`).getUTCDay();
  const lead = (weekday + 6) % 7; // Monday = 0
  const start = shiftDay(first, -lead);
  return Array.from({ length: 42 }, (_, index) => shiftDay(start, index));
}

export function monthLabel(day: string): string {
  return new Date(`${monthStart(day)}T12:00:00+05:30`).toLocaleDateString("en-IN", {
    timeZone: zone,
    month: "long",
    year: "numeric",
  });
}

export function download(path: string, filename: string) {
  // Same-origin link click: the cookie goes with it and the browser saves the
  // file, so no token ever reaches JavaScript.
  const link = document.createElement("a");
  link.href = `/api${path}`;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}
