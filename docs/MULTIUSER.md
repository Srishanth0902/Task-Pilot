# Multi-user web application

The default FastAPI app requires Google login for `/chat`, `/events` and all
conversation routes. The Desktop OAuth CLI remains separate; its `token.json`
is never used by the web API. The React frontend signs in, signs out, and lets
the user reopen saved conversations from the sidebar.

## Google setup

1. In the existing Google Cloud project, create an OAuth client of type **Web
   application**. Enable Calendar API and configure the consent screen/test users.
2. Register this exact authorized redirect URI for local development:
   `http://127.0.0.1:5173/api/auth/callback`.
3. Download that client's JSON as `credentials.web.json` in the project root.
4. Set `PUBLIC_APP_URL=http://127.0.0.1:5173` in `.env`. Use this same hostname
   in your browser; `localhost` and `127.0.0.1` have different cookies.
5. Restart the backend, open the frontend and choose a Google account. Each
   user selects and grants access to their own Calendar. Google may require consent-screen
   verification before the app can be broadly distributed.

The server uses OAuth state tied to the browser, a one-use state record with a
10-minute expiry, PKCE, and verified Google ID tokens with a nonce and client
audience check. Identity comes from Google's stable subject identifier. Session
cookies are HttpOnly and SameSite=Lax, with Secure enabled for HTTPS. Mutations
require the same-origin frontend header; the API does not enable cross-origin
credentialed requests. Google shows its account chooser on every new login, and
the UI also offers **Switch account** while signed in. Logout revokes only the
current app session; it does not revoke the Google grant, erase saved conversations,
or delete the encrypted refresh token, so returning with the same account is smooth.

## Storage and deployment

`DATA_DIRECTORY` contains SQLite storage and per-conversation file locks. Tokens,
user profiles, and conversation state are encrypted with Fernet. Session secrets
are stored only as SHA-256 hashes. By default, browser sessions expire 30 days
after the most recent request; set `SESSION_MAX_AGE_DAYS` from 1 to 365 to change
that renewable lifetime. No tokens are sent
to browser JavaScript. Refresh tokens are saved after refresh under a short lock
for that account; Google API clients and transports are created per request.

For local development only, an encryption key is generated as `data/master.key`.
Restrict the data directory to the operating-system account running the server;
do not share it or sync it publicly. On HTTPS deployments, `TOKEN_ENCRYPTION_KEY`
must be supplied externally through a secret manager or protected environment.
Use a Fernet key generated with `Fernet.generate_key()` from `cryptography`.
Back up the database and encryption key separately. Losing the key makes the
stored credentials and conversations unreadable. Never commit either one.

SQLite WAL and file locks support multiple workers on **one host sharing local
disk**. Model execution does not hold a database transaction or global chat lock.
Requests for the same conversation serialize, while unrelated conversations can
run concurrently. Multiple hosts/network filesystems require moving storage and
locking to a shared database such as PostgreSQL; that deployment is not implemented.

The full graph state is saved after each completed turn, including pending actions,
selected events, confirmations and messages. A fresh graph restores that state
for the next request. It does not retain process memory between API requests.
Each conversation also receives an encrypted, stable title from its first user
message so the sidebar can show recognizable chats without exposing their text
in plaintext storage.
An interrupted turn remains marked busy on disk and cannot be replayed. The user
must check Calendar and start a new conversation. This avoids duplicate mutations
after a crash; it is not an exactly-once transaction across Google and SQLite.

For Docker, mount the web OAuth file and retain the named data volume. Set
`PUBLIC_APP_URL` to the browser-facing URL (`http://127.0.0.1:8501` locally) and
register its `/api/auth/callback` URI. HTTPS is required outside localhost.
Do not use the older Streamlit/anonymous API client for this authenticated API.

## Verification

`python -m unittest tests.test_multiuser -v` exercises account separation,
anonymous access, cross-origin rejection, encrypted persistence, restart recovery,
confirmation continuation, expired/reused OAuth state, session logout, and locks.
Google OAuth tests use mocked token exchange; live login still requires the Web
application credential setup above and browser consent by each account.

Protocol reference: https://developers.google.com/identity/protocols/oauth2/web-server
