# Deployment notes

Task Pilot is packaged as two containers: `api` for FastAPI/LangGraph and
`web` for the React frontend served by Nginx. The supplied Compose file is the reference deployment.
Nginx forwards `/api/` to FastAPI on the internal network; browser code receives no API keys.

1. Create `.env` from `.env.example` and provide `OPENROUTER_API_KEY`.
2. Configure a Web application OAuth client as described in [multi-user setup](../docs/MULTIUSER.md) and save `credentials.web.json`.
3. Run `docker compose up --build`.
4. Set `PUBLIC_APP_URL=http://127.0.0.1:8501` and open that exact URL; health is available at
   `http://localhost:8000/health`.

The secret files are mounted at runtime and excluded from both Git and Docker
build contexts. For a cloud host, store their contents in that platform's
secret manager and mount them at the paths configured in `docker-compose.yml`.
The named data volume persists encrypted user tokens and conversations. Supply
`TOKEN_ENCRYPTION_KEY` externally for HTTPS deployments and back it up separately.

This repository intentionally does not publish personal OAuth credentials or a
public deployment. Web OAuth, encrypted per-user tokens and durable conversations
are implemented for a single host. Multiple workers must share the local data
directory; multiple hosts require a shared database and distributed locks.
