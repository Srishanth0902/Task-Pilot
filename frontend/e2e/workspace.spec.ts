import { test, expect } from "@playwright/test";
test.beforeEach(async ({ page }) => {
  await page.route("**/api/health", (route) =>
    route.fulfill({
      json: {
        status: "ok",
        timezone: "Asia/Kolkata",
        google_token_configured: true,
      },
    }),
  );
  await page.route("**/api/events?*", (route) =>
    route.fulfill({
      json: {
        success: true,
        count: 2,
        events: [
          {
            event_id: "yoga",
            title: "Yoga",
            start: "2026-09-13T01:30:00Z",
            end: "2026-09-13T02:30:00Z",
            description: "Morning physical practice & breathwork",
            location: "Home studio",
          },
          {
            event_id: "meeting",
            title: "Project meeting",
            start: "2026-09-13T10:00:00+05:30",
            end: "2026-09-13T11:00:00+05:30",
            description: "Sprint sync & planning",
            location: "Google Meet",
          },
        ],
      },
    }),
  );
});
test("renders reference layout and readable IST times", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Yoga", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("7:00 AM", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Next day", exact: true }).click();
  await page.getByRole("button", { name: "Week", exact: true }).click();
  await expect(page.getByText("Seven days, starting here.")).toBeVisible();
  await page.getByRole("button", { name: "Agenda", exact: true }).click();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({
    path: "test-results/workspace-desktop.png",
    fullPage: true,
  });
});
test("confirmation executes once and keeps thread context", async ({
  page,
}) => {
  const calls: any[] = [];
  await page.route("**/api/chat", async (route) => {
    const body = route.request().postDataJSON();
    calls.push(body);
    await route.fulfill({
      json: {
        success: true,
        thread_id: body.thread_id,
        response: calls.length === 1 ? "Delete Yoga?" : "Deleted Yoga.",
        requires_confirmation: calls.length === 1,
        alternatives: [],
        proposed_changes: [],
        events: [],
        conflicts: [],
      },
    });
  });
  await page.goto("/");
  await page.getByLabel("Message your assistant").fill("Delete Yoga");
  await page.getByLabel("Send message").click();
  await expect(
    page.getByRole("button", { name: "Confirm", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Message your assistant")).toBeDisabled();
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.getByText("Deleted Yoga.", { exact: true })).toBeVisible();
  expect(calls).toHaveLength(2);
  expect(calls[1].message).toBe("yes");
  expect(calls[1].thread_id).toBe(calls[0].thread_id);
  await expect(
    page.getByRole("button", { name: "Confirm", exact: true }),
  ).toHaveCount(0);
});
test("add-event form submits the reviewed IST date and duration", async ({page}) => {
  let submitted:any;
  await page.route('**/api/chat', async route => {
    submitted=route.request().postDataJSON();
    await route.fulfill({json:{success:true,thread_id:submitted.thread_id,response:'Event created.',requires_confirmation:false,events:[],conflicts:[],alternatives:[],proposed_changes:[]}});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Add event',exact:true}).click();
  await page.getByLabel('Event title').fill('Interview practice');
  await page.getByLabel('Date',{exact:true}).fill('2026-09-13');
  await page.getByLabel('Time · IST').fill('18:30');
  await page.getByLabel('Duration in minutes').fill('45');
  await page.getByRole('button',{name:'Create event',exact:true}).click();
  await expect(page.getByText('Event created.',{exact:true})).toBeVisible();
  expect(submitted.message).toBe('Create "Interview practice" on 2026-09-13 at 18:30 IST for 45 minutes');
});
test("slot selection opens review without sending a mutation", async ({
  page,
}) => {
  let calls = 0;
  await page.route("**/api/chat", async (route) => {
    calls++;
    await route.fulfill({
      json: {
        success: true,
        thread_id: "sample",
        response: "Available slots tomorrow.",
        intent: "free_slot",
        requires_confirmation: false,
        alternatives: [
          {
            start: "2026-09-13T18:00:00+05:30",
            end: "2026-09-13T19:00:00+05:30",
          },
        ],
        events: [],
        conflicts: [],
        proposed_changes: [],
      },
    });
  });
  await page.goto("/");
  await page.getByLabel("Message your assistant").fill("Find slots");
  await page.getByLabel("Send message").click();
  await page.getByRole("button", { name: "Select slot" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByLabel("Time · IST")).toHaveValue("18:00");
  expect(calls).toBe(1);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
});
test("failed requests preserve input and mobile layout stays within viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/chat", (route) =>
    route.fulfill({ status: 503, json: { detail: "Connection interrupted" } }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Assistant", exact: true }).click();
  await page.getByLabel("Message your assistant").fill("Move my Yoga");
  await page.getByLabel("Send message").click();
  await expect(page.getByRole("alert")).toContainText("Connection interrupted");
  await expect(page.getByLabel("Message your assistant")).toHaveValue(
    "Move my Yoga",
  );
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/workspace-mobile.png",
    fullPage: true,
  });
});
