import { describe, it, expect } from "vitest";
import { clock, dateKey, shiftDay, range, safeLink, dayLabel } from "./api";
describe("IST display contract", () => {
  it("converts UTC into IST and crosses midnight correctly", () => {
    expect(clock("2026-09-13T01:30:00Z")).toBe("7:00 AM");
    expect(dateKey(new Date("2026-09-12T20:00:00Z"))).toBe("2026-09-13");
    expect(dayLabel("2026-09-12T20:00:00Z")).toBe("Sunday, 13 September 2026");
  });
  it("navigates month and year boundaries", () => {
    expect(shiftDay("2026-12-31", 1)).toBe("2027-01-01");
    expect(shiftDay("2028-03-01", -1)).toBe("2028-02-29");
  });
  it("does not turn all-day events into midnight appointments", () => {
    expect(range("2026-09-13", "2026-09-14")).toBe("All day");
  });
  it("rejects unsafe event links", () => {
    expect(safeLink("javascript:alert(1)")).toBeUndefined();
    expect(safeLink("https://calendar.google.com/")).toBeTruthy();
  });
});
