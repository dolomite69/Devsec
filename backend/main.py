from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from agent.retry_loop import run_forge_agent
from job_store import create_job, get_job
from models import ForgeRequest, JobState, JobStatus

load_dotenv()

app = FastAPI(title="DockerForge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {"message": "DockerForge backend is running"}


@app.post("/api/forge")
async def start_forge(request: ForgeRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid4())
    await create_job(job_id)
    background_tasks.add_task(run_forge_agent, job_id, request.github_url)
    return {"job_id": job_id, "status": "created"}


@app.get("/api/forge/{job_id}/stream")
async def stream_job(job_id: str):
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        while True:
            current = await get_job(job_id)
            if current is None:
                break

            payload = {
                "status": current.status,
                "logs": current.logs,
                "steps": [s.model_dump() for s in current.steps],
            }
            yield {"data": json.dumps(payload)}

            if current.status in (JobStatus.SUCCESS, JobStatus.FAILED):
                break

            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())


@app.get("/api/forge/{job_id}/result", response_model=JobState)
async def get_result(job_id: str):
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
