from orchestrator.agent import AgentTask
from orchestrator.runner import AgentRunner


class ClineAdapter:

    name = "cline"

    def __init__(self, runner: AgentRunner):
        self.runner = runner

    def execute(self, task: AgentTask):

        command = [
            "cline.cmd",
            "--json",
            "--plan",
            "--auto-approve",
            "false",
        ]

        return self.runner.run(
            agent_name=self.name,
            command=command,
            task=task,
        )