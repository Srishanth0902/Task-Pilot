import {test, expect} from '@playwright/test';

const nativeStatus = {
  guest: true,
  google_connected: false,
  active_provider: 'native',
  active_provider_label: 'Task Pilot calendar',
  available_providers: [{name: 'native', label: 'Task Pilot calendar', ready: true}],
};

const bothStatus = {
  guest: false,
  google_connected: true,
  active_provider: 'google',
  active_provider_label: 'Google Calendar',
  available_providers: [
    {name: 'google', label: 'Google Calendar', ready: true},
    {name: 'native', label: 'Task Pilot calendar', ready: true},
  ],
};

const nativeEvent = {
  event_id: 'nat_1',
  title: 'Yoga',
  start: '2026-10-05T07:00:00+05:30',
  end: '2026-10-05T08:00:00+05:30',
  status: 'confirmed',
  // Native events carry no Google link.
  html_link: null,
  provider: 'native',
};

test('a visitor is offered the native calendar as well as Google', async ({page}) => {
  await page.route('**/api/auth/me', route =>
    route.fulfill({json: {authenticated: false, login_configured: true, user: null}}));
  await page.goto('/');

  await expect(page.getByRole('link', {name: 'Connect Google Calendar'})).toBeVisible();
  await expect(page.getByRole('button', {name: /native calendar/i})).toBeVisible();
  // The browser-bound caveat must be stated, not buried.
  await expect(page.getByText(/clearing cookies/i)).toBeVisible();
});

test('choosing the native calendar opens a workspace without Google', async ({page}) => {
  let authenticated = false;
  let guestCalls = 0;
  await page.route('**/api/auth/me', route => route.fulfill({
    json: authenticated
      ? {authenticated: true, login_configured: true,
         user: {id: 'guest_1', name: 'Guest', email: ''}, ...nativeStatus}
      : {authenticated: false, login_configured: true, user: null},
  }));
  await page.route('**/api/auth/guest', route => {
    guestCalls += 1;
    authenticated = true;
    return route.fulfill({json: {success: true, ...nativeStatus}});
  });
  await page.route('**/api/calendar/status', route => route.fulfill({json: nativeStatus}));
  await page.route('**/api/conversations', route => route.fulfill({json: []}));
  await page.route('**/api/health', route => route.fulfill({json: {status: 'ok', timezone: 'Asia/Kolkata'}}));
  await page.route('**/api/events?*', route =>
    route.fulfill({json: {success: true, count: 1, events: [nativeEvent], provider: 'native'}}));

  await page.goto('/');
  await page.getByRole('button', {name: /native calendar/i}).click();

  await expect(page.getByText('Calendar workspace')).toBeVisible();
  expect(guestCalls).toBe(1);
});

test('the active calendar is named and guest access is explained', async ({page}) => {
  await page.route('**/api/auth/me', route => route.fulfill({
    json: {authenticated: true, login_configured: true,
           user: {id: 'guest_1', name: 'Guest', email: ''}, ...nativeStatus}}));
  await page.route('**/api/calendar/status', route => route.fulfill({json: nativeStatus}));
  await page.route('**/api/conversations', route => route.fulfill({json: []}));
  await page.route('**/api/health', route => route.fulfill({json: {status: 'ok', timezone: 'Asia/Kolkata'}}));
  await page.route('**/api/events?*', route =>
    route.fulfill({json: {success: true, count: 1, events: [nativeEvent], provider: 'native'}}));

  await page.goto('/');
  await expect(page.getByText('Task Pilot calendar / IST')).toBeVisible();
  await expect(page.getByText(/Guest workspace/i)).toBeVisible();
  // One calendar available means nothing to switch between.
  await expect(page.locator('.calendar-switch')).toHaveCount(0);
});

test('native events never show a Google Calendar link', async ({page}) => {
  await page.route('**/api/auth/me', route => route.fulfill({
    json: {authenticated: true, login_configured: true,
           user: {id: 'guest_1', name: 'Guest', email: ''}, ...nativeStatus}}));
  await page.route('**/api/calendar/status', route => route.fulfill({json: nativeStatus}));
  await page.route('**/api/conversations', route => route.fulfill({json: []}));
  await page.route('**/api/health', route => route.fulfill({json: {status: 'ok', timezone: 'Asia/Kolkata'}}));
  await page.route('**/api/events?*', route =>
    route.fulfill({json: {success: true, count: 1, events: [nativeEvent], provider: 'native'}}));

  await page.goto('/');
  await page.getByRole('button', {name: 'Next day', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Yoga', exact: true})).toBeVisible();
  await expect(page.getByRole('link', {name: /Google Calendar/i})).toHaveCount(0);
});

test('switching calendars clears the previous calendar from view', async ({page}) => {
  let active: 'google' | 'native' = 'google';
  await page.route('**/api/auth/me', route => route.fulfill({
    json: {authenticated: true, login_configured: true,
           user: {id: 'alice', name: 'Alice', email: 'alice@example.com'}, ...bothStatus}}));
  await page.route('**/api/calendar/status', route => route.fulfill({
    json: {...bothStatus, active_provider: active,
           active_provider_label: active === 'google' ? 'Google Calendar' : 'Task Pilot calendar'}}));
  await page.route('**/api/calendar/provider*', route => {
    active = new URL(route.request().url()).searchParams.get('provider') as 'google' | 'native';
    return route.fulfill({json: {...bothStatus, active_provider: active,
      active_provider_label: active === 'google' ? 'Google Calendar' : 'Task Pilot calendar'}});
  });
  await page.route('**/api/conversations', route => route.fulfill({json: []}));
  await page.route('**/api/health', route => route.fulfill({json: {status: 'ok', timezone: 'Asia/Kolkata'}}));
  await page.route('**/api/events?*', route => route.fulfill({json: {
    success: true, count: 1, provider: active,
    events: [active === 'google'
      ? {...nativeEvent, event_id: 'g1', title: 'Google team sync',
         html_link: 'https://calendar.google.com/event?eid=g1', provider: 'google'}
      : {...nativeEvent, title: 'Native study block'}],
  }}));

  await page.goto('/');
  await page.getByRole('button', {name: 'Next day', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Google team sync'})).toBeVisible();

  await page.locator('.calendar-switch button', {hasText: 'Task Pilot calendar'}).click();

  await expect(page.getByText('Task Pilot calendar / IST')).toBeVisible();
  // The previous calendar's event must not linger after the switch.
  await expect(page.getByRole('heading', {name: 'Google team sync'})).toHaveCount(0);
});
