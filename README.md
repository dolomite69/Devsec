# DockerDev

Paste a GitHub URL → get a working, build-tested Dockerfile.

DockerDev clones a public repo, detects its stack with an LLM, generates a Dockerfile, then builds and runs it to confirm it works — retrying up to 3 times on failure.

## Stack

- **Frontend:** React 19 + Vite — bright "Daybreak" UI with live SSE logs
- **Backend:** FastAPI (Python 3.11)
- **LLM:** Groq (LLaMA 3.3 70B via OpenAI-compatible API)
- **Infra:** Docker, Docker Compose, nginx

## Features

- **Self-healing builds** — build/run errors are fed back to the LLM and the Dockerfile is regenerated (up to 3 attempts).
- **Monorepo aware** — detects multiple `package.json` files, identifies the backend/server service (express/fastify/etc.), and containerizes only that folder.
- **Smart start command** — uses `npm start` only when a `start` script exists, otherwise runs the entry file directly (`node server.js`).
- **Clean build context** — committed `node_modules` / `__pycache__` / virtualenvs are excluded (recursively, so nested ones in monorepos don't leak host-compiled native binaries into the image).
- **Live streaming** — stage timeline and build logs stream to the browser over Server-Sent Events.

## Pipeline

`Validate URL → Clone → Analyze → Generate Dockerfile → Build → Run → Return`

On build/run failure the error is fed back to the LLM and regenerated (max 3 attempts).

## Prerequisites

- Python 3.11+, Node.js 18+
- Docker (daemon accessible via `/var/run/docker.sock`)
- A free [Groq API key](https://console.groq.com/keys)

## Environment

Create `backend/.env`:

```env
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile
```

## Run locally

Backend:

```bash
cd backend
pip install -r requirements.txt
uvicorn app:app --port 8000 --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Run with Docker Compose

```bash
docker compose up --build
```

> The backend reads `GROQ_API_KEY` from `backend/.env` (passed through via `env_file` in `docker-compose.yml`).

- Frontend → http://localhost:3000
- Backend → http://localhost:8000

## API

- `POST /api/builds` — start a build, returns `build_id`
- `GET /api/builds/{build_id}/stream` — SSE stage/log updates
- `GET /api/builds/{build_id}/result` — final Dockerfile + logs

## Limitations

- Public repos only
- Apps needing external DBs/services may fail the run check (DockerDev detects common "app started but missing external service" cases and treats them as success)
- Containers are stopped after a short startup check
- No build cache between builds
