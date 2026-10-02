from dataclasses import dataclass, field
from typing import Optional
from .task import AgentTask


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