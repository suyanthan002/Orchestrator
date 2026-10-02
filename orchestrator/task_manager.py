from pathlib import Path
from uuid import uuid4

from .task import AgentTask


class TaskManager:

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def create_task(
        self,
        prompt: str,
        working_directory: str | None = None,
        timeout_seconds: int = 120,
        task_type: str = "general",
        metadata: dict | None = None,
    ) -> AgentTask:

        task_id = f"task-{uuid4().hex[:8]}"

        if working_directory is None:
            working_directory = str(self.root)

        return AgentTask(
            task_id=task_id,
            prompt=prompt,
            working_directory=working_directory,
            timeout_seconds=timeout_seconds,
            task_type=task_type,
            metadata=metadata,
        )