from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class BuildStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class StageStatus(str, Enum):
    WAITING = "WAITING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    ERROR = "ERROR"


class BuildRequest(BaseModel):
    repo_url: str


class Stage(BaseModel):
    id: int
    name: str
    status: StageStatus
    message: str = ""


def _default_stages() -> list[Stage]:
    names = [
        "Validate URL",
        "Clone Repository",
        "Analyze Codebase",
        "Generate Dockerfile",
        "Build Docker Image",
        "Run Container",
        "Done",
    ]
    return [Stage(id=i + 1, name=name, status=StageStatus.WAITING) for i, name in enumerate(names)]


class BuildRecord(BaseModel):
    build_id: str
    status: BuildStatus
    stages: list[Stage] = Field(default_factory=_default_stages)
    logs: list[str] = Field(default_factory=list)
    dockerfile: str = ""
    error: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
