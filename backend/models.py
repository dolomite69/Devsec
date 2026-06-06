from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class StepStatus(str, Enum):
    WAITING = "WAITING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    ERROR = "ERROR"


class ForgeRequest(BaseModel):
    github_url: str


class Step(BaseModel):
    id: int
    name: str
    status: StepStatus
    message: str = ""


def _default_steps() -> list[Step]:
    names = [
        "Validate URL",
        "Clone Repository",
        "Analyze Codebase",
        "Generate Dockerfile",
        "Build Docker Image",
        "Run Container",
        "Done",
    ]
    return [Step(id=i + 1, name=name, status=StepStatus.WAITING) for i, name in enumerate(names)]


class JobState(BaseModel):
    job_id: str
    status: JobStatus
    steps: list[Step] = Field(default_factory=_default_steps)
    logs: list[str] = Field(default_factory=list)
    dockerfile: str = ""
    error: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
