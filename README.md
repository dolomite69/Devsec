# DockerForge

> Paste a GitHub URL, get a production-ready Dockerfile — automatically generated, built, and validated.

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Groq](https://img.shields.io/badge/Groq-LLaMA%203.3-F55036?logo=groq&logoColor=white)

---

## Demo

<!-- Replace with your actual GIF -->
![DockerForge Demo](https://via.placeholder.com/800x450.png?text=Demo+GIF+Coming+Soon)

---

## Architecture Overview

DockerForge follows a **request → agent pipeline → validated output** architecture. The frontend submits a GitHub URL via `POST /api/forge`, which creates a background job and returns a `job_id`. The client then opens an SSE connection to `/api/forge/{job_id}/stream` to receive real-time step updates and build logs.

On the backend, the forge agent orchestrates a linear pipeline: it validates the URL, clones the repository with GitPython, scans the file tree and key configuration files (e.g., `package.json`, `requirements.txt`, `go.mod`) to detect the project's language and structure, then sends this context to Groq's LLM to generate a Dockerfile. The generated Dockerfile is written into the cloned repo and validated by running `docker build` and `docker run` against the host's Docker daemon via the mounted socket. If either step fails, the build/run error is fed back to the LLM as context and the Dockerfile is regenerated — up to 3 attempts. On success, the validated Dockerfile is returned to the client. On final failure, the job is marked failed with full error logs. All temporary artifacts (cloned repos, built images) are cleaned up in a `finally` block regardless of outcome.

In production, nginx serves the React SPA and reverse-proxies `/api/` requests to the FastAPI backend over a shared Docker Compose bridge network.

```
┌──────────────────────────────────────────────────────────────┐
│                        Frontend (React)                      │
│                     Vite · Port 3000 (nginx)                 │
│                                                              │
│  ┌────────────┐ ┌───────────────┐ ┌──────────┐ ┌──────────┐ │
│  │ InputForm  │ │ StepTimeline  │ │ LogViewer│ │Dockerfile│ │
│  │            │ │               │ │          │ │  Output  │ │
│  └─────┬──────┘ └───────┬───────┘ └────┬─────┘ └────┬─────┘ │
│        │ POST /api/forge│ SSE stream   │            │        │
└────────┼────────────────┼──────────────┼────────────┼────────┘
         │                │              │            │
    ─────┼────── nginx reverse proxy (/api/) ────────┼─────
         │                │              │            │
┌────────┼────────────────┼──────────────┼────────────┼────────┐
│        ▼                ▼              ▼            ▼        │
│                    Backend (FastAPI)                          │
│                      Port 8000                               │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │                  Forge Agent Pipeline                 │    │
│  │                                                      │    │
│  │  Validate URL → Clone Repo → Analyze Codebase        │    │
│  │       → Generate Dockerfile (Groq LLM)               │    │
│  │       → Build Image → Run Container                  │    │
│  │       → Retry on failure (up to 3 attempts)          │    │
│  └──────────────────────────────────────────────────────┘    │
│                         │                                    │
│                    Docker Socket                             │
│                   /var/run/docker.sock                        │
└──────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer      | Technology            | Purpose                                |
| ---------- | --------------------- | -------------------------------------- |
| Frontend   | React 19, Vite 8      | SPA with real-time SSE streaming       |
| Backend    | Python 3.11, FastAPI  | REST API + agentic pipeline            |
| LLM        | Groq (LLaMA 3.3 70B) | Dockerfile generation                  |
| Container  | Docker, Docker Compose| Build, run, and orchestrate services   |
| Proxy      | nginx                 | Serve SPA, reverse-proxy API calls     |
| VCS        | GitPython             | Clone repositories for analysis        |

---

## Setup & Run

### Prerequisites

- Python 3.11+
- Node.js 18+
- Docker (with Docker CLI accessible)
- A [Groq API key](https://console.groq.com/)

### Environment Variables

Create a `.env` file in the project root (or `backend/` directory):

```env
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile
```

### Local Development

**Backend:**

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend:**

```bash
cd frontend
npm install
npm run dev
```

The frontend dev server proxies `/api/` requests to `http://localhost:8000` automatically.

### Docker Compose (Production)

```bash
# Set your API key
export GROQ_API_KEY=your_groq_api_key_here   # Linux/macOS
$env:GROQ_API_KEY="your_groq_api_key_here"   # PowerShell

# Build and start
docker compose up --build
```

| Service  | URL                    |
| -------- | ---------------------- |
| Frontend | http://localhost:3000   |
| Backend  | http://localhost:8000   |

---

## How It Works

DockerForge runs a **7-step agentic pipeline** for every request:

| Step | Action                | Description                                                        |
| ---- | --------------------- | ------------------------------------------------------------------ |
| 1    | **Validate URL**      | Checks the input is a valid `https://github.com/` URL              |
| 2    | **Clone Repository**  | Clones the repo into a temporary working directory                 |
| 3    | **Analyze Codebase**  | Detects language, scans file tree, reads key config files           |
| 4    | **Generate Dockerfile** | Sends codebase context to Groq LLM to produce a Dockerfile       |
| 5    | **Build Docker Image** | Runs `docker build` with the generated Dockerfile                 |
| 6    | **Run Container**     | Starts the container to verify it boots correctly                  |
| 7    | **Display Dockerfile** | Returns the validated Dockerfile to the user                      |

If the build or run fails, DockerForge feeds the error back to the LLM and **retries up to 3 times** with a corrected Dockerfile.

---

## LLM Provider — Why Groq?

DockerForge uses **[Groq](https://groq.com/)** running **LLaMA 3.3 70B** (`llama-3.3-70b-versatile`, configurable via `GROQ_MODEL`).

Groq was selected specifically because DockerForge's retry-based agent workflow demands **low-latency inference at every iteration**. When a generated Dockerfile fails to build, the error log is fed back to the LLM and a corrected Dockerfile must be produced quickly — up to 3 times per job. With Groq's LPU-accelerated inference, each regeneration completes in seconds rather than tens of seconds, keeping the total pipeline time practical even in worst-case retry scenarios.

| Selection Criteria          | How Groq Fits                                                                  |
| --------------------------- | ------------------------------------------------------------------------------ |
| **Low latency**             | Sub-second token generation; critical for keeping retry loops under 60 s total |
| **Fast iteration cycles**   | Regenerating a Dockerfile on build failure adds minimal overhead               |
| **Structured output**       | Strong performance generating raw Dockerfile content (no markdown wrapping)    |
| **OpenAI-compatible API**   | Drop-in integration via standard chat completions endpoint; no custom SDK      |
| **Deterministic generation**| Temperature `0.2` produces consistent, reproducible Dockerfiles                |

The LLM receives the detected language, full file tree, and contents of key config files as context, and is instructed to return only raw Dockerfile content — no explanations, no fenced code blocks. This keeps parsing trivial and the agent loop simple.

---

## Known Limitations

- **Public repositories only** — private repos require authentication tokens, which are not yet supported
- **Complex and monorepo projects** — repositories with multiple services, workspaces, or non-standard build systems may produce incomplete Dockerfiles since the scanner provides a single-root context to the LLM
- **Database-dependent services** — applications that require a running database, Redis, or other external services at startup will fail the container run check, as only the application container is started in isolation
- **GPU and system-level dependencies** — projects requiring CUDA, native system libraries, or hardware-specific drivers may not be correctly handled by the generated Dockerfile
- **Host Docker availability** — the backend requires a working Docker daemon accessible via `/var/run/docker.sock`; on Windows this requires Docker Desktop with WSL 2 backend, and headless Linux servers need Docker Engine installed
- **15-second container timeout** — containers that take longer than 15 seconds to start are assumed successful and stopped; long-initializing applications may pass validation without actually being ready
- **No build caching** — each job clones fresh and builds from scratch; there is no persistent layer cache between jobs
- **Single-stage output** — the LLM may not always produce optimized multi-stage builds, resulting in larger images than hand-tuned Dockerfiles

---

## Video Demo

<!-- Replace with your actual video link -->
🎥 *Video walkthrough coming soon — will be linked here.*

---

## Future Improvements

- Support for private repositories via GitHub token authentication
- Multi-stage Dockerfile optimization prompts
- Dockerfile history and diff comparison between retry attempts
- Support for additional LLM providers (OpenAI, Anthropic)
- Persistent job storage with a database
- GitHub App integration for one-click Dockerization
- Custom base image preferences per language
- Deploy generated images directly to a container registry
