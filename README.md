# DockerDev

Paste a GitHub URL → get a working, build-tested Dockerfile.

DockerDev clones a public repo, detects its stack with an LLM, generates a Dockerfile, then builds and runs it to confirm it works — retrying up to 3 times on failure.

## Stack

- **Frontend:** React 19 + Vite (SSE live logs)
- **Backend:** FastAPI (Python 3.11)
- **LLM:** Groq (LLaMA 3.3 70B via OpenAI-compatible API)
- **Infra:** Docker, Docker Compose, nginx

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
- Single-root projects (monorepos may be incomplete)
- Apps needing external DBs/services fail the run check
- Containers are stopped after a 15s startup timeout
- No build cache between builds
