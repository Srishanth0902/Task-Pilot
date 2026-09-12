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
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok)
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
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
  return `${clock(start)}${end ? " – " + clock(end) + endDate : ""} IST`;
}
export function safeLink(url?: string): string | undefined {
  try {
    const u = new URL(url || "");
    return u.protocol === "https:" ? u.href : undefined;
  } catch {
    return undefined;
  }
}
