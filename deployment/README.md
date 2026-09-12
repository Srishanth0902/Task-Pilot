# Deployment notes

Task Pilot is packaged as two containers: `api` for FastAPI/LangGraph and
`web` for the React frontend served by Nginx. The supplied Compose file is the reference deployment.
Nginx forwards `/api/` to FastAPI on the internal network; browser code receives no API keys.

1. Create `.env` from `.env.example` and provide `OPENROUTER_API_KEY`.
2. Complete Google OAuth locally so `credentials.json` and `token.json` exist.
3. Run `docker compose up --build`.
4. Open `http://localhost:8501`; health is available at
   `http://localhost:8000/health`.

The secret files are mounted at runtime and excluded from both Git and Docker
build contexts. For a cloud host, store their contents in that platform's
secret manager and mount them at the paths configured in `docker-compose.yml`.
Use persistent encrypted storage for `token.json` because Google may refresh it.

This repository intentionally does not publish personal OAuth credentials or a
public deployment. Public multi-user deployment needs a web OAuth redirect flow
and durable per-user encrypted token storage; the current Desktop OAuth flow is
appropriate for this single-user project deployment.
