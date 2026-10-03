import { describe, it, expect } from "vitest";
import { clock, dateKey, shiftDay, range, safeLink, dayLabel, monthGrid, monthLabel, monthStart, shiftMonth } from "./api";
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

describe("month grid", () => {
  it("starts a month on the first", () => {
    expect(monthStart("2026-10-17")).toBe("2026-10-01");
  });

  it("always returns a full six-week grid", () => {
    for (const day of ["2026-01-15", "2026-02-10", "2026-10-03", "2027-03-01"]) {
      expect(monthGrid(day)).toHaveLength(42);
    }
  });

  it("begins the grid on a Monday", () => {
    const first = monthGrid("2026-10-03")[0];
    expect(new Date(`${first}T12:00:00+05:30`).getUTCDay()).toBe(1);
  });

  it("contains every day of the month", () => {
    const grid = monthGrid("2026-02-10");
    expect(grid).toContain("2026-02-01");
    expect(grid).toContain("2026-02-28");
  });

  it("covers a leap day", () => {
    expect(monthGrid("2028-02-10")).toContain("2028-02-29");
  });

  it("is a contiguous run of days", () => {
    const grid = monthGrid("2026-10-03");
    for (let i = 1; i < grid.length; i += 1) {
      expect(grid[i]).toBe(shiftDay(grid[i - 1], 1));
    }
  });

  it("steps months without drifting", () => {
    expect(shiftMonth("2026-10-03", 1)).toBe("2026-11-01");
    expect(shiftMonth("2026-01-31", 1)).toBe("2026-02-01");
    expect(shiftMonth("2026-01-15", -1)).toBe("2025-12-01");
    expect(shiftMonth("2026-12-10", 1)).toBe("2027-01-01");
  });

  it("labels the month", () => {
    expect(monthLabel("2026-10-03")).toContain("October");
    expect(monthLabel("2026-10-03")).toContain("2026");
  });
});
