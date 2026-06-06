from __future__ import annotations

import asyncio
from datetime import datetime

from models import JobState, JobStatus, Step, StepStatus

_store: dict[str, JobState] = {}
_lock = asyncio.Lock()


async def create_job(job_id: str) -> JobState:
    job = JobState(
        job_id=job_id,
        status=JobStatus.PENDING,
        created_at=datetime.utcnow(),
    )
    async with _lock:
        _store[job_id] = job
    return job


async def get_job(job_id: str) -> JobState | None:
    async with _lock:
        return _store.get(job_id)


async def update_job(job_id: str, **kwargs) -> JobState | None:
    async with _lock:
        job = _store.get(job_id)
        if job is None:
            return None
        updated = job.model_copy(update=kwargs)
        _store[job_id] = updated
        return updated


async def append_log(job_id: str, message: str) -> JobState | None:
    async with _lock:
        job = _store.get(job_id)
        if job is None:
            return None
        updated = job.model_copy(update={"logs": job.logs + [message]})
        _store[job_id] = updated
        return updated


async def update_step(
    job_id: str,
    step_id: int,
    status: StepStatus,
    message: str = "",
) -> JobState | None:
    async with _lock:
        job = _store.get(job_id)
        if job is None:
            return None
        new_steps = [
            Step(id=s.id, name=s.name, status=status, message=message)
            if s.id == step_id
            else s
            for s in job.steps
        ]
        updated = job.model_copy(update={"steps": new_steps})
        _store[job_id] = updated
        return updated
