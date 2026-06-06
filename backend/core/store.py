from __future__ import annotations

import asyncio
from datetime import datetime

from core.schemas import BuildRecord, BuildStatus, Stage, StageStatus

_store: dict[str, BuildRecord] = {}
_lock = asyncio.Lock()


async def create_build(build_id: str) -> BuildRecord:
    record = BuildRecord(
        build_id=build_id,
        status=BuildStatus.PENDING,
        created_at=datetime.utcnow(),
    )
    async with _lock:
        _store[build_id] = record
    return record


async def get_build(build_id: str) -> BuildRecord | None:
    async with _lock:
        return _store.get(build_id)


async def update_build(build_id: str, **kwargs) -> BuildRecord | None:
    async with _lock:
        record = _store.get(build_id)
        if record is None:
            return None
        updated = record.model_copy(update=kwargs)
        _store[build_id] = updated
        return updated


async def append_log(build_id: str, message: str) -> BuildRecord | None:
    async with _lock:
        record = _store.get(build_id)
        if record is None:
            return None
        updated = record.model_copy(update={"logs": record.logs + [message]})
        _store[build_id] = updated
        return updated


async def update_stage(
    build_id: str,
    stage_id: int,
    status: StageStatus,
    message: str = "",
) -> BuildRecord | None:
    async with _lock:
        record = _store.get(build_id)
        if record is None:
            return None
        new_stages = [
            Stage(id=s.id, name=s.name, status=status, message=message)
            if s.id == stage_id
            else s
            for s in record.stages
        ]
        updated = record.model_copy(update={"stages": new_stages})
        _store[build_id] = updated
        return updated
