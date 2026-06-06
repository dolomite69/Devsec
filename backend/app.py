from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from core.store import create_build, get_build
from core.schemas import BuildRequest, BuildRecord, BuildStatus
from pipeline.orchestrator import run_build_pipeline, sweep_work_dir

load_dotenv()

app = FastAPI(title="DockerDev")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _cleanup_stale_builds() -> None:
    """Clear leftover clone folders from builds interrupted by a restart."""
    removed = await asyncio.to_thread(sweep_work_dir)
    if removed:
        print(f"[DockerDev] Removed {removed} stale build folder(s) on startup")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {"message": "DockerDev backend is running"}


@app.post("/api/builds")
async def start_build(request: BuildRequest, background_tasks: BackgroundTasks):
    build_id = str(uuid4())
    await create_build(build_id)
    background_tasks.add_task(run_build_pipeline, build_id, request.repo_url)
    return {"build_id": build_id, "status": "created"}


@app.get("/api/builds/{build_id}/stream")
async def stream_build(build_id: str):
    record = await get_build(build_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Build not found")

    async def event_generator():
        while True:
            current = await get_build(build_id)
            if current is None:
                break

            payload = {
                "status": current.status,
                "logs": current.logs,
                "stages": [s.model_dump() for s in current.stages],
            }
            yield {"data": json.dumps(payload)}

            if current.status in (BuildStatus.SUCCESS, BuildStatus.FAILED):
                break

            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())


@app.get("/api/builds/{build_id}/result", response_model=BuildRecord)
async def get_result(build_id: str):
    record = await get_build(build_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Build not found")
    return record
