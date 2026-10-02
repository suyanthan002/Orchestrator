from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentTask:
    task_id: str
    prompt: str
    working_directory: str
    timeout_seconds: int = 120


@dataclass
class AgentResult:
    task_id: str
    agent: str
    success: bool
    exit_code: Optional[int]
    stdout: str
    stderr: str
    command: list[str] = field(default_factory=list)

    final_response: Optional[str] = None
    finish_reason: Optional[str] = None
    iterations: Optional[int] = None
    usage: Optional[dict] = None