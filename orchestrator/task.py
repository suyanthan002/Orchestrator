from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentTask:
    task_id: str
    prompt: str
    working_directory: str
    timeout_seconds: int = 120
    task_type: str = "general"
    metadata: Optional[dict] = None