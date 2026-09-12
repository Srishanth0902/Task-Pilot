# Task Pilot frontend

The frontend follows the user-supplied Stitch screenshot with responsive layouts,
an agenda, a seven-day list, event details, an assistant and confirmation flows.

## Stack decisions

- React + TypeScript: typed API results and local conversation/form state.
- Vite: a small client application with a development proxy and static build.
- TanStack Query: loading, failure, caching and refresh after calendar requests.
- Custom CSS: reproduces the reference without a large component framework.
- Lucide: consistent navigation icons. Google Fonts provides DM Sans/Newsreader,
  with system/Georgia fallbacks when unavailable.
- FastAPI remains the sole backend; OpenRouter and Google secrets stay there.

Run `npm ci && npm run dev` with FastAPI on port 8000. Open port 5173.
The Docker build serves static assets on port 8501 and proxies `/api/` to FastAPI.

`npm run build` checks TypeScript and produces `dist/`. `npm test` verifies IST
formatting and safe links. `npx playwright test` tests the browser with synthetic
API responses. Install its browser first with `npx playwright install chromium`.
On Windows, an existing Edge installation can be used with
`$env:PLAYWRIGHT_CHANNEL='msedge'`.

Chat history and pending proposals live in the current page session. Reloading
starts a new conversation. Slot selection opens a review form; only submitting
the form requests creation. Mutating requests are never automatically retried.
The backend resolves natural-language dates relative to today, not the selected
agenda day; date-specific form requests include an explicit ISO date and IST time.
